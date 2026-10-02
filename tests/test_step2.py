"""Step 2: contract once per session, check log tails, forbidden checkouts,
scope-escape readiness and the short decide hint. Offline; no model or network."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime_helpers import (RUNTIME, Repo, base_env, cleanup_repos, default_config, run_cli)

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402
import pi_summary  # noqa: E402
import pi_task  # noqa: E402
from pi_command_guard import CommandGuard, forbidden_reference  # noqa: E402

CHECK = RUNTIME / "pi_check.py"


class ContractOncePerSessionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-s2-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.task_dir = self.tmp / "task"
        (self.task_dir / "rounds").mkdir(parents=True)
        self.task = {"task": "T1", "taskDir": str(self.task_dir), "readOnly": False,
                     "model": "deepseek/deepseek-flash", "thinking": "max",
                     "timeoutSeconds": 600, "constraints": [], "checks": []}

    def record(self, key):
        return ({"contractSha256": key, "baselineCommit": "a" * 40,
                 "contract": {"phaseId": "P", "baseline": "HEAD", "designRef": "d.md",
                              "designSha256": "0" * 64, "budgetSeconds": 60, "scope": ["."],
                              "commandTimeoutSeconds": 30, "acceptanceItems": [],
                              "autonomousRepair": [], "escalateWhen": []}}, None)

    def write_round(self, number, key, prior=None):
        with mock.patch.object(pi_task, "read_phase_record", return_value=self.record(key)):
            brief, contract = pi_task.compose_round_texts(self.task, number, "Do it.", prior)
        round_dir = self.task_dir / "rounds" / str(number)
        round_dir.mkdir(parents=True)
        (round_dir / "brief.md").write_text(brief, encoding="utf-8")
        (round_dir / "contract.md").write_text(contract, encoding="utf-8")
        return brief, contract

    def test_short_header_after_round_one_and_full_again_when_the_hash_changes(self):
        brief1, contract1 = self.write_round(1, "h1")
        self.assertIn("worker_contract=full contract_hash=h1", brief1)
        self.assertIn("User directive", brief1)
        prior = {"round": 1, "state": "completed", "exitCode": 0, "endHead": "b" * 40}
        brief2, contract2 = self.write_round(2, "h1", prior)
        appended = brief2.split("\n---\n", 1)[1]
        self.assertLessEqual(len(appended.splitlines()), 15)
        self.assertIn("worker_contract=short contract_hash=h1", brief2)
        self.assertIn("Same worker contract as round 1; it still applies", brief2)
        self.assertIn(str(self.task_dir / "rounds" / "1" / "brief.md"), brief2)
        self.assertIn("pi_check.py", brief2)
        self.assertIn("forbidden-path", brief2)
        self.assertNotIn("User directive", brief2, "the short header never repeats the contract")
        self.assertNotIn("Model policy", brief2)
        self.assertLess(len(brief2), len(brief1) / 2)
        # The full contract is still written for audit every round.
        self.assertEqual(contract2.splitlines()[0], contract1.splitlines()[0])
        self.assertIn("User directive", contract2)
        self.assertIn("worker_contract=full contract_hash=h1 round=2", contract2)
        brief3, _ = self.write_round(3, "h2", dict(prior, round=2))
        self.assertIn("worker_contract=full contract_hash=h2", brief3)
        self.assertIn("User directive", brief3, "a changed contract hash restores the full text")
        brief4, _ = self.write_round(4, "h2")
        self.assertIn("Same worker contract as round 3", brief4)
        brief5, _ = self.write_round(5, "h1")
        self.assertIn("User directive", brief5, "going back to another hash is also a change")
        for number in range(1, 6):
            self.assertTrue((self.task_dir / "rounds" / str(number) / "contract.md").is_file())

    def test_full_contract_carries_the_must_ask_and_forbidden_rules(self):
        _, contract = self.write_round(1, "h1")
        self.assertIn("choose the conservative reading and report it as a spec gap", contract)
        self.assertIn("forbidden-path", contract)


class ContractCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-s2cli-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        cleanup_repos()

    def test_continue_writes_contract_md_and_a_short_brief(self):
        repo = Repo(self.tmp, name="repo", config=default_config())
        worktree = repo.worktree("wt")
        env = base_env(PI_DOUBLE_MODE="ok")
        repo.start("two", worktree, env=env)
        repo.wait_terminal("two", env=env)
        repo.continue_task("two", env=env)
        repo.wait_terminal("two", env=env, round=2)
        for number in (1, 2):
            self.assertTrue((repo.task_dir("two") / "rounds" / str(number) / "contract.md").is_file())
        brief1 = (repo.task_dir("two") / "rounds" / "1" / "brief.md").read_text()
        brief2 = (repo.task_dir("two") / "rounds" / "2" / "brief.md").read_text()
        self.assertIn("User directive", brief1)
        self.assertNotIn("User directive", brief2)
        self.assertIn("worker_contract=short", brief2)
        self.assertIn("User directive", (repo.task_dir("two") / "rounds" / "2" / "contract.md").read_text())


class LogTailTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-tail-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        cleanup_repos()

    def run_check(self, code: str):
        repo = Repo(self.tmp, name="repo")
        out = self.tmp / "checks"
        out.mkdir(exist_ok=True)
        proc = subprocess.run([sys.executable, str(CHECK), "--output-dir", str(out), "--id", "T1",
                               "--timeout-seconds", "60", "--", sys.executable, "-c", code],
                              cwd=str(repo.root), capture_output=True, text=True, timeout=60)
        return proc, json.loads(proc.stdout), out

    def test_failure_adds_bounded_log_tail_and_leaves_the_receipt_unchanged(self):
        code = ("import sys\n"
                "for i in range(100): print('line %03d é' % i)\n"
                "sys.exit(3)")
        proc, data, out = self.run_check(code)
        self.assertEqual(proc.returncode, 3)
        tail = data["log_tail"]
        lines = tail.splitlines()
        self.assertLessEqual(len(lines), 20)
        self.assertLessEqual(len(tail.encode("utf-8")), 2000)
        self.assertEqual(lines[-1], "line 099 é")
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)
        receipt = json.loads(Path(data["receipt"]).read_text())
        self.assertNotIn("log_tail", receipt)

    def test_byte_cap_wins_over_line_cap_and_invalid_utf8_is_replaced(self):
        code = ("import sys\n"
                "sys.stdout.buffer.write(b'\\xff\\xfe bad\\n' + ('x' * 150 + '\\n').encode() * 19"
                " + 'é'.encode() * 900)\n"
                "sys.exit(1)")
        _proc, data, _ = self.run_check(code)
        tail = data["log_tail"]
        self.assertLessEqual(len(tail.encode("utf-8")), 2000)
        self.assertLessEqual(len(tail.splitlines()), 20)

    def test_replacement_characters_cannot_exceed_the_byte_cap(self):
        raw = b"\xff" * 5000
        tail = sys.modules.get("pi_check") or __import__("pi_check")
        self.assertLessEqual(len(tail.log_tail(raw).encode("utf-8")), 2000)

    def test_success_has_no_log_tail(self):
        proc, data, _ = self.run_check("print('ok')")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("log_tail", data)

    def test_timeout_has_a_log_tail(self):
        repo = Repo(self.tmp, name="repo-timeout")
        out = self.tmp / "checks-timeout"
        out.mkdir()
        proc = subprocess.run([sys.executable, str(CHECK), "--output-dir", str(out), "--id", "T2",
                               "--timeout-seconds", "1", "--", sys.executable, "-c",
                               "import time; print('start', flush=True); time.sleep(30)"],
                              cwd=str(repo.root), capture_output=True, text=True, timeout=60)
        data = json.loads(proc.stdout)
        self.assertTrue(data["timed_out"])
        self.assertIn("start", data["log_tail"])


class ForbiddenCheckoutTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-forbid-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.main = self.tmp / "main"
        self.wt = self.tmp / "wt"
        self.other = self.tmp / "other-wt"
        for path in (self.main, self.wt, self.other):
            path.mkdir()
        (self.main / "secret.txt").write_text("x")
        self.tools = self.main / ".git" / "codex-pi" / "tasks" / "T" / "tools"
        self.checks = self.main / ".git" / "codex-pi" / "tasks" / "T" / "rounds" / "1" / "round.checks"
        self.tools.mkdir(parents=True)
        self.checks.mkdir(parents=True)
        self.alias = self.tmp / "alias"
        self.alias.symlink_to(self.main)
        self.forbidden = [str(self.main), str(self.other)]
        self.allowed = [str(self.wt), str(self.tools), str(self.checks)]

    def hit(self, command):
        return forbidden_reference(command, self.forbidden, self.allowed, str(self.wt))

    def test_references_resolve_through_symlinks(self):
        self.assertEqual(self.hit(f"cat {self.main}/secret.txt"), str(self.main / "secret.txt"))
        self.assertEqual(self.hit(f"cat {self.alias}/secret.txt"), str(self.main / "secret.txt"))
        self.assertTrue(self.hit(f"git -C {self.other} status"))
        self.assertTrue(self.hit(f"ls ../main"), "relative references resolve against the worktree")
        (self.wt / "link").symlink_to(self.main)
        self.assertTrue(self.hit("cat link/secret.txt"))

    def test_allowed_paths_are_never_forbidden(self):
        self.assertIsNone(self.hit(f"cat {self.wt}/a.py && ls {self.wt}"))
        self.assertIsNone(self.hit(f'python3 "{self.tools}/pi_check.py" --output-dir "{self.checks}" -- true'))
        self.assertIsNone(self.hit("echo hi; ls -la ./src application/json"))
        self.assertIsNone(self.hit("python3 -c 'print(1)'"))

    def test_guard_terminates_only_the_forbidden_command(self):
        log = self.tmp / "round.jsonl"
        log.touch()
        guard = CommandGuard(self.tmp, 900001, default=600, ceiling=600,
                             forbidden=self.forbidden, allowed=self.allowed, cwd=self.wt)
        rows = {900002: {"pid": 900002, "parent": 900001, "group": 900002, "birth": "fixed",
                         "createdAt": 100, "comm": "/bin/bash"}}

        def emit(**values):
            with log.open("a") as stream:
                stream.write(json.dumps(values) + "\n")

        emit(type="tool_execution_start", toolName="bash", toolCallId="ok",
             args={"command": f"cat {self.wt}/a.py"})
        with mock.patch("pi_command_guard.os.killpg") as kill:
            guard.scan(now=100, rows=rows)
            kill.assert_not_called()
        emit(type="tool_execution_end", toolCallId="ok", isError=False)
        emit(type="tool_execution_start", toolName="bash", toolCallId="bad",
             args={"command": f"cat {self.alias}/secret.txt"})
        with mock.patch("pi_command_guard.os.killpg") as kill:
            guard.scan(now=100, rows=rows)
            kill.assert_called_once_with(900002, signal.SIGTERM)
        state = json.loads((self.tmp / "command-guard.json").read_text())
        self.assertEqual(state["active"]["status"], "forbidden_path_terminated")
        self.assertEqual(state["active"]["rule"], "forbidden-path")
        history = [json.loads(line) for line in
                   (self.tmp / "command-guard-signals.jsonl").read_text().splitlines()]
        self.assertEqual(history[-1]["rule"], "forbidden-path")
        self.assertEqual(history[-1]["effect"], "unconfirmed")
        emit(type="tool_execution_end", toolCallId="bad", isError=True)
        guard.scan(now=101, rows=rows)
        self.assertEqual(json.loads((self.tmp / "command-guard.json").read_text())
                         ["events"][-1]["status"], "forbidden_path_terminated")

    def test_ambiguous_ownership_never_signals_even_for_a_forbidden_command(self):
        log = self.tmp / "round.jsonl"
        log.write_text(json.dumps({"type": "tool_execution_start", "toolName": "bash",
                                   "toolCallId": "bad",
                                   "args": {"command": f"cat {self.main}/secret.txt"}}) + "\n")
        guard = CommandGuard(self.tmp, 900001, forbidden=self.forbidden, allowed=self.allowed,
                             cwd=self.wt)
        rows = {900002: {"pid": 900002, "parent": 55, "group": 900002, "birth": "x",
                         "createdAt": 100, "comm": "bash"}}
        with mock.patch("pi_command_guard.os.killpg") as kill:
            guard.scan(now=100, rows=rows)
            kill.assert_not_called()

    def test_registered_worktrees_are_forbidden_and_the_round_paths_allowed(self):
        repo = Repo(self.tmp, name="real")
        wt = repo.worktree("a")
        other = repo.worktree("b")
        task_dir = repo.task_dir("T")
        forbidden, allowed = pi_task.forbidden_checkouts(wt, task_dir, 1)
        self.assertIn(os.path.realpath(str(repo.root)), forbidden)
        self.assertIn(os.path.realpath(str(other)), forbidden)
        self.assertNotIn(os.path.realpath(str(wt)), forbidden)
        self.assertIn(os.path.realpath(str(wt)), allowed)
        self.assertIn(str(task_dir / "tools"), allowed)
        self.assertIn(str(task_dir / "rounds" / "1" / "round.checks"), allowed)
        cleanup_repos()


class ScopeEscapeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-escape-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def log(self, *calls) -> Path:
        run = self.tmp / "task" / "rounds" / "1"
        run.mkdir(parents=True, exist_ok=True)
        path = run / "round.jsonl"
        path.write_text("".join(json.dumps({"type": "tool_execution_start", "toolName": tool,
                                            "toolCallId": str(i), "args": {"path": target}}) + "\n"
                                for i, (tool, target) in enumerate(calls)), encoding="utf-8")
        return path

    def test_summary_separates_write_edit_escapes_from_reads(self):
        wt = self.tmp / "wt"
        wt.mkdir()
        path = self.log(("write", "/elsewhere/a.py"), ("edit", "/elsewhere/b.py"),
                        ("read", "/elsewhere/c.py"), ("write", str(wt / "ok.py")),
                        ("read", str(self.tmp / "task" / "tools" / "pi_check.py")))
        data = pi_summary.summarize(path, wt, path.parent, None)
        escape = data["scope_escape"]
        self.assertEqual(escape["write_edit_count"], 2)
        self.assertEqual(escape["read_count"], 1)
        self.assertEqual(pi_summary.bounded(data)["scope_escape"], escape)

    def snapshot(self, escape):
        from test_progress_echo import REAL_CONTRACT
        self.count = getattr(self, "count", 0) + 1
        repo = Repo(self.tmp, name=f"repo{self.count}")
        head = subprocess.check_output(["git", "-C", str(repo.root), "rev-parse", "HEAD"],
                                       text=True).strip()
        task_dir = self.tmp / f"t{self.count}"
        round_dir = task_dir / "rounds" / "1"
        round_dir.mkdir(parents=True)
        contract = dict(REAL_CONTRACT)
        (task_dir / "phase.json").write_text(json.dumps({
            "schemaVersion": 1, "contract": contract,
            "contractSha256": pi_task.contract_hash(contract), "baselineCommit": head,
            "createdAt": 1}), encoding="utf-8")
        (task_dir / "task.json").write_text(json.dumps({"worktree": str(repo.root)}),
                                            encoding="utf-8")
        if escape is not None:
            (round_dir / "round.summary.json").write_text(json.dumps({"scope_escape": escape}),
                                                          encoding="utf-8")
        state = {"state": "completed", "exitCode": 0, "timedOut": False, "cancelled": False,
                 "endHead": head, "startHead": head}
        candidate = pi_task.normalize_candidate(state)
        checks = pi_task._scan_checks(round_dir / "round.checks")
        return pi_task.build_phase_snapshot(task_dir, {"worktree": str(repo.root)}, 1,
                                            state=state, checks=checks, candidate=candidate)

    def tearDown(self):
        cleanup_repos()

    def test_write_escape_blocks_readiness_but_read_escape_does_not(self):
        clean = self.snapshot(None)
        self.assertNotIn("SCOPE_ESCAPE", json.dumps(clean["readiness"]))
        blocked = self.snapshot({"write_edit_count": 2, "write_edit": ["line 1: write /x"],
                                 "read_count": 0})
        self.assertEqual(blocked["readiness"]["status"], "not_ready")
        self.assertTrue(blocked["readiness"]["reason"].startswith("SCOPE_ESCAPE"))
        self.assertIn("SCOPE_ESCAPE", [gap["id"] for gap in blocked["gaps"]])
        reads = self.snapshot({"write_edit_count": 0, "write_edit": [], "read_count": 4})
        self.assertNotIn("SCOPE_ESCAPE", json.dumps(reads["readiness"]))
        self.assertTrue(any("4 read tool call(s)" in note for note in reads["notes"]))
        self.assertNotIn("SCOPE_ESCAPE", [gap["id"] for gap in reads["gaps"]])
        self.assertEqual(reads["readiness"]["status"], clean["readiness"]["status"])
        self.assertEqual(reads["readiness"]["reason"], clean["readiness"]["reason"])


class DecideHintTest(unittest.TestCase):
    def test_hint_references_the_card_and_stays_within_200_bytes(self):
        card = pi_board._new_card("t1", "11111111-2222-3333-4444-555555555555", "T", "G", "b.md",
                                  None, "/r", "/c", "/w", pi_board.TRANSPORT_CLI_QUEUE,
                                  "/bin/true", 1)
        head = "0123456789abcdef0123456789abcdef01234567"
        event = pi_board.add_event(card, "review_required", 2, "fp", "s",
                                   {"round": 2, "head": head}, {}, "q", 1)
        event["phaseId"] = "P1"
        event["contractHash"] = "c" * 64
        hint = pi_board._decide_hint(card["repo"], card["taskId"], event)
        self.assertLessEqual(len(hint.encode("utf-8")), 200)
        self.assertNotIn(head, hint)
        self.assertNotIn("c" * 64, hint)
        self.assertNotIn(event["id"], hint)
        text, _ = pi_board.build_packet(card, [event])
        self.assertIn(head, text)
        self.assertIn("contract=" + "c" * 64, text)
        self.assertIn(event["id"], text)
        self.assertLessEqual(len(text.encode("utf-8")), 1200)


if __name__ == "__main__":
    unittest.main()
