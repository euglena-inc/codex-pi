"""Step 5 (Python side): worker.json, launch argv, load proof, version gate, settle view.

The extension itself is covered by test_worker_extension.py. Everything here runs
against the offline Pi double; no model, network or real Pi is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from runtime_helpers import Repo, RUNTIME, base_env, cleanup_repos, cli_json, default_config, run_cli

sys.path.insert(0, str(RUNTIME))
import pi_task  # noqa: E402
import pi_phase  # noqa: E402


class Step5Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-s5-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = Repo(self.tmp, name="repo", config=default_config())
        self.worktree = self.repo.worktree("wt")

    def round_dir(self, task, number=1):
        return self.repo.task_dir(task) / "rounds" / str(number)

    def start(self, task, env, *extra, expect=0):
        return run_cli("start", "--repo", self.repo.root, "--task", task, "--worktree",
                       self.worktree, "--prompt", "Implement the change.", *extra, env=env,
                       expect=expect)


class LaunchTest(Step5Case):
    def test_launch_argv_loads_the_frozen_extension_and_passes_the_config(self):
        trace = self.tmp / "trace.jsonl"
        env = base_env(PI_DOUBLE_MODE="session-trace", PI_DOUBLE_TRACE=str(trace))
        self.start("launch", env)
        self.repo.wait_terminal("launch", env=env)
        recorded = json.loads(trace.read_text().splitlines()[0])
        tools = self.repo.task_dir("launch") / "tools"
        self.assertTrue(recorded["noExtensions"])
        self.assertTrue(recorded["noApprove"])
        self.assertEqual(recorded["extension"], str(tools / "pi_worker.ts"))
        self.assertEqual(recorded["workerConfig"], str(self.round_dir("launch") / "worker.json"))
        for flag in ("-p", "--mode", "json", "--no-skills", "--no-prompt-templates"):
            self.assertIn(flag, recorded["argv"])
        self.assertEqual(recorded["tools"], "read,write,edit,bash,check,progress,readiness,codemode")
        self.assertEqual((tools / "pi_worker.ts").read_bytes(), (RUNTIME / "pi_worker.ts").read_bytes())
        helper_hashes = json.loads((self.repo.task_dir("launch") / "task.json").read_text())["helperHashes"]
        self.assertEqual(helper_hashes["pi_worker.ts"],
                         hashlib.sha256((RUNTIME / "pi_worker.ts").read_bytes()).hexdigest())
        self.assertNotIn("pi_command_guard.py", helper_hashes)

    def test_worker_json_holds_the_round_configuration(self):
        env = base_env(PI_DOUBLE_MODE="ok")
        self.start("cfg", env)
        self.repo.wait_terminal("cfg", env=env)
        worker = json.loads((self.round_dir("cfg") / "worker.json").read_text())
        task_dir = self.repo.task_dir("cfg")
        self.assertEqual(sorted(worker), sorted([
            "schemaVersion", "task", "round", "repo", "worktree", "forbiddenRoots", "allowedRoots",
            "bashDefaultTimeoutSeconds", "bashCeilingSeconds", "checkTimeoutSeconds", "checksDir",
            "toolsDir", "python", "phase", "settleQuotaPath", "roundDir", "contract"]))
        self.assertEqual((worker["task"], worker["round"], worker["phase"]), ("cfg", 1, False))
        self.assertEqual(worker["python"], sys.executable)
        self.assertEqual(worker["checksDir"], str(self.round_dir("cfg") / "round.checks"))
        self.assertEqual(worker["roundDir"], str(self.round_dir("cfg")))
        self.assertIn(os.path.realpath(str(self.repo.root)), worker["forbiddenRoots"])
        self.assertNotIn(os.path.realpath(str(self.worktree)), worker["forbiddenRoots"])
        self.assertIn(os.path.realpath(str(self.worktree)), worker["allowedRoots"])
        self.assertIn(str(task_dir / "tools"), worker["allowedRoots"])
        self.assertEqual(worker["contract"], (self.round_dir("cfg") / "contract.md").read_text())
        self.assertEqual(worker["bashDefaultTimeoutSeconds"], 600)
        self.assertEqual(worker["bashCeilingSeconds"], 3600)


class BriefTest(Step5Case):
    def test_every_round_gets_the_same_short_header_and_a_contract_file(self):
        env = base_env(PI_DOUBLE_MODE="ok")
        self.start("brief", env)
        self.repo.wait_terminal("brief", env=env)
        run_cli("continue", "--repo", self.repo.root, "--task", "brief", "--prompt",
                "Repair round.", env=env)
        self.repo.wait_terminal("brief", env=env, round=2)
        for number in (1, 2):
            brief = (self.round_dir("brief", number) / "brief.md").read_text()
            header = brief.split("\n---\n", 1)[1]
            self.assertLessEqual(len(header.splitlines()), 15)
            self.assertIn("check", header)
            self.assertIn("forbidden-path", header)
            self.assertNotIn("Model policy", brief, "the contract is not repeated in the brief")
            self.assertNotIn("pi_check.py", brief, "no helper command templates")
            self.assertNotIn("progress --repo", brief)
            contract = (self.round_dir("brief", number) / "contract.md").read_text()
            self.assertIn("Model policy", contract)
            self.assertIn("check(id, command", contract)
            worker = json.loads((self.round_dir("brief", number) / "worker.json").read_text())
            self.assertEqual(worker["contract"], contract)
        round2 = (self.round_dir("brief", 2) / "brief.md").read_text()
        self.assertIn(str(self.round_dir("brief", 1) / "brief.md"), round2)
        self.assertIn("Previous round 1", round2)
        self.assertIn("conservative reading", (self.round_dir("brief", 1) / "contract.md").read_text())


class VersionGateTest(Step5Case):
    def test_start_and_continue_refuse_an_old_or_unreadable_pi(self):
        for task, version in (("old-a", "0.9.9"), ("old-b", "garbage")):
            env = base_env(PI_DOUBLE_VERSION=version)
            proc = self.start(task, env, expect=2)
            self.assertIn("Pi >= 1.0.0", proc.stderr)
            self.assertFalse(self.repo.task_dir(task).exists())
        missing = base_env(PI_BIN=str(self.tmp / "no-such-pi"))
        self.assertIn("cannot read the Pi version", self.start("gone", missing, expect=2).stderr)
        ok = base_env(PI_DOUBLE_MODE="ok", PI_DOUBLE_VERSION="1.2.3")
        self.start("fresh", ok)
        self.repo.wait_terminal("fresh", env=ok)
        self.assertEqual(json.loads((self.repo.task_dir("fresh") / "task.json").read_text())["piVersion"], "1.2.3")
        old = base_env(PI_DOUBLE_VERSION="0.8.0")
        refused = run_cli("continue", "--repo", self.repo.root, "--task", "fresh", "--prompt", "x",
                          env=old, expect=2)
        self.assertIn("too old", refused.stderr)
        self.assertEqual(sorted(p.name for p in (self.repo.task_dir("fresh") / "rounds").iterdir()), ["1"])
        newer = base_env(PI_DOUBLE_MODE="ok", PI_DOUBLE_VERSION="1.4.0")
        run_cli("continue", "--repo", self.repo.root, "--task", "fresh", "--prompt", "x", env=newer)
        self.repo.wait_terminal("fresh", env=newer, round=2)
        self.assertIn("pi_version=1.2.3", (self.round_dir("fresh", 1) / "round.meta").read_text())
        self.assertIn("pi_version=1.4.0", (self.round_dir("fresh", 2) / "round.meta").read_text())


class LoadProofTest(Step5Case):
    def test_a_round_without_the_ready_marker_fails_and_is_never_ready(self):
        env = base_env(PI_DOUBLE_MODE="ok", PI_DOUBLE_NO_READY="1")
        self.start("noready", env)
        result = self.repo.wait_terminal("noready", env=env)
        self.assertEqual(result["state"], "failed")
        state = json.loads((self.round_dir("noready") / "round.state.json").read_text())
        self.assertIn("WORKER_EXTENSION_NOT_LOADED", state["error"])
        self.assertNotEqual(state["exitCode"], 0)
        self.assertNotEqual(result["execution"], "completed_execution")

    def test_a_tool_execution_before_the_marker_kills_the_round(self):
        env = base_env(PI_DOUBLE_MODE="tool-before-ready")
        self.start("early", env)
        result = self.repo.wait_terminal("early", env=env, timeout=30)
        self.assertEqual(result["state"], "failed")
        state = json.loads((self.round_dir("early") / "round.state.json").read_text())
        self.assertIn("WORKER_EXTENSION_NOT_LOADED", state["error"])
        self.assertIn("before worker.ready", state["error"])
        self.assertFalse((self.round_dir("early") / "worker.ready").exists())

    def test_a_loaded_round_completes_normally(self):
        env = base_env(PI_DOUBLE_MODE="ok")
        self.start("loaded", env)
        self.assertEqual(self.repo.wait_terminal("loaded", env=env)["state"], "completed")
        ready = json.loads((self.round_dir("loaded") / "worker.ready").read_text())
        self.assertEqual(sorted(ready), ["at", "codemode", "piVersion", "pid"])


class SettleViewTest(unittest.TestCase):
    """Eligibility is computed in Python; the extension only claims the quota file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-settle-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = Repo(self.tmp, name="repo", config=default_config())
        design = self.repo.root / "docs" / "design.md"
        design.parent.mkdir(parents=True)
        design.write_text("# design\n")
        self.repo._git("add", "-A")
        self.repo._git("-c", "user.email=t@e.invalid", "-c", "user.name=T", "commit", "-qm", "design")
        self.worktree = self.repo.worktree("wt")
        contract = {
            "schemaVersion": 1, "phaseId": "P-SETTLE", "goal": "g", "result": "r", "baseline": "HEAD",
            "scope": ["."], "designRef": "docs/design.md",
            "designSha256": hashlib.sha256(design.read_bytes()).hexdigest(),
            "acceptanceItems": [
                {"id": "A1", "description": "d", "command": "python3 -c pass", "passCondition": "exit 0",
                 "evidence": "receipt"},
                {"id": "A2", "description": "d", "command": "python3 -c pass", "passCondition": "exit 0",
                 "evidence": "receipt"}],
            "budgetSeconds": 3600, "commandTimeoutSeconds": 900, "resourceLimits": [],
            "autonomousRepair": ["fix"], "escalateWhen": ["design"]}
        (self.tmp / "c.json").write_text(json.dumps(contract))
        self.env = base_env(PI_DOUBLE_MODE="hang")
        run_cli("start", "--repo", self.repo.root, "--task", "settle", "--worktree", self.worktree,
                "--prompt", "go", "--contract-file", self.tmp / "c.json", env=self.env)
        self.repo.wait_round_state("settle", "running")
        self.checks = self.repo.task_dir("settle") / "rounds" / "1" / "round.checks"
        self.head = subprocess.check_output(["git", "-C", str(self.worktree), "rev-parse", "HEAD"],
                                            text=True).strip()

    def tearDown(self):
        self.repo.cancel("settle", env=self.env)
        self.repo.wait_terminal("settle", env=self.env, timeout=25)

    def receipt(self, check_id, exit_code):
        self.checks.mkdir(parents=True, exist_ok=True)
        log = self.checks / f"{check_id}-{uuid.uuid4().hex[:8]}.log"
        log.write_text("evidence\n")
        started = time.time() - 1
        (self.checks / f"{check_id}-{uuid.uuid4().hex[:8]}.json").write_text(json.dumps({
            "schema_version": 1, "id": check_id, "argv": shlex.split("python3 -c pass"),
            "cwd": str(self.checks), "head": self.head, "dirty": False, "started_at": started,
            "ended_at": time.time(), "deadline_at": started + 900, "exit_code": exit_code,
            "timed_out": False, "cancelled": False, "error": None, "test_counts": None,
            "log": log.name, "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "acceptance": "not_verified"}))

    def settle(self):
        return cli_json("readiness", "--repo", str(self.repo.root), "--task", "settle", env=self.env)["settle"]

    def test_only_missing_evidence_is_eligible_and_the_quota_file_ends_it(self):
        view = self.settle()
        self.assertTrue(view["eligible"])
        self.assertEqual([item["id"] for item in view["missing"]], ["A1", "A2"])
        self.assertEqual(view["missing"][0]["command"], "python3 -c pass")
        self.receipt("A1", 0)
        self.assertEqual([item["id"] for item in self.settle()["missing"]], ["A2"])
        quota = pi_phase.settle_quota_path(self.repo.task_dir("settle"), "P-SETTLE")
        worker = json.loads((self.repo.task_dir("settle") / "rounds" / "1" / "worker.json").read_text())
        self.assertEqual(worker["settleQuotaPath"], str(quota))
        quota.parent.mkdir(parents=True, exist_ok=True)
        quota.write_text("{}")
        used = self.settle()
        self.assertFalse(used["eligible"])
        self.assertEqual(used["reason"], "quota_used")
        status = cli_json("phase-status", "--repo", str(self.repo.root), "--task", "settle", env=self.env)
        self.assertTrue(status["settleQuotaUsed"])

    def test_a_failed_check_is_not_a_missing_one(self):
        self.receipt("A1", 3)
        view = self.settle()
        self.assertFalse(view["eligible"])
        self.assertEqual(view["reason"], "required_check_not_missing")

    def test_complete_coverage_has_nothing_to_settle(self):
        self.receipt("A1", 0)
        self.receipt("A2", 0)
        self.assertEqual(self.settle()["reason"], "nothing_missing")

    def test_a_pause_blocks_the_settle_continuation(self):
        board = self.repo.state_dir / "board.json"
        run = subprocess.run([sys.executable, str(RUNTIME / "pi_board.py"), "register", "--repo",
                              str(self.repo.root), "--task", "settle", "--transport", "offline"],
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(run.returncode, 0, run.stderr)
        subprocess.run([sys.executable, str(RUNTIME / "pi_board.py"), "pause", "--repo",
                        str(self.repo.root), "--task", "settle"], check=True, capture_output=True,
                       env=self.env)
        self.assertTrue(board.exists())
        self.assertEqual(self.settle()["reason"], "paused")


if __name__ == "__main__":
    unittest.main()
