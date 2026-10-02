"""The in-process worker extension (runtime/pi_worker.ts) driven through Node.

Node strips the TypeScript types natively; a fake ExtensionAPI harness
(tests/doubles/extension_harness.mjs) loads the real extension file. No Pi and no
model are involved. A missing ``node`` fails the suite instead of skipping it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import RUNTIME, Repo, cleanup_repos

sys.path.insert(0, str(RUNTIME))
import pi_task  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "doubles" / "extension_harness.mjs"
EXTENSION = RUNTIME / "pi_worker.ts"
NODE = shutil.which("node")


def setUpModule():
    if NODE is None:
        raise AssertionError("node is required on PATH: the worker extension tests must not be skipped")


class ExtensionCase(unittest.TestCase):
    """A synthetic task: main checkout, round worktree, another worktree, frozen tools."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-ext-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = Repo(self.tmp, name="main")
        self.main = self.repo.root.resolve()
        self.worktree = self.repo.worktree("wt").resolve()
        self.other = self.repo.worktree("other").resolve()
        (self.main / "secret.txt").write_text("secret\n")
        self.task_dir = self.main / ".git" / "codex-pi" / "tasks" / "T"
        self.round_dir = self.task_dir / "rounds" / "1"
        self.tools = self.task_dir / "tools"
        self.checks = self.round_dir / "round.checks"
        for path in (self.tools, self.checks):
            path.mkdir(parents=True)
        pi_task.snapshot_helpers(RUNTIME, self.tools)
        self.quota = self.task_dir / "settle" / "phase.claim"
        self.alias = self.tmp / "alias"
        self.alias.symlink_to(self.main)
        self.config = {
            "schemaVersion": 1, "task": "T", "round": 1, "repo": str(self.main),
            "worktree": str(self.worktree),
            "forbiddenRoots": [str(self.main), str(self.other)],
            "allowedRoots": [str(self.worktree), str(self.tools), str(self.checks)],
            "bashDefaultTimeoutSeconds": 30, "bashCeilingSeconds": 120,
            "checkTimeoutSeconds": 60, "checksDir": str(self.checks), "toolsDir": str(self.tools),
            "python": sys.executable, "phase": False, "settleQuotaPath": None,
            "roundDir": str(self.round_dir), "contract": "CONTRACT TEXT",
        }

    def run_steps(self, steps, config=None, env=None):
        config = self.config if config is None else config
        config_path = self.tmp / "worker.json"
        config_path.write_text(json.dumps(config))
        scenario = self.tmp / "scenario.json"
        scenario.write_text(json.dumps({"steps": steps}))
        full_env = {**os.environ, "CODEX_PI_WORKER_CONFIG": str(config_path), **(env or {})}
        if env and env.get("CODEX_PI_WORKER_CONFIG") is None:
            full_env.pop("CODEX_PI_WORKER_CONFIG", None)
        proc = subprocess.run([NODE, str(HARNESS), str(EXTENSION), str(scenario)],
                              capture_output=True, text=True, env=full_env, timeout=120,
                              cwd=str(self.worktree))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def call(self, tool, **input):
        return {"op": "tool_call", "toolName": tool, "input": input}

    def blocks(self):
        path = self.round_dir / "worker-blocks.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class LoadAndGuardTest(ExtensionCase):
    def test_load_writes_the_ready_marker_and_registers_everything(self):
        out = self.run_steps([{"op": "registered"}])
        ready = json.loads((self.round_dir / "worker.ready").read_text())
        self.assertEqual(sorted(ready), ["at", "piVersion", "pid"])
        self.assertIsInstance(ready["pid"], int)
        self.assertEqual(out[0]["tools"], ["check", "progress", "readiness"])
        self.assertEqual(out[0]["events"], ["agent_before_settle", "before_agent_start", "tool_call"])

    def test_missing_config_writes_no_marker_and_blocks_every_call(self):
        out = self.run_steps([self.call("read", path=str(self.worktree / "a")),
                              self.call("bash", command="true")],
                             env={"CODEX_PI_WORKER_CONFIG": None})
        self.assertFalse((self.round_dir / "worker.ready").exists())
        for entry in out:
            self.assertTrue(entry["blocked"])
            self.assertIn("undecidable", entry["reason"])

    def test_invalid_config_writes_no_marker(self):
        bad = dict(self.config, forbiddenRoots="nope")
        self.run_steps([{"op": "registered"}], config=bad)
        self.assertFalse((self.round_dir / "worker.ready").exists())

    def test_forbidden_paths_are_blocked_with_the_rule_and_recorded(self):
        out = self.run_steps([
            self.call("read", path=str(self.main / "secret.txt")),
            self.call("bash", command=f"cat {self.main}/secret.txt"),
            self.call("bash", command=f"cat {self.alias}/secret.txt"),
            self.call("bash", command="ls ../main"),
            self.call("edit", path=str(self.other / "x.py")),
            self.call("write", path=str(self.alias / "secret.txt")),
            self.call("ls", path=str(self.main)),
            self.call("check", id="x", command=f"cat {self.main}/secret.txt"),
        ])
        for entry in out:
            self.assertTrue(entry["blocked"], entry)
            self.assertTrue(entry["reason"].startswith("forbidden-path: "), entry)
            self.assertIn(f"outside this task's worktree {self.worktree}", entry["reason"])
            self.assertIn("work only inside it", entry["reason"])
        self.assertIn(str(self.main / "secret.txt"), out[2]["reason"], "alias is resolved")
        records = self.blocks()
        self.assertEqual(len(records), len(out))
        self.assertEqual({r["rule"] for r in records}, {"forbidden-path"})
        self.assertEqual([r["tool"] for r in records],
                         ["read", "bash", "bash", "bash", "edit", "write", "ls", "check"])
        self.assertTrue(all(set(r) == {"at", "tool", "rule", "path"} for r in records))

    def test_symlink_inside_the_worktree_to_the_main_checkout_is_forbidden(self):
        (self.worktree / "link").symlink_to(self.main)
        out = self.run_steps([self.call("read", path="link/secret.txt"),
                              self.call("bash", command="cat link/secret.txt")])
        self.assertTrue(all(entry["blocked"] for entry in out))

    def test_allowed_work_inside_the_round_paths(self):
        out = self.run_steps([
            self.call("read", path=str(self.worktree / "README.md")),
            self.call("write", path=str(self.worktree / "new.py")),
            self.call("edit", path="relative.py"),
            self.call("bash", command=f"cd {self.worktree} && ls -la ./src application/json"),
            self.call("bash", command=f'python3 "{self.tools}/pi_check.py" --output-dir "{self.checks}" -- true'),
            self.call("write", path=str(self.checks / "note.txt")),
            self.call("grep", pattern="x"),
            self.call("ls"),
            self.call("read", path="/etc/hosts"),
        ])
        for entry in out:
            self.assertFalse(entry["blocked"], entry)
        self.assertEqual(self.blocks(), [])

    def test_write_outside_the_allowed_roots_is_blocked_but_read_is_not(self):
        out = self.run_steps([self.call("write", path=str(self.tmp / "elsewhere.txt")),
                              self.call("edit", path="/etc/hosts"),
                              self.call("read", path=str(self.tmp / "elsewhere.txt"))])
        self.assertTrue(out[0]["blocked"] and out[1]["blocked"])
        self.assertTrue(out[0]["reason"].startswith("outside-worktree: "))
        self.assertFalse(out[2]["blocked"])
        self.assertEqual([r["rule"] for r in self.blocks()], ["outside-worktree"] * 2)

    def test_bash_timeout_is_filled_and_clamped(self):
        out = self.run_steps([self.call("bash", command="true"),
                              self.call("bash", command="true", timeout=500),
                              self.call("bash", command="true", timeout=45),
                              self.call("bash", command="true", timeout=-3),
                              self.call("bash", command="true", timeout="soon")])
        self.assertEqual([entry["input"]["timeout"] for entry in out], [30, 120, 45, 30, 30])

    def test_anything_undecidable_is_blocked(self):
        (self.worktree / "a").symlink_to(self.worktree / "b")
        (self.worktree / "b").symlink_to(self.worktree / "a")
        out = self.run_steps([self.call("read", path="a/x"),
                              self.call("mystery"),
                              self.call("bash", command=123),
                              self.call("read"),
                              self.call("write", path=7)])
        for entry in out:
            self.assertTrue(entry["blocked"], entry)
            self.assertIn("undecidable", entry["reason"])
        self.assertEqual({r["rule"] for r in self.blocks()}, {"undecidable", "guard-error"})

    def test_system_prompt_section_carries_the_contract(self):
        out = self.run_steps([{"op": "before_agent_start"}, {"op": "before_agent_start"}])
        for entry in out:
            self.assertEqual(entry["sections"], {"codex_pi_worker": "CONTRACT TEXT"})


class StubMixin:
    def stub_tools(self, readiness):
        """Replace pi_task.py with a stub that records its argv and prints scripted JSON."""
        stub = self.tools / "pi_task.py"
        stub.write_text("import json, sys, pathlib\n"
                        "here = pathlib.Path(__file__).parent\n"
                        "with (here / 'calls.jsonl').open('a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                        "print(json.dumps(json.loads((here / 'stub.json').read_text())))\n")
        (self.tools / "stub.json").write_text(json.dumps(readiness))

    def stub_calls(self):
        path = self.tools / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class NativeToolsTest(StubMixin, ExtensionCase):
    PY = f"{sys.executable}"

    def check(self, **params):
        return self.run_steps([{"op": "tool", "name": "check", "params": params}])[0]

    def test_receipt_is_byte_compatible_with_a_direct_pi_check_run(self):
        command = f"{self.PY} -c \"print('ok')\""
        result = self.check(id="same", command=command, timeoutSeconds=30)
        self.assertNotIn("isError", result)
        direct_dir = self.tmp / "direct"
        direct_dir.mkdir()
        subprocess.run([sys.executable, str(self.tools / "pi_check.py"), "--output-dir", str(direct_dir),
                        "--id", "same", "--timeout-seconds", "30", "--", sys.executable, "-c",
                        "print('ok')"], cwd=str(self.worktree), capture_output=True, check=True)
        ours = json.loads(next(self.checks.glob("same-*.json")).read_text()) \
            if list(self.checks.glob("same-*.json")) else None
        theirs = json.loads(next(direct_dir.glob("*.json")).read_text()) \
            if list(direct_dir.glob("*.json")) else None
        if ours is None:  # receipt naming is pi_check's business: take the only JSON file
            ours = json.loads(next(p for p in self.checks.iterdir() if p.suffix == ".json"
                                   and not p.name.endswith(".running")).read_text())
            theirs = json.loads(next(p for p in direct_dir.iterdir() if p.suffix == ".json").read_text())
        volatile = {"started_at", "ended_at", "deadline_at", "running_marker", "log"}
        self.assertEqual({k: v for k, v in ours.items() if k not in volatile},
                         {k: v for k, v in theirs.items() if k not in volatile})
        self.assertEqual(ours["argv"], [sys.executable, "-c", "print('ok')"])
        self.assertEqual(result["details"]["exit_code"], 0)
        self.assertIn("receipt=", result["content"][0]["text"])

    def test_failure_returns_the_log_tail_and_is_an_error(self):
        result = self.check(id="bad", command=f"{self.PY} -c \"print('boom'); raise SystemExit(3)\"")
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("exit=3", text)
        self.assertIn("log_tail:", text)
        self.assertIn("boom", text)
        self.assertEqual(result["details"]["exit_code"], 3)

    def test_timeout_is_an_error_and_the_deadline_is_clamped(self):
        result = self.check(id="slow", timeoutSeconds=1,
                            command=f"{self.PY} -c \"import time; time.sleep(30)\"")
        self.assertTrue(result["isError"])
        self.assertTrue(result["details"]["timed_out"])
        self.assertIn("TIMED_OUT", result["content"][0]["text"])
        capped = dict(self.config, checkTimeoutSeconds=5)
        out = self.run_steps([{"op": "tool", "name": "check",
                               "params": {"id": "cap", "timeoutSeconds": 9999, "command": f"{self.PY} -c pass"}}],
                             config=capped)
        receipt = json.loads(next(p for p in self.checks.iterdir() if p.name.startswith("cap")
                                  and p.suffix == ".json").read_text())
        self.assertLessEqual(receipt["deadline_at"] - receipt["started_at"], 5.5)
        self.assertNotIn("isError", out[0])

    def test_bad_arguments_are_errors_not_exceptions(self):
        for params in ({"id": "bad id!", "command": "true"}, {"id": "x", "command": "echo 'open"},
                       {"id": "x", "command": "   "}, {"id": "x", "command": "true", "watchPath": "/x"}):
            result = self.check(**params)
            self.assertTrue(result["isError"], params)

    def test_progress_and_readiness_call_the_frozen_helper(self):
        self.stub_tools({"ok": True, "status": "not_ready", "readinessReason": "x",
                         "coverage": {"required": 1}, "gaps": [{"id": "A1", "status": "missing"}]})
        out = self.run_steps([
            {"op": "tool", "name": "progress", "params": {
                "activity": "checking", "step": "s", "next": "n", "completedCriteria": ["A1"],
                "evidenceRefs": ["r.md"]}},
            {"op": "tool", "name": "readiness", "params": {}}])
        self.assertNotIn("isError", out[0])
        self.assertNotIn("isError", out[1])
        self.assertIn("A1:missing", out[1]["content"][0]["text"])
        progress, readiness = self.stub_calls()
        self.assertEqual(progress[:7], ["progress", "--repo", str(self.main), "--task", "T", "--round", "1"])
        self.assertIn("--completed-criteria", progress)
        self.assertEqual(progress[progress.index("--evidence-ref") + 1], "r.md")
        self.assertEqual(readiness[0], "readiness")


class SettleTest(StubMixin, ExtensionCase):
    def settle_config(self):
        return dict(self.config, phase=True, settleQuotaPath=str(self.quota))

    def eligible(self):
        return {"settle": {"eligible": True, "reason": "missing_checks",
                           "missing": [{"id": "A1", "command": "make test", "passCondition": "exit 0"}]}}

    def test_continues_once_then_never_again(self):
        self.stub_tools(self.eligible())
        out = self.run_steps([{"op": "settle"}, {"op": "settle"}], config=self.settle_config())
        first = out[0]["result"]
        self.assertTrue(first["continue"])
        entry = first["entries"][0]
        self.assertEqual((entry["type"], entry["customType"], entry["display"]),
                         ("custom_message", "codex-pi-settle", False))
        self.assertIn("A1: make test (pass: exit 0)", entry["content"])
        self.assertIsNone(out[1]["result"], "the persisted quota is one use per phase")
        self.assertTrue(self.quota.exists())

    def test_existing_quota_file_blocks_the_continuation(self):
        self.stub_tools(self.eligible())
        self.quota.parent.mkdir(parents=True, exist_ok=True)
        self.quota.write_text("{}")
        out = self.run_steps([{"op": "settle"}], config=self.settle_config())
        self.assertIsNone(out[0]["result"])

    def test_not_eligible_no_phase_and_other_outcomes_never_continue(self):
        self.stub_tools({"settle": {"eligible": False, "reason": "required_check_not_missing", "missing": []}})
        out = self.run_steps([{"op": "settle"}], config=self.settle_config())
        self.assertIsNone(out[0]["result"])
        self.assertFalse(self.quota.exists())
        self.stub_tools(self.eligible())
        out = self.run_steps([{"op": "settle"}], config=self.config)  # phase false
        self.assertIsNone(out[0]["result"])
        out = self.run_steps([{"op": "settle", "outcome": "error"}], config=self.settle_config())
        self.assertIsNone(out[0]["result"])
        self.assertFalse(self.quota.exists())

    def test_helper_failure_is_not_a_continuation(self):
        (self.tools / "pi_task.py").write_text("raise SystemExit(5)\n")
        out = self.run_steps([{"op": "settle"}], config=self.settle_config())
        self.assertIsNone(out[0]["result"])
        self.assertFalse(self.quota.exists())


if __name__ == "__main__":
    unittest.main()
