#!/usr/bin/env python3
"""Old/new log-analysis benchmark on synthetic 10 MiB and 200 MiB logs.

The baseline helpers are materialized read-only from ``--baseline-ref`` into a
temporary directory with ``git archive``; the current source, history and index
are never touched. The candidate helpers are a temporary copy of ``runtime/``.
Each implementation runs helper then terminal summary in its own sequential
child sequence, so the measured peak RSS covers the check-log hash/tail path and
the summary receipt-verification path together and never accumulates peaks.
The wall clock includes the identical ``cat`` log capture for both. Hash, test counts and the bounded log tail are compared with independent
expected facts and with each other.

Peak RSS is reported in bytes (converted from KiB on Linux) and the process is
never gated on a speedup claim. The candidate's 200 MiB peak may grow at most
32 MiB over its 10 MiB peak and must stay at least 32 MiB below the old
implementation's 200 MiB peak. Raw measurements stay in a private evidence
directory; no network, model or business text is used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
DEFAULT_SIZES_MIB = (10, 200)
GROWTH_LIMIT_BYTES = 32 * 1024 * 1024
SIGNIFICANT_SAVING_BYTES = 32 * 1024 * 1024
MEASURE_TIMEOUT_SECONDS = 900.0

MEASURE_SCRIPT = r'''
import json, os, resource, subprocess, sys, time
helper, log_path, round_dir, cwd, summary_cli = sys.argv[1:6]
checks = os.path.join(round_dir, "round.checks")
os.makedirs(checks, exist_ok=True)
round_log = os.path.join(round_dir, "round.jsonl")
open(round_log, "ab").close()
helper_started = time.monotonic()
proc = subprocess.run(
    [sys.executable, helper, "--output-dir", checks, "--id", "bench",
     "--timeout-seconds", "600", "--", "sh", "-c", "cat '%s'; exit 1" % log_path.replace("'", "'\\''")],
    cwd=cwd, capture_output=True, text=True)
helper_wall = time.monotonic() - helper_started
summary_started = time.monotonic()
summary = subprocess.run(
    [sys.executable, summary_cli, round_log, "--worktree", cwd, "--run-dir", round_dir,
     "--checks-dir", checks, "--json"],
    cwd=cwd, capture_output=True, text=True)
summary_wall = time.monotonic() - summary_started
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
unit = "bytes" if sys.platform == "darwin" else "kib"
peak = usage.ru_maxrss if unit == "bytes" else usage.ru_maxrss * 1024
print(json.dumps({"wallSeconds": helper_wall + summary_wall, "helperWall": helper_wall,
                  "summaryWall": summary_wall, "helperExit": proc.returncode,
                  "helperStdout": proc.stdout[-20000:], "helperStderr": proc.stderr[-2000:],
                  "summaryExit": summary.returncode, "summaryStdout": summary.stdout[-200000:],
                  "summaryStderr": summary.stderr[-2000:],
                  "peakRssBytes": peak, "rssUnit": unit}, separators=(",", ":")))
'''


def generate_log(path: Path, size_bytes: int) -> dict:
    """Write a deterministic synthetic log in chunks; return expected facts."""
    tail_line = b"--- SKIP: SyntheticTail\n"
    block_for = lambda index: (f"=== RUN TestCase{index:08d}\n"
                               f"e\u4e2d\U0001f642 payload {index:08d}\n"
                               f"--- PASS: TestCase{index:08d}\n").encode("utf-8")
    digest = hashlib.sha256()
    written = cases = 0
    content_budget = size_bytes - len(tail_line)
    if content_budget < 1024:
        raise ValueError("synthetic size is too small; use at least 1 KiB")
    with path.open("wb") as out:
        while True:
            block = block_for(cases)
            if written + len(block) > content_budget:
                break
            out.write(block)
            digest.update(block)
            written += len(block)
            cases += 1
        remaining = content_budget - written
        if remaining:
            padding = b"#" * (remaining - 1) + b"\n"
            out.write(padding)
            digest.update(padding)
        out.write(tail_line)
        digest.update(tail_line)
    return {"bytes": size_bytes, "sha256": digest.hexdigest(),
            "counts": {"run": cases, "pass": cases, "fail": 0, "skip": 1,
                       "format": "go_verbose_top_level"},
            "tailLastLine": "--- SKIP: SyntheticTail"}


def materialize(commit: str, destination: Path) -> Path:
    """git archive baseline runtime/ read-only into ``destination/runtime``."""
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "baseline.tar"
    subprocess.run(["git", "-C", str(ROOT), "archive", "-o", str(archive), commit, "runtime"],
                   check=True, capture_output=True, text=True)
    with tarfile.open(archive) as bundle:
        try:
            bundle.extractall(destination, filter="data")
        except TypeError:  # Python < 3.12 has no extraction filter
            bundle.extractall(destination)
    archive.unlink()
    return destination / "runtime"


def copy_candidate(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(RUNTIME, destination / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return destination / "runtime"


def measure(runtime_dir: Path, log_path: Path, work: Path, tag: str) -> dict:
    """Run helper then summary in one measured child sequence and verify both."""
    round_dir = work / f"round-{tag}"
    (round_dir / "round.checks").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([sys.executable, "-c", MEASURE_SCRIPT,
                           str(runtime_dir / "pi_check.py"), str(log_path), str(round_dir),
                           str(work), str(runtime_dir / "pi_summary.py")],
                          capture_output=True, text=True, timeout=MEASURE_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise RuntimeError(f"measurement {tag} failed: {proc.stderr[-500:]}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    try:
        helper = json.loads(result["helperStdout"].strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"measurement {tag} produced no helper JSON: {exc}") from exc
    receipt = json.loads(Path(helper["receipt"]).read_text(encoding="utf-8"))
    try:
        summary = json.loads(result["summaryStdout"].strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"measurement {tag} produced no summary JSON: {exc}") from exc
    verified = any(isinstance(item, dict) and item.get("id") == "bench"
                   and item.get("log_verified") is True
                   for item in (summary.get("check_receipts") or []))
    result.update({"receipt": helper["receipt"], "logTail": helper.get("log_tail"),
                   "logSha256": receipt["log_sha256"], "testCounts": receipt["test_counts"],
                   "summaryVerified": verified})
    return result


def _timing_fixture(runtime_dir: Path, work: Path) -> dict:
    """Known-correct totals for the session-derived timing fixture."""
    fixture = work / "fixture"
    round_dir = fixture / "rounds" / "1"
    checks_dir = round_dir / "round.checks"
    session_dir = fixture / "session"
    checks_dir.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    messages = [
        {"role": "assistant", "provider": "p", "model": "m", "timestamp": 1000,
         "stopReason": "toolUse",
         "usage": {"input": 10, "cacheRead": 90, "cacheWrite": 5, "output": 20,
                   "reasoning": 7, "totalTokens": 125, "cost": {"total": 0.1}},
         "content": [{"type": "toolCall", "id": "c1", "name": "bash"}]},
        {"role": "assistant", "provider": "p", "model": "m", "timestamp": 2000,
         "stopReason": "stop",
         "usage": {"input": 3, "cacheRead": 7, "cacheWrite": 0, "output": 5,
                   "reasoning": 2, "totalTokens": 15, "cost": {"total": 0.2}},
         "content": [{"type": "text", "text": "done"}]},
    ]
    events = [{"type": "agent_end", "messages": messages},
              {"type": "turn_end", "message": messages[0], "toolResults": []},
              {"type": "message_end", "message": messages[0]},
              {"type": "message_end", "message": messages[1]}]
    (round_dir / "round.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    (checks_dir / "r1.json").write_text(json.dumps({
        "id": "r1", "argv": ["x"], "head": None, "dirty": False, "exit_code": 0,
        "timed_out": False, "started_at": 100.0, "ended_at": 102.5,
        "test_counts": {"run": 1, "pass": 1, "fail": 0, "skip": 0},
        "log": "r1.log", "log_sha256": "x"}), encoding="utf-8")
    (checks_dir / "r1.log").write_text("ok\n", encoding="utf-8")
    entries = [
        {"type": "session", "version": 3, "id": "sid",
         "timestamp": "1970-01-01T00:00:00.000Z", "cwd": "/tmp"},
        {"type": "message", "id": "a1", "parentId": None,
         "timestamp": "1970-01-01T00:00:10.000Z",
         "message": {"role": "assistant", "timestamp": 8000, "provider": "p", "model": "m",
                     "usage": {}, "content": [{"type": "toolCall", "id": "c1", "name": "bash"}]}},
        {"type": "message", "id": "t1", "parentId": "a1",
         "timestamp": "1970-01-01T00:00:11.100Z",
         "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "bash",
                     "timestamp": 11000, "content": [], "isError": False}},
        {"type": "message", "id": "a2", "parentId": "t1",
         "timestamp": "1970-01-01T00:00:13.000Z",
         "message": {"role": "assistant", "timestamp": 11500, "provider": "p", "model": "m",
                     "usage": {}, "content": []}},
    ]
    (session_dir / "2026-01-01T00-00-00-000Z_sid.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("bench_pi_summary",
                                                  runtime_dir / "pi_summary.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(runtime_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(runtime_dir))
    data = module.summarize(round_dir / "round.jsonl", fixture, round_dir, "p/m",
                            checks_dir=checks_dir, session_dir=session_dir,
                            session_id="sid", window=(5.0, 20.0))
    metrics = data["metrics"]
    observed = {
        "uncachedInput": metrics["usage"]["uncachedInput"]["value"],
        "cacheRead": metrics["usage"]["cacheRead"]["value"],
        "output": metrics["usage"]["output"]["value"],
        "reasoning": metrics["usage"]["reasoning"]["value"],
        "contextPeak": metrics["context"]["peak"]["value"],
        "modelResponseSeconds": metrics["timing"]["modelResponseSeconds"]["value"],
        "toolSeconds": metrics["timing"]["toolSeconds"]["value"],
        "coveredSeconds": metrics["timing"]["coveredSeconds"]["value"],
        "unattributedSeconds": metrics["timing"]["unattributedSeconds"]["value"],
        "checkAttempts": metrics["checks"]["attempts"],
        "checkElapsedSeconds": metrics["checks"]["elapsedSeconds"]["value"],
    }
    expected = {
        "uncachedInput": 13, "cacheRead": 97, "output": 25, "reasoning": 9.0,
        "contextPeak": 105.0, "modelResponseSeconds": 3.5, "toolSeconds": 1.0,
        "coveredSeconds": 4.5, "unattributedSeconds": 10.5,
        "checkAttempts": 1, "checkElapsedSeconds": 2.5,
    }
    mismatches = {key: {"expected": expected[key], "observed": observed.get(key)}
                  for key in expected if observed.get(key) != expected[key]}
    return {"ok": not mismatches, "expected": expected, "observed": observed,
            "mismatches": mismatches}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline-ref", required=True,
                        help="git ref of the frozen old implementation to materialize")
    parser.add_argument("--sizes-mib", default=",".join(str(size) for size in DEFAULT_SIZES_MIB),
                        help="comma-separated synthetic log sizes in MiB (default 10,200)")
    parser.add_argument("--evidence-dir", type=Path,
                        help="private raw measurement directory (default: repository private/)")
    parser.add_argument("--keep-temp", action="store_true",
                        help="keep the temporary materialized trees and logs")
    args = parser.parse_args()
    sizes = [int(part) for part in args.sizes_mib.split(",") if part.strip()]
    if not sizes or any(size <= 0 for size in sizes):
        parser.error("--sizes-mib needs positive integers")
    try:
        baseline_commit = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", f"{args.baseline_ref}^{{commit}}"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        print(json.dumps({"ok": False, "error": "baseline_ref_unresolvable",
                          "baselineRef": args.baseline_ref}, separators=(",", ":")))
        return 2
    evidence_dir = args.evidence_dir or (ROOT / "private" / "benchmark-runtime"
                                         / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="codex-pi-benchmark-"))
    keep = args.keep_temp
    try:
        baseline_runtime = materialize(baseline_commit, work / "baseline")
        candidate_runtime = copy_candidate(work / "candidate")
        results = {"baselineRef": args.baseline_ref, "baselineCommit": baseline_commit,
                   "host": {"platform": platform.system(), "machine": platform.machine(),
                            "python": platform.python_version()},
                   "sizesMiB": sizes, "runs": {}, "factsOk": True, "errors": []}
        for size_mib in sizes:
            size_bytes = size_mib * 1024 * 1024
            log_path = work / f"log-{size_mib}MiB.txt"
            facts = generate_log(log_path, size_bytes)
            baseline_run = measure(baseline_runtime, log_path, work, f"old-{size_mib}")
            candidate_run = measure(candidate_runtime, log_path, work, f"new-{size_mib}")
            facts_ok = (
                baseline_run["logSha256"] == facts["sha256"]
                and candidate_run["logSha256"] == facts["sha256"]
                and baseline_run["testCounts"] == facts["counts"]
                and candidate_run["testCounts"] == facts["counts"]
                and (baseline_run["logTail"] or "").splitlines()[-1:] == [facts["tailLastLine"]]
                and (candidate_run["logTail"] or "").splitlines()[-1:] == [facts["tailLastLine"]]
                and baseline_run["logTail"] == candidate_run["logTail"]
                and baseline_run["summaryVerified"] and candidate_run["summaryVerified"])
            results["factsOk"] = results["factsOk"] and facts_ok
            if not facts_ok:
                results["errors"].append(f"fact mismatch at {size_mib} MiB")
            results["runs"][str(size_mib)] = {"old": baseline_run, "new": candidate_run}
        largest = max(sizes)
        old_largest = results["runs"][str(largest)]["old"]["peakRssBytes"]
        new_largest = results["runs"][str(largest)]["new"]["peakRssBytes"]
        smallest = min(sizes)
        new_smallest = results["runs"][str(smallest)]["new"]["peakRssBytes"]
        growth = new_largest - new_smallest
        saving = old_largest - new_largest
        gates = {"boundedGrowth": growth <= GROWTH_LIMIT_BYTES,
                 "significantSaving": saving >= SIGNIFICANT_SAVING_BYTES}
        results.update({"newGrowthBytes": growth, "oldMinusNewLargestBytes": saving,
                        "growthLimitBytes": GROWTH_LIMIT_BYTES,
                        "significantSavingBytes": SIGNIFICANT_SAVING_BYTES,
                        "gates": gates,
                        "wallSeconds": {key: {side: results["runs"][key][side]["wallSeconds"]
                                              for side in ("old", "new")} for key in results["runs"]},
                        "peakRssBytes": {key: {side: results["runs"][key][side]["peakRssBytes"]
                                               for side in ("old", "new")} for key in results["runs"]}})
        fixture = _timing_fixture(candidate_runtime, work)
        results["timingFixture"] = fixture
        ok = bool(results["factsOk"] and all(gates.values()) and fixture["ok"])
        results["ok"] = ok
        (evidence_dir / "result.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        summary = {"ok": ok, "baselineCommit": baseline_commit, "sizesMiB": sizes,
                   "factsOk": results["factsOk"], "gates": gates,
                   "timingFixtureOk": fixture["ok"],
                   "summaryVerified": {key: {"old": results["runs"][key]["old"]["summaryVerified"],
                                             "new": results["runs"][key]["new"]["summaryVerified"]}
                                       for key in results["runs"]},
                   "summaryWallSeconds": {key: {side: results["runs"][key][side]["summaryWall"]
                                                for side in ("old", "new")}
                                          for key in results["runs"]},
                   "wallSeconds": results["wallSeconds"], "peakRssBytes": results["peakRssBytes"],
                   "newGrowthBytes": growth, "oldMinusNewLargestBytes": saving,
                   "evidence": str(evidence_dir / "result.json"),
                   "temp": str(work) if (keep or not ok) else None}
        print(json.dumps(summary, separators=(",", ":")))
        if not ok or keep:
            keep = True
        return 0 if ok else 1
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
