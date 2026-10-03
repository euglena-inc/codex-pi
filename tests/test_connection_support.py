"""Explicit proxy configuration, frozen policy and transport diagnostics.

Offline mechanism evidence only. The fake Pi double proves the public
start/continue/status wiring, and the actual preload is exercised through
``node:diagnostics_channel`` fault injection. Neither proves provider
availability or that a proxy heals connectivity.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import (ROOT, RUNTIME, Repo, base_env, cleanup_repos, cli_json,
                             default_config, make_pi_trap, run_cli, write_config)

sys.path.insert(0, str(RUNTIME))
import pi_core  # noqa: E402
import pi_task  # noqa: E402

NODE = shutil.which("node")
PROBE = ROOT / "scripts" / "connection_support_probe.mjs"
PRELOAD = RUNTIME / "pi_network_diagnostics.mjs"
PROXY_A = "http://proxy-a.invalid:3128"
PROXY_B = "http://proxy-b.invalid:8080"
SECRET_URL = "".join(["http://", "alice", ":", "s3cr3t", "@", "private.invalid", ":3128",
                      "/tunnel?token=ZZTOKENZZ#frag"])
SECRET_PARTS = ("alice", "s3cr3t", "private.invalid", "ZZTOKENZZ")
TRACE_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy",
              "NO_PROXY", "no_proxy", "NODE_OPTIONS", "CODEX_PI_NETWORK_DIAG_FILE",
              "CODEX_PI_NETWORK_DIAG_SCOPE", "CODEX_PI_NETWORK_DIAG_SUPERVISOR",
              "CODEX_PI_TEST_UNRELATED")
ALLOWED_RECORD_KEYS = {"at", "dur", "phase", "class", "code", "status", "hdr", "scope",
                       "proc", "primary"}


def connection_env(**extra) -> dict:
    """Test environment with inherited proxy/NODE_OPTIONS removed.

    Verbatim trace values are only requested for this explicit key list, so a
    developer's real proxy credentials are never written into a trace file.
    """
    env = base_env(**extra)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    if "NODE_OPTIONS" not in extra:
        env.pop("NODE_OPTIONS", None)
    env["NO_PROXY"] = "existing.invalid"
    env["no_proxy"] = "existing.invalid"
    env["CODEX_PI_TEST_UNRELATED"] = "synthetic-unrelated"
    env["PI_DOUBLE_ENV_TRACE_KEYS"] = ",".join(TRACE_KEYS)
    return env


def trace_entries(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class NetworkConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-network-cfg-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        cleanup_repos()

    def test_absent_network_keeps_defaults_and_project_output(self):
        repo = Repo(self.tmp, name="default", config=default_config())
        data = cli_json("project", "--repo", str(repo.root))
        self.assertEqual(data["config"]["network"], {"proxyUrl": None, "diagnostics": False})
        self.assertEqual(data["limits"]["network"], {"proxyUrl": None, "diagnostics": False})
        self.assertIn("freezes the project policy", data["limits"]["networkPolicy"])

    def test_valid_network_is_canonicalized(self):
        config = default_config(network={"proxyUrl": "HTTPS://Proxy-A.invalid:443/",
                                         "diagnostics": True})
        repo = Repo(self.tmp, name="valid", config=config)
        data = cli_json("project", "--repo", str(repo.root))
        self.assertEqual(data["config"]["network"],
                         {"proxyUrl": "https://proxy-a.invalid:443", "diagnostics": True})

    def test_invalid_network_rejected_before_spawn_without_echo(self):
        trap, marker = make_pi_trap(self.tmp / "bin")
        cases = (
            {"proxyUrl": SECRET_URL},
            {"proxyUrl": "http://proxy.invalid"},
            {"proxyUrl": "socks5://proxy.invalid:3128"},
            {"proxyUrl": "http://proxy.invalid:3128/path"},
            {"proxyUrl": "http://proxy.invalid:3128?x=1"},
            {"proxyUrl": "http://proxy.invalid:3128#frag"},
            {"proxyUrl": "http://:3128"},
            {"proxyUrl": "http://proxy.invalid:99999"},
            {"proxyUrl": "http://proxy.invalid:abc"},
            {"proxyUrl": True},
            {"proxyUrl": " http://proxy.invalid:3128"},
            {"diagnostics": "yes"},
            {"unsupported": True},
        )
        for index, network in enumerate(cases):
            with self.subTest(network=network):
                repo = Repo(self.tmp, name=f"invalid-{index}",
                            config=default_config(network=network))
                env = connection_env(PI_BIN=str(trap))
                project = run_cli("project", "--repo", str(repo.root), env=env, expect=2)
                for part in SECRET_PARTS:
                    self.assertNotIn(part, project.stderr)
                worktree = repo.worktree("wt")
                started = repo.start(f"bad-{index}", worktree, env=env, expect=2)
                for part in SECRET_PARTS:
                    self.assertNotIn(part, started.stderr)
                self.assertFalse(repo.task_dir(f"bad-{index}").exists())
        self.assertFalse(marker.exists())

    def test_rejected_url_error_names_the_category_not_the_value(self):
        try:
            pi_core.normalize_proxy_url(SECRET_URL)
        except ValueError as exc:
            message = str(exc)
            self.assertIn("proxyUrl", message)
            for part in SECRET_PARTS:
                self.assertNotIn(part, message)
        else:
            self.fail("credential-bearing proxy URL was accepted")


class ApplyPolicyTest(unittest.TestCase):
    def test_explicit_route_overrides_all_forms_and_preserves_exclusions(self):
        env = {"HTTP_PROXY": "http://old.invalid:1", "http_proxy": "http://old.invalid:2",
               "NO_PROXY": "keep.invalid", "CODEX_PI_TEST_UNRELATED": "x"}
        result, record = pi_core.apply_network_policy(
            env, {"proxyUrl": PROXY_A, "diagnostics": False, "source": "frozen"})
        for key in pi_core.NETWORK_PROXY_ENV_KEYS:
            self.assertEqual(result[key], PROXY_A)
        self.assertEqual(result["NO_PROXY"], "keep.invalid")
        self.assertEqual(result["CODEX_PI_TEST_UNRELATED"], "x")
        self.assertEqual(record["proxy"], {"mode": "explicit", "source": "frozen",
                                           "origin": PROXY_A, "envProxyPresent": True})
        self.assertEqual(record["diagnostics"], {"enabled": False, "file": None})

    def test_diagnostics_preserve_existing_node_options_and_encode_spaces(self):
        space = self.tmp_root = Path(tempfile.mkdtemp(prefix="codex-pi-space-"))
        self.addCleanup(shutil.rmtree, space, True)
        tool_dir = space / "tools with space"
        tool_dir.mkdir()
        preload = tool_dir / "pi_network_diagnostics.mjs"
        shutil.copy2(PRELOAD, preload)
        sidecar = space / "round.network.jsonl"
        env = {"NODE_OPTIONS": "--max-old-space-size=128"}
        result, record = pi_core.apply_network_policy(
            env, {"proxyUrl": PROXY_A, "diagnostics": True, "source": "frozen"},
            diagnostics_file=sidecar, scope="round-2", supervisor_pid=4242, preload_path=preload)
        self.assertTrue(result["NODE_OPTIONS"].startswith("--max-old-space-size=128 --import="))
        self.assertIn("%20", result["NODE_OPTIONS"])
        import_url = result["NODE_OPTIONS"].split("--import=", 1)[1]
        self.assertNotIn(" ", import_url)
        self.assertEqual(result["CODEX_PI_NETWORK_DIAG_FILE"], str(sidecar))
        self.assertEqual(result["CODEX_PI_NETWORK_DIAG_SCOPE"], "round-2")
        self.assertEqual(result["CODEX_PI_NETWORK_DIAG_SUPERVISOR"], "4242")
        self.assertEqual(record["diagnostics"], {"enabled": True, "file": str(sidecar)})

    def test_disabled_policy_leaves_environment_untouched(self):
        env = {"HTTP_PROXY": "http://inherited.invalid:8080", "NO_PROXY": "keep.invalid"}
        result, record = pi_core.apply_network_policy(
            env, {"proxyUrl": None, "diagnostics": False, "source": "legacy"})
        self.assertEqual(result, env)
        self.assertEqual(record["proxy"], {"mode": "inherited", "source": "legacy",
                                           "origin": None, "envProxyPresent": True})
        self.assertNotIn("NODE_OPTIONS", result)

    def test_missing_preload_refuses_diagnostics(self):
        with self.assertRaises(ValueError):
            pi_core.apply_network_policy(
                {}, {"proxyUrl": None, "diagnostics": True, "source": "frozen"},
                diagnostics_file="/tmp/x/round.network.jsonl", scope="round-1",
                preload_path="/tmp/no-such-preload.mjs")

    def test_frozen_policy_is_revalidated(self):
        policy = pi_core.network_policy_for_task({"network": {"proxyUrl": PROXY_A,
                                                              "diagnostics": True}})
        self.assertEqual(policy, {"proxyUrl": PROXY_A, "diagnostics": True, "source": "frozen"})
        self.assertEqual(pi_core.network_policy_for_task({}),
                         {"proxyUrl": None, "diagnostics": False, "source": "legacy"})
        with self.assertRaises(ValueError):
            pi_core.network_policy_for_task({"network": {"proxyUrl": SECRET_URL}})


class SidecarReaderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-network-side-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_missing_and_disabled(self):
        self.assertEqual(pi_core.read_network_sidecar(None)["status"], "missing")
        self.assertEqual(pi_core.read_network_sidecar(self.tmp / "none.jsonl")["status"], "missing")

    def test_valid_records_are_counted_and_abort_cleanup_is_not_failure(self):
        path = self.tmp / "round.network.jsonl"
        records = [
            {"at": 0.0, "phase": "observer_ready", "scope": "round-1", "proc": "aaaa"},
            {"at": 1.0, "phase": "request_error", "class": "connection_reset", "code": "ECONNRESET"},
            {"at": 2.0, "phase": "request_error", "class": "abort_cleanup", "code": "ABORT_ERR"},
            {"at": 3.0, "phase": "request_error", "class": "proxy_connect_failure",
             "code": None, "status": 503},
        ]
        path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
        summary = pi_core.read_network_sidecar(path)
        self.assertEqual(summary["status"], "present")
        self.assertEqual(summary["records"], 4)
        self.assertEqual(summary["failures"], 2)
        self.assertFalse(summary["truncated"])
        self.assertEqual(summary["classes"]["connection_reset"], 1)

    def test_truncation_marker_and_corruption_are_explicit(self):
        path = self.tmp / "round.network.jsonl"
        path.write_text(json.dumps({"phase": "request_error", "class": "timeout"}) + "\n"
                        + json.dumps({"phase": "truncated"}) + "\n", encoding="utf-8")
        summary = pi_core.read_network_sidecar(path)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["records"], 1)
        path.write_text("{not json\n", encoding="utf-8")
        self.assertEqual(pi_core.read_network_sidecar(path)["status"], "unreadable")

    def test_network_evidence_legacy_defaults(self):
        evidence = pi_task.network_evidence({}, self.tmp, 1, {})
        self.assertEqual(evidence["proxy"], {"mode": "inherited", "source": "legacy",
                                             "origin": None, "envProxyPresent": None})
        self.assertEqual(evidence["diagnostics"]["status"], "disabled")


class FrozenPolicyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-network-int-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name)

    def make(self, network=None, name="repo"):
        config = default_config()
        if network is not None:
            config["network"] = network
        repo = Repo(self.tmp, name=name, config=config)
        worktree = repo.worktree("wt")
        return repo, worktree

    def start(self, repo, worktree, task, env, diagnostics: bool = False, proxy=PROXY_A):
        started = repo.start_json(task, worktree, env=env)
        result = repo.wait_terminal(task, env=env)
        self.assertEqual(result["state"], "completed")
        return started

    def test_new_task_freezes_policy_and_round_applies_it(self):
        repo, worktree = self.make(network={"proxyUrl": PROXY_A, "diagnostics": False})
        trace = self.tmp / "trace.jsonl"
        env = connection_env(PI_DOUBLE_TRACE=str(trace))
        self.start(repo, worktree, "frozen", env)
        task = json.loads((repo.task_dir("frozen") / "task.json").read_text())
        self.assertEqual(task["network"], {"proxyUrl": PROXY_A, "diagnostics": False})
        entry = trace_entries(trace)[0]
        for key in pi_core.NETWORK_PROXY_ENV_KEYS:
            self.assertEqual(entry["envValues"][key], PROXY_A)
        self.assertEqual(entry["envValues"]["NO_PROXY"], "existing.invalid")
        self.assertEqual(entry["envValues"]["CODEX_PI_TEST_UNRELATED"], "synthetic-unrelated")
        self.assertNotIn("--import=", entry["envValues"].get("NODE_OPTIONS") or "")
        status = cli_json("status", "--repo", str(repo.root), "--task", "frozen", env=env)
        self.assertEqual(status["network"]["proxy"]["mode"], "explicit")
        self.assertEqual(status["network"]["proxy"]["origin"], PROXY_A)
        self.assertEqual(status["network"]["diagnostics"]["status"], "disabled")
        result = cli_json("result", "--repo", str(repo.root), "--task", "frozen", env=env)
        self.assertEqual(result["network"]["proxy"]["origin"], PROXY_A)

    def test_continue_uses_frozen_policy_after_project_edit(self):
        repo, worktree = self.make(network={"proxyUrl": PROXY_A, "diagnostics": False})
        trace = self.tmp / "trace.jsonl"
        env = connection_env(PI_DOUBLE_TRACE=str(trace))
        self.start(repo, worktree, "frozen", env)
        write_config(repo.root, default_config(network={"proxyUrl": PROXY_B, "diagnostics": True}))
        repo.continue_task("frozen", env=env)
        result = repo.wait_terminal("frozen", env=env)
        self.assertEqual(result["state"], "completed")
        entries = trace_entries(trace)
        self.assertEqual(len(entries), 2)
        for key in pi_core.NETWORK_PROXY_ENV_KEYS:
            self.assertEqual(entries[1]["envValues"][key], PROXY_A)
        self.assertIsNone(entries[1]["envValues"].get("CODEX_PI_NETWORK_DIAG_FILE"))
        self.assertNotIn("--import=", entries[1]["envValues"].get("NODE_OPTIONS") or "")

    def test_legacy_task_without_frozen_network_keeps_inherited_behavior(self):
        repo, worktree = self.make(network=None)
        trace = self.tmp / "trace.jsonl"
        env = connection_env(PI_DOUBLE_TRACE=str(trace))
        self.start(repo, worktree, "legacy", env)
        # Simulate a task created before the network feature existed.
        task_file = repo.task_dir("legacy") / "task.json"
        frozen = json.loads(task_file.read_text())
        frozen.pop("network", None)
        task_file.write_text(json.dumps(frozen))
        write_config(repo.root, default_config(network={"proxyUrl": PROXY_B, "diagnostics": True}))
        repo.continue_task("legacy", env=env)
        result = repo.wait_terminal("legacy", env=env)
        self.assertEqual(result["state"], "completed")
        entry = trace_entries(trace)[1]
        for key in pi_core.NETWORK_PROXY_ENV_KEYS:
            self.assertIsNone(entry["envValues"].get(key))
        self.assertIsNone(entry["envValues"].get("CODEX_PI_NETWORK_DIAG_FILE"))
        self.assertNotIn("--import=", entry["envValues"].get("NODE_OPTIONS") or "")
        status = cli_json("status", "--repo", str(repo.root), "--task", "legacy", env=env)
        self.assertEqual(status["network"]["proxy"]["source"], "legacy")

    def test_diagnostics_enable_preload_and_status_marks_missing_unknown(self):
        repo, worktree = self.make(network={"proxyUrl": PROXY_A, "diagnostics": True})
        trace = self.tmp / "trace.jsonl"
        env = connection_env(PI_DOUBLE_TRACE=str(trace))
        self.start(repo, worktree, "diag", env)
        entry = trace_entries(trace)[0]
        sidecar = repo.task_dir("diag") / "rounds" / "1" / "round.network.jsonl"
        self.assertEqual(entry["envValues"]["CODEX_PI_NETWORK_DIAG_FILE"],
                         str(sidecar.resolve()))
        self.assertEqual(entry["envValues"]["CODEX_PI_NETWORK_DIAG_SCOPE"], "round-1")
        node_options = entry["envValues"]["NODE_OPTIONS"]
        self.assertIn("--import=file://", node_options)
        self.assertIn("pi_network_diagnostics.mjs", node_options)
        self.assertFalse(sidecar.exists(), "the fake Pi never loads the Node preload")
        status = cli_json("status", "--repo", str(repo.root), "--task", "diag", env=env)
        self.assertEqual(status["network"]["diagnostics"]["status"], "missing")
        self.assertTrue(any("unknown, not healthy" in note for note in status["notes"]))
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"at": 1, "phase": "request_error",
                                       "class": "connection_reset", "code": "ECONNRESET"}) + "\n",
                           encoding="utf-8")
        status = cli_json("status", "--repo", str(repo.root), "--task", "diag", env=env)
        self.assertEqual(status["network"]["diagnostics"]["status"], "present")
        self.assertEqual(status["network"]["diagnostics"]["records"], 1)
        self.assertEqual(status["network"]["diagnostics"]["failures"], 1)

    def test_path_with_spaces_and_existing_node_options_survive(self):
        repo, worktree = self.make(network={"proxyUrl": PROXY_A, "diagnostics": True},
                                   name="repo with space")
        trace = self.tmp / "trace.jsonl"
        env = connection_env(PI_DOUBLE_TRACE=str(trace), NODE_OPTIONS="--max-old-space-size=128")
        self.start(repo, worktree, "spaced", env)
        entry = trace_entries(trace)[0]
        node_options = entry["envValues"]["NODE_OPTIONS"]
        self.assertTrue(node_options.startswith("--max-old-space-size=128 --import="))
        self.assertIn("%20", node_options)
        import_url = node_options.split("--import=", 1)[1]
        self.assertNotIn(" ", import_url)
        self.assertTrue(entry["envValues"]["CODEX_PI_NETWORK_DIAG_SUPERVISOR"].isdigit())

    def test_disabled_diagnostics_creates_no_sidecar_or_preload(self):
        repo, worktree = self.make(network={"proxyUrl": None, "diagnostics": False})
        trace = self.tmp / "trace.jsonl"
        env = connection_env(PI_DOUBLE_TRACE=str(trace), NODE_OPTIONS="")
        self.start(repo, worktree, "quiet", env)
        entry = trace_entries(trace)[0]
        self.assertEqual(entry["envValues"]["NODE_OPTIONS"], "")
        self.assertIsNone(entry["envValues"].get("CODEX_PI_NETWORK_DIAG_FILE"))
        self.assertFalse((repo.task_dir("quiet") / "rounds" / "1"
                          / "round.network.jsonl").exists())


class HelperSnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-network-snap-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_helper_catalog_and_snapshot_include_preload(self):
        self.assertIn("pi_network_diagnostics.mjs", pi_task.HELPER_FILES)
        self.assertTrue(PRELOAD.is_file())
        destination = self.tmp / "tools"
        hashes = pi_task.snapshot_helpers(RUNTIME, destination)
        digest = hashlib.sha256(PRELOAD.read_bytes()).hexdigest()
        self.assertEqual(hashes["pi_network_diagnostics.mjs"], digest)
        self.assertEqual((destination / "pi_network_diagnostics.mjs").read_bytes(),
                         PRELOAD.read_bytes())


@unittest.skipUnless(NODE, "node is required for the preload mechanism tests")
class PreloadMechanismTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-network-node-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def probe_env(self, sidecar: Path) -> dict:
        env, _record = pi_core.apply_network_policy(
            os.environ, {"proxyUrl": None, "diagnostics": True, "source": "frozen"},
            diagnostics_file=sidecar, scope="test-probe", supervisor_pid=os.getpid(),
            preload_path=PRELOAD)
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        return env

    def run_probe(self, mode: str, sidecar: Path) -> dict:
        proc = subprocess.run([NODE, str(PROBE), mode], env=self.probe_env(sidecar),
                              cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, f"{mode}: {proc.stderr[-400:]}")
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def records(self, sidecar: Path) -> list:
        return [json.loads(line) for line in
                sidecar.read_text(encoding="utf-8").splitlines() if line]

    def test_synthetic_classification_privacy_and_allowlist(self):
        sidecar = self.tmp / "round.network.jsonl"
        self.run_probe("synthetic", sidecar)
        records = self.records(sidecar)
        self.assertEqual(records[0]["phase"], "observer_ready")
        for record in records:
            self.assertFalse(set(record) - ALLOWED_RECORD_KEYS,
                             f"non-allowlisted keys: {sorted(set(record) - ALLOWED_RECORD_KEYS)}")
        classes = {record.get("class") for record in records if record.get("class")}
        self.assertEqual(classes, {"connection_reset", "abort_cleanup", "proxy_connect_failure",
                                   "dns_failure", "timeout", "connection_refused", "tls_failure",
                                   "post_header_error", "transport_error", "unknown"})
        self.assertTrue(any(record.get("status") == 503 for record in records))
        abort = [record for record in records if record.get("class") == "abort_cleanup"]
        self.assertTrue(abort)
        self.assertTrue(all(record.get("status") is None for record in abort))
        raw = sidecar.read_text(encoding="utf-8")
        for part in ("alice", "s3cr3t", "private.invalid", "ZZTOKENZZ", "Proxy response",
                     "socket hang up", "getaddrinfo", "ECONNRESET://"):
            self.assertNotIn(part, raw, f"sidecar leaked {part!r}")

    def test_real_socket_reset_is_classified_and_success_is_silent(self):
        sidecar = self.tmp / "round.network.jsonl"
        observation = self.run_probe("real", sidecar)
        self.assertTrue(observation["reset"])
        self.assertTrue(observation["healthyOk"])
        records = self.records(sidecar)
        pre_header = [record for record in records
                      if record.get("class") in ("connection_reset", "transport_error")
                      and record.get("hdr") is False]
        self.assertEqual(len(pre_header), 1, records)
        failures = [record for record in records
                    if record.get("class") not in (None, "abort_cleanup")]
        self.assertEqual(len(failures), 1)

    def test_flood_is_bounded_with_explicit_truncation(self):
        sidecar = self.tmp / "round.network.jsonl"
        self.run_probe("flood", sidecar)
        records = self.records(sidecar)
        self.assertLessEqual(len(records), 257)
        self.assertTrue(any(record.get("phase") == "truncated" for record in records))
        self.assertLessEqual(sidecar.stat().st_size, 65536)

    def test_write_failure_is_harmless(self):
        observation = self.run_probe("write-failure", self.tmp)
        self.assertEqual(observation["exit"], 0)

    def test_disabled_diagnostics_adds_no_preload_or_file(self):
        base = {key: value for key, value in os.environ.items() if key != "NODE_OPTIONS"}
        env, _record = pi_core.apply_network_policy(
            base, {"proxyUrl": None, "diagnostics": False, "source": "frozen"},
            diagnostics_file=self.tmp / "never.jsonl", scope="round-1",
            supervisor_pid=os.getpid(), preload_path=PRELOAD)
        proc = subprocess.run([NODE, "-e", "console.log('ok')"], env=env, cwd=str(ROOT),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-200:])
        self.assertFalse((self.tmp / "never.jsonl").exists())

    def test_preload_marks_observer_ready_and_does_not_break_plain_node(self):
        sidecar = self.tmp / "round.network.jsonl"
        proc = subprocess.run([NODE, "-e", "console.log('ok')"], env=self.probe_env(sidecar),
                              cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-200:])
        records = self.records(sidecar)
        self.assertEqual(records[0]["phase"], "observer_ready")


if __name__ == "__main__":
    unittest.main()
