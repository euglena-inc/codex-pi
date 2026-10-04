#!/usr/bin/env python3
"""Deterministic validation for the Codex-Pi explicit proxy and transport diagnostics.

It validates, without any external network or paid provider:

* the project-config validation matrix (accepted and rejected shapes, no value echo);
* the exact child environment builder (explicit overrides, preserved NODE_OPTIONS,
  NO_PROXY semantics and the percent-encoded preload import);
* the actual preload module under ``node:diagnostics_channel`` fault injection:
  classification, privacy redaction, abort-cleanup suppression, bounded output and
  harmless write failures;
* the frozen-policy integration tests through the public start/continue/status
  entrypoints with the fake Pi double (``tests/test_connection_support.py``).

Missing Node fails nonzero; it never skips and reports success. A zero exit only
proves execution finished, not acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
PROBE = ROOT / "scripts" / "connection_support_probe.mjs"

# The validator already depends on the offline test suite for its integration
# mode; reuse the same boundary helper so an inherited NODE_OPTIONS/preload or
# diagnostic identity can never masquerade as this run's intended observer.
sys.path.insert(0, str(ROOT / "tests"))
from runtime_helpers import isolated_env  # noqa: E402

MAX_LINES = 257
ALLOWED_RECORD_KEYS = {"at", "dur", "phase", "class", "code", "status", "hdr", "scope",
                       "proc", "primary"}
FORBIDDEN_SUBSTRINGS = ("alice", "s3cr3t", "private.invalid", "ZZTOKENZZ", "Proxy response",
                        "socket hang up", "getaddrinfo")
EXPECTED_CLASSES = {"connection_reset", "abort_cleanup", "proxy_connect_failure", "dns_failure",
                    "timeout", "connection_refused", "tls_failure", "post_header_error",
                    "transport_error", "unknown"}


class ValidationError(RuntimeError):
    """A deterministic expectation failed; the probe must exit nonzero."""


def load_core():
    sys.path.insert(0, str(RUNTIME))
    import pi_core  # noqa: E402
    return pi_core


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def config_matrix(core) -> dict:
    accepted = core.parse_network_policy({"proxyUrl": "HTTP://Proxy.Invalid:3128/",
                                          "diagnostics": True})
    check(accepted == {"proxyUrl": "http://proxy.invalid:3128", "diagnostics": True},
          f"canonical proxy mismatch: {accepted}")
    default = core.parse_network_policy(None)
    check(default == {"proxyUrl": None, "diagnostics": False}, f"default mismatch: {default}")
    secret = "".join(["http://", "alice", ":", "s3cr3t", "@", "private.invalid", ":3128",
                      "/tunnel?token=ZZTOKENZZ#frag"])
    rejected = 0
    for index, raw in enumerate((
            {"proxyUrl": secret},
            {"proxyUrl": "http://proxy.invalid"},
            {"proxyUrl": "socks5://proxy.invalid:3128"},
            {"proxyUrl": "http://proxy.invalid:3128/path"},
            {"proxyUrl": "http://proxy.invalid:3128?x=1"},
            {"proxyUrl": "http://proxy.invalid:3128#frag"},
            {"proxyUrl": "http://:3128"},
            {"proxyUrl": "http://proxy.invalid:99999"},
            {"proxyUrl": "http://proxy.invalid:abc"},
            {"proxyUrl": True},
            {"diagnostics": "yes"},
            {"proxyUrl": None, "extra": 1},
    )):
        try:
            core.parse_network_policy(raw)
        except ValueError as exc:
            rejected += 1
            text = str(exc)
            for part in ("alice", "s3cr3t", "private.invalid", "ZZTOKENZZ"):
                check(part not in text, f"case {index} echoed a rejected value component")
        else:
            raise ValidationError(f"invalid network policy case {index} was accepted")
    check(rejected == 12, f"expected 12 rejected config shapes, got {rejected}")
    return {"accepted": 2, "rejected": rejected}


def read_sidecar(path: Path) -> list:
    check(path.is_file(), f"diagnostics sidecar was not created: {path.name}")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def probe_env(core, sidecar: Path, scope: str) -> dict:
    env, _record = core.apply_network_policy(
        isolated_env(), {"proxyUrl": None, "diagnostics": True, "source": "frozen"},
        diagnostics_file=sidecar, scope=scope, supervisor_pid=os.getpid(),
        preload_path=RUNTIME / "pi_network_diagnostics.mjs")
    # Local fault servers must never be routed through an inherited proxy.
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    return env


def run_probe(node: str, env: dict, mode: str) -> dict:
    proc = subprocess.run([node, str(PROBE), mode], env=env, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120)
    check(proc.returncode == 0,
          f"probe mode {mode} exited {proc.returncode}: {(proc.stderr or proc.stdout)[-300:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def probe_checks(core, node: str, tmp: Path) -> dict:
    synthetic_dir = tmp / "synthetic"
    synthetic_dir.mkdir()
    sidecar = synthetic_dir / "round.network.jsonl"
    run_probe(node, probe_env(core, sidecar, "synthetic"), "synthetic")
    records = read_sidecar(sidecar)
    check(records and records[0].get("phase") == "observer_ready",
          "observer_ready marker missing")
    for record in records:
        unknown = set(record) - ALLOWED_RECORD_KEYS
        check(not unknown, f"sidecar record has non-allowlisted keys: {sorted(unknown)}")
        for value in (record.get("code"), record.get("class")):
            if isinstance(value, str):
                check(not any(part in value for part in FORBIDDEN_SUBSTRINGS),
                      "sidecar leaked a raw error/value fragment")
    kinds = {record.get("class") for record in records if record.get("class")}
    check(kinds == EXPECTED_CLASSES, f"synthetic classes mismatch: {sorted(kinds)}")
    check(any(record.get("status") == 503 for record in records),
          "proxy CONNECT 503 status was not preserved")
    check(all(record.get("class") != "abort_cleanup" or record.get("status") is None
              for record in records), "abort cleanup must not carry a failure status")
    raw = sidecar.read_text(encoding="utf-8")
    for part in ("alice", "s3cr3t", "private.invalid", "ZZTOKENZZ", "Proxy response",
                 "socket hang up"):
        check(part not in raw, f"sidecar leaked raw text: {part}")

    flood_dir = tmp / "flood"
    flood_dir.mkdir()
    flood_sidecar = flood_dir / "round.network.jsonl"
    run_probe(node, probe_env(core, flood_sidecar, "flood"), "flood")
    flood_records = read_sidecar(flood_sidecar)
    check(len(flood_records) <= MAX_LINES, f"flood file has {len(flood_records)} lines")
    check(any(record.get("phase") == "truncated" for record in flood_records),
          "flood file lacks a truncation marker")
    check(flood_sidecar.stat().st_size <= 65536, "flood file exceeded its byte bound")

    real_dir = tmp / "real"
    real_dir.mkdir()
    real_sidecar = real_dir / "round.network.jsonl"
    observation = run_probe(node, probe_env(core, real_sidecar, "real"), "real")
    check(observation.get("reset") is True, "real probe did not observe the local reset")
    check(observation.get("healthyOk") is True, "real probe local success request failed")
    real_records = read_sidecar(real_sidecar)
    real_failures = [record for record in real_records
                     if record.get("class") not in (None, "abort_cleanup")]
    check(len(real_failures) == 1,
          f"real reset must produce exactly one failure record: {real_failures}")
    check(real_failures[0].get("class") in ("connection_reset", "transport_error")
          and real_failures[0].get("hdr") is False,
          f"real reset was not classified as a pre-header transport failure: {real_failures}")

    failure_dir = tmp / "write-failure"
    failure_dir.mkdir()
    run_probe(node, probe_env(core, failure_dir, "write-failure"), "write-failure")
    # Pointing at a directory makes the append fail; the process must still exit 0.
    bad_env = probe_env(core, failure_dir, "write-failure")
    bad_env["CODEX_PI_NETWORK_DIAG_FILE"] = str(failure_dir)
    run_probe(node, bad_env, "write-failure")

    # One primary writer per round; inherited children must not append.
    cross_dir = tmp / "cross"
    cross_dir.mkdir()
    cross_sidecar = cross_dir / "round.network.jsonl"
    run_probe(node, probe_env(core, cross_sidecar, "cross"), "flood")
    check(cross_sidecar.stat().st_size <= 65536,
          f"cross-process sidecar exceeded its bound: {cross_sidecar.stat().st_size}")
    cross_records = read_sidecar(cross_sidecar)
    check(any(record.get("phase") == "truncated" for record in cross_records)
          or core.read_network_sidecar(cross_sidecar)["truncated"],
          "cross-process dropped events lack a truncation signal")
    before_size = cross_sidecar.stat().st_size
    child_env = probe_env(core, cross_sidecar, "cross")
    child_env["CODEX_PI_NETWORK_DIAG_SUPERVISOR"] = "1"
    children = [subprocess.Popen([node, str(PROBE), "flood"], env=child_env, cwd=str(ROOT),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(4)]
    for child in children:
        _out, err = child.communicate(timeout=120)
        check(child.returncode == 0, f"inherited flood failed: {err[-200:]}")
    check(cross_sidecar.stat().st_size == before_size,
          "inherited children appended to the primary sidecar")
    return {"syntheticRecords": len(records), "floodRecords": len(flood_records),
            "realRecords": len(real_records), "crossRecords": len(cross_records),
            "crossBytes": cross_sidecar.stat().st_size}


def integration() -> dict:
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                           "-p", "test_connection_support.py", "-v"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    tail = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ValidationError("integration tests failed:\n" + tail[-2000:])
    return {"exit": proc.returncode, "tests": tail.strip().splitlines()[-1][:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--no-integration", action="store_true",
                        help="skip the fake-Pi integration module")
    args = parser.parse_args()
    node = shutil.which(args.node)
    if not node:
        print(f"validate_connection_support: Node executable {args.node!r} is not on PATH")
        return 2
    core = load_core()
    summary = {"ok": True, "node": node, "config": config_matrix(core)}
    with tempfile.TemporaryDirectory(prefix="codex-pi-network-") as directory:
        try:
            summary["probe"] = probe_checks(core, node, Path(directory))
            if not args.no_integration:
                summary["integration"] = integration()
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            print(f"validate_connection_support: {exc}")
            return 1
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
