"""Delivery card (<= 1200 UTF-8 bytes), compact CLI output and `result --full`.

Everything runs offline against synthetic boards and the repository's Pi
double. No model, network or real Codex CLI is invoked.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import (RUNTIME, Repo, base_env, cleanup_repos, cli_json, default_config,
                             run_cli)

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402
import pi_task  # noqa: E402

THREAD = "11111111-2222-3333-4444-555555555555"
HEAD = "0123456789abcdef0123456789abcdef01234567"
CONTRACT = "c" * 64
CARD_LIMIT = 1200


def nbytes(text: str) -> int:
    return len(text.encode("utf-8"))


class DeliveryCardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-card-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.round_dir = self.tmp / "rounds" / "2"
        self.round_dir.mkdir(parents=True)

    def make_card(self, repo="/repo", task="card-task") -> dict:
        return pi_board._new_card(task, THREAD, "Title", "Goal", "brief.md", None, repo,
                                  "/common", "/worktree", pi_board.TRANSPORT_CLI_QUEUE,
                                  "/bin/true", 1)

    def write_report(self, text: str) -> None:
        (self.round_dir / "round.summary.json").write_text(
            json.dumps({"final_text": text}), encoding="utf-8")

    def review_event(self, card, items, fingerprint="fp", kind="review_required", head=HEAD):
        event = pi_board.add_event(
            card, kind, 2, fingerprint, "phase P is delivery-ready", {"round": 2, "head": head},
            {"stateRef": str(self.round_dir / "round.state.json"),
             "briefRef": str(self.round_dir / "brief.md")},
            "Review the exact candidate.", 1)
        event["phaseId"] = "P1"
        event["contractHash"] = CONTRACT
        event["delivery"] = {"items": items, "more": 0}
        return event

    # ------------------------------------------------------------------
    def test_limit_constant_is_1200_bytes(self):
        self.assertEqual(pi_board.MAX_PACKET_CHARS, CARD_LIMIT)

    def test_card_has_every_item_line_full_sha_policy_report_and_one_hint(self):
        card = self.make_card()
        items = [
            {"id": "A1", "st": "covered", "exit": 0,
             "counts": {"run": 12, "pass": 12, "fail": 0, "skip": 0}},
            {"id": "A2", "st": "failed", "exit": 3, "counts": None},
            {"id": "A3", "st": "missing", "exit": None, "counts": None},
            {"id": "A4", "st": "unknown", "exit": None, "counts": None},
        ]
        event = self.review_event(card, items)
        self.write_report("完成。" * 400)
        text, included = pi_board.build_packet(card, [event])
        self.assertEqual(included, [event])
        self.assertLessEqual(nbytes(text), CARD_LIMIT)
        self.assertIn(HEAD, text, "the full candidate SHA is on the card")
        self.assertIn(event["id"], text)
        self.assertIn("review_required", text)
        self.assertIn("round=2", text)
        self.assertIn("task=card-task", text)
        for item in items:
            self.assertIn(pi_task.acceptance_line(item["id"], item["st"], item["exit"],
                                                  item["counts"]), text.splitlines())
        self.assertIn("A1 exit=0 run=12 pass=12 fail=0 skip=0", text)
        self.assertIn("A2 exit=3 [failed]", text)
        self.assertIn("A3 missing", text)
        self.assertIn("A4 unknown", text)
        self.assertRegex(text, r"\npolicy=0/\d pi\n", "review policy as failed/limit owner")
        report_lines = [line for line in text.splitlines() if line.startswith("report=")]
        self.assertEqual(len(report_lines), 1)
        self.assertTrue(report_lines[0].endswith("..."), "the report is truncated to fit")
        self.assertEqual(sum(1 for line in text.splitlines() if line.startswith("decide=")), 1)
        self.assertNotIn(str(self.round_dir), text, "no absolute evidence paths on the card")
        self.assertNotIn("brief.md", text)
        text.encode("utf-8").decode("utf-8")

    def test_short_report_is_kept_whole(self):
        card = self.make_card()
        event = self.review_event(card, [])
        self.write_report("all done")
        text, _ = pi_board.build_packet(card, [event])
        self.assertIn("report=all done", text)

    def test_many_items_are_capped_and_card_stays_within_the_limit(self):
        card = self.make_card()
        items = [{"id": f"ITEM-{i}", "st": "covered", "exit": 0,
                  "counts": {"run": 5, "pass": 5, "fail": 0, "skip": 0}} for i in range(40)]
        event = self.review_event(card, items)
        event["delivery"] = {"items": items[:pi_board.MAX_CARD_ITEMS],
                             "more": len(items) - pi_board.MAX_CARD_ITEMS}
        text, included = pi_board.build_packet(card, [event])
        self.assertEqual(included, [event])
        self.assertLessEqual(nbytes(text), CARD_LIMIT)
        self.assertIn("+32 more items (show)", text)

    def test_multi_event_overflow_stays_bounded_and_keeps_the_drain_line(self):
        card = self.make_card()
        items = [{"id": "A1", "st": "covered", "exit": 0,
                  "counts": {"run": 3, "pass": 3, "fail": 0, "skip": 0}}]
        events = [self.review_event(card, items, fingerprint=f"fp-{i}",
                                    head=f"{i:040x}") for i in range(5)]
        self.write_report("r" * 3000)
        text, included = pi_board.build_packet(card, events)
        self.assertLessEqual(nbytes(text), CARD_LIMIT)
        self.assertTrue(included)
        self.assertLess(len(included), 5)
        self.assertIn(f"+{5 - len(included)} pending event(s)", text)
        self.assertIn("same turn", text)
        self.assertIn("pi_board.py show", text)
        for event in included:
            self.assertIn(event["id"], text)
        for event in events[len(included):]:
            self.assertNotIn(event["id"], text, "an omitted event is never claimed")

    def test_oversized_event_falls_back_but_keeps_the_exact_event_id(self):
        # A task identity this long makes even a bare event block exceed the card.
        card = self.make_card(repo="/r/" + "x" * 1500, task="t" + "y" * 40)
        event = self.review_event(card, [])
        text, included = pi_board.build_packet(card, [event])
        self.assertEqual(included, [event], "an oversized event is not silently dropped")
        self.assertIn(event["id"], text)
        self.assertLessEqual(nbytes(text), CARD_LIMIT)
        # Many pending events with the same problem still produce one bounded fallback.
        more = [self.review_event(card, [], fingerprint=f"o{i}") for i in range(3)]
        text, included = pi_board.build_packet(card, more)
        self.assertEqual(included, [more[0]])
        self.assertIn(more[0]["id"], text)
        self.assertLessEqual(nbytes(text), CARD_LIMIT)

    def test_non_review_event_names_the_anomaly_question(self):
        card = self.make_card()
        event = pi_board.add_event(card, pi_board.PROGRESS_EVENT_KIND, 2, "fp-a",
                                   "check 'A1' has an unresolved failure observed for 200s",
                                   {"round": 2, "head": HEAD}, {},
                                   "Anomaly: choose pause, cancel or let Pi repair.", 1)
        text, _ = pi_board.build_packet(card, [event])
        self.assertIn("ask=Anomaly: choose pause, cancel or let Pi repair.", text)
        self.assertIn("note=check 'A1' has an unresolved failure", text)
        self.assertLessEqual(nbytes(text), CARD_LIMIT)

    def test_legacy_items_come_from_the_latest_receipt_per_check(self):
        status = {"phase": None, "checks": {"receipts": {"recent": [
            {"id": "unit", "exitCode": 1, "testCounts": None},
            {"id": "unit", "exitCode": 0, "testCounts": {"run": 2, "pass": 2, "fail": 0,
                                                         "skip": 0}},
            {"id": "lint", "exitCode": None, "testCounts": None}]}}}
        info = pi_board._delivery_info(status)
        self.assertEqual([(i["id"], i["st"], i["exit"]) for i in info["items"]],
                         [("unit", "covered", 0), ("lint", "unknown", None)])


class CompactCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-compact-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = Repo(self.tmp, name="repo", config=default_config())
        self.worktree = self.repo.worktree("wt")
        self.env = base_env(PI_DOUBLE_MODE="ok")
        self.repo.start("compact", self.worktree, env=self.env)
        self.repo.wait_terminal("compact", env=self.env)

    def tearDown(self):
        cleanup_repos()

    def single_line(self, proc):
        out = proc.stdout
        self.assertTrue(out.endswith("\n"))
        self.assertNotIn("\n", out[:-1], "stdout must be one JSON line")
        self.assertNotIn('": ', out, "compact separators, no indent")
        return json.loads(out)

    def test_every_cli_prints_single_line_json(self):
        base = ("--repo", str(self.repo.root), "--task", "compact")
        for args in (("project", "--repo", str(self.repo.root)),
                     ("status", *base), ("result", *base), ("result", *base, "--full"),
                     ("phase-status", *base)):
            with self.subTest(args=args[0]):
                self.single_line(run_cli(*args, env=self.env))

    def test_board_commands_print_single_line_json(self):
        script = RUNTIME / "pi_board.py"
        args = ["--repo", str(self.repo.root), "--task", "compact"]
        for command in (["register", *args, "--thread", THREAD, "--title", "T", "--goal", "G"],
                        ["show", *args]):
            proc = subprocess.run([sys.executable, str(script), *command], capture_output=True,
                                  text=True, env=self.env, timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("\n", proc.stdout[:-1])
            json.loads(proc.stdout)

    def test_result_is_compact_by_default_and_full_keeps_the_old_shape(self):
        compact = self.single_line(run_cli("result", "--repo", str(self.repo.root),
                                           "--task", "compact", env=self.env))
        full = self.single_line(run_cli("result", "--repo", str(self.repo.root),
                                        "--task", "compact", "--full", env=self.env))
        self.assertEqual(compact["view"], "compact")
        for key in ("state", "execution", "acceptance", "round", "usage", "checks", "final",
                    "notes", "candidateHead"):
            self.assertIn(key, compact)
        for dropped in ("summaryText", "rounds", "summary"):
            self.assertNotIn(dropped, compact)
        self.assertLessEqual(len(compact["final"]), 1200)
        self.assertEqual(compact["state"], "completed")
        self.assertEqual(compact["acceptance"], "not_verified")
        self.assertEqual(sum(1 for note in compact["notes"] if "never acceptance" in note), 1)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(full)))
        # --full is the previous shape.
        for key in ("summaryText", "rounds", "summary", "evidence", "session", "latestRound",
                    "usageComplete", "execution", "notes", "runtimeVersion"):
            self.assertIn(key, full)
        self.assertNotIn("view", full)
        self.assertEqual(full["rounds"][0]["evidence"]["roundDir"], full["evidence"]["roundDir"])
        self.assertIn("final_excerpt", full["summary"])

    def test_status_drops_fixed_notes(self):
        status = cli_json("status", "--repo", str(self.repo.root), "--task", "compact",
                          env=self.env)
        self.assertFalse([note for note in status["notes"] if "exit 0" in note])

    def test_wait_instruction_does_not_invite_repeated_waits(self):
        data = cli_json("wait", "--repo", str(self.repo.root), "--task", "compact",
                        "--timeout-ms", "100", env=self.env)
        text = json.dumps(data["wait"])
        self.assertNotIn("repeat", text)
        self.assertIn("do not call wait again", text)


if __name__ == "__main__":
    unittest.main()
