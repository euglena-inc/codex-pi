"""Check log tails, forbidden checkout roots and the short decide hint. Offline."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import (RUNTIME, Repo, base_env, cleanup_repos, default_config, run_cli)

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402
import pi_task  # noqa: E402

CHECK = RUNTIME / "pi_check.py"


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


class ForbiddenCheckoutRootsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-forbid-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name).resolve()

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
