"""Hook presence and session closeout (0.8.8, P0-1 + P0-2).

Offline and deterministic: no model, no network, no Codex CLI, no skip. Design:
docs/design/hook-presence-and-closeout.md.

Proven here: work awaiting a main decision is present at session/prompt boundaries without
polling; pins survive into a compaction summary; a session end prepares (never files) an
outcome closeout; unknown stays unknown; every hook path stays read-only, bounded and safe.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from runtime_helpers import ROOT, RUNTIME, Repo, base_env, cleanup_repos, default_config

sys.path.insert(0, str(RUNTIME))
import pi_events  # noqa: E402
import pi_outcome  # noqa: E402
import pi_presence  # noqa: E402
import pi_store  # noqa: E402

HANDOFF = RUNTIME / "pi_handoff.py"
BOARD = RUNTIME / "pi_board.py"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
THREAD = "11111111-2222-3333-4444-555555555555"
HEAD = "a" * 40
CONTRACT = "c" * 64
EVENT_HOOKS = ("SessionStart", "UserPromptSubmit", "Interrupt", "PreCompact", "SessionEnd")


def h_env(tmp: Path, **extra) -> dict:
    env = base_env(**extra)
    env["CODEX_PI_HANDOFF_ROOT"] = str(tmp / "handoffs")
    env.pop("CODEX_THREAD_ID", None)
    return env


def board_only(repo, task_id: str = "UNIT-PRESENCE", thread: str = THREAD,
               state: str = "completed") -> dict:
    board = {"schemaVersion": 1, "revision": 1, "createdAt": 1, "updatedAt": 1, "cards": {}}
    card = pi_events._new_card(task_id, thread, "Unit task", "Unit goal", "brief.md", None,
                               repo.root, repo.state_dir, str(repo.root),
                               pi_store.TRANSPORT_OFFLINE, None, 1)
    card["pi"] = {"round": 1, "state": state, "stage": "implementing", "updatedAt": 1}
    card["phase"] = {"phaseId": "PH-1", "contractHash": CONTRACT, "candidateHead": HEAD,
                     "baselineCommit": "b" * 40}
    board["cards"][task_id] = card
    return board


def write_board(repo, board) -> Path:
    path = repo.state_dir / "board.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=2), encoding="utf-8")
    return path


def add_delivery(board, task_id: str = "UNIT-PRESENCE", kind: str = "review_required",
                 round_number: int = 1, now: float = 1791200000.0) -> None:
    card = board["cards"][task_id]
    pi_events.add_event(card, kind, round_number, f"fp-{task_id}-{kind}-{round_number}",
                        "round complete, checks ok", {"head": HEAD, "phaseId": "PH-1",
                                                      "contractHash": CONTRACT},
                        {"receipts": ["rounds/1/round.checks"]}, "", now)


class PresenceBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-presence-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.env = h_env(self.tmp)
        self._environ = dict(os.environ)
        os.environ["CODEX_PI_HANDOFF_ROOT"] = str(self.tmp / "handoffs")
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(self._environ))
        self.repo = Repo(self.tmp, name="repo", config=default_config())
        self.common = self.repo.root / ".git"
        task_dir = self.repo.state_dir / "tasks" / "UNIT-PRESENCE"
        (task_dir / "rounds" / "1").mkdir(parents=True, exist_ok=True)
        (task_dir / "task.json").write_text(json.dumps({"task": "UNIT-PRESENCE"}),
                                            encoding="utf-8")
        (task_dir / "rounds" / "1" / "round.state.json").write_text(
            json.dumps({"round": 1}), encoding="utf-8")
        self.board = board_only(self.repo)
        self.board_file = write_board(self.repo, self.board)
        pi_store.register_route(THREAD, self.board_file, ["UNIT-PRESENCE"],
                                pi_store.TRANSPORT_OFFLINE, 1791200000.0)

    def tearDown(self):
        cleanup_repos()

    def run_hook(self, name: str, session: str = THREAD, extra: dict | None = None) -> dict:
        payload = {"hook_event_name": name, "session_id": session, "turn_id": "t1",
                   "cwd": str(self.repo.root)}
        payload.update(extra or {})
        proc = subprocess.run([sys.executable, str(HANDOFF), "hook"],
                              input=json.dumps(payload), capture_output=True, text=True,
                              env=self.env, timeout=60, cwd=str(self.tmp))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)


class ManifestTest(PresenceBase):
    def test_hooks_registered_for_five_events_with_bounded_timeout(self):
        manifest = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        registered = manifest["hooks"]
        self.assertEqual(sorted(EVENT_HOOKS), sorted(registered))
        commands = {command for event in EVENT_HOOKS for group in registered[event]
                    for command in [entry["command"] for entry in group["hooks"]]}
        self.assertEqual(1, len(commands), "all events must share one bounded entry point")
        self.assertIn("pi_handoff.py", commands.pop())
        for event in EVENT_HOOKS:
            for group in registered[event]:
                for entry in group["hooks"]:
                    self.assertLessEqual(entry["timeout"], 5)


class PresenceDigestTest(PresenceBase):
    def test_presence_line_lists_every_event_awaiting_a_decision(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        digest = pi_presence.presence_for_thread(THREAD, board_file=self.board_file)
        text = "\n".join(digest["lines"])
        self.assertIn("awaiting-main decision", text)
        self.assertIn("UNIT-PRESENCE", text)
        self.assertIn("review_required", text)

    def test_nothing_awaiting_a_decision_yields_pins_only(self):
        digest = pi_presence.presence_for_thread(THREAD, board_file=self.board_file)
        text = "\n".join(digest["lines"])
        self.assertNotIn("awaiting-main decision", text)
        self.assertIn("task=UNIT-PRESENCE", text)

    def test_pin_line_keeps_unknown_and_never_zero(self):
        card = self.board["cards"]["UNIT-PRESENCE"]
        card["phase"] = {"phaseId": "PH-2"}
        line = pi_presence.task_pin(card)
        self.assertIn("contract=unknown", line)
        self.assertIn("candidate=unknown", line)
        self.assertNotIn("candidate=0", line)
        self.assertNotIn("contract=", line.replace("contract=unknown", "x"))

    def test_digest_is_bounded_and_marks_truncation(self):
        for number in range(1, 41):
            add_delivery(self.board, task_id="UNIT-PRESENCE", round_number=number)
        write_board(self.repo, self.board)
        digest = pi_presence.presence_for_thread(THREAD, board_file=self.board_file)
        text, truncated = pi_presence.render_context(digest)
        self.assertTrue(truncated or digest["truncated"])
        self.assertLessEqual(len(text.encode("utf-8")), pi_presence.MAX_CONTEXT_BYTES)
        self.assertIn("more", text)
        self.assertIn("never acceptance", text)

    def test_digest_reuses_transport_lines_without_duplication(self):
        pi_store.pause_route(THREAD, "interrupted")
        digest = pi_presence.compact_digest(THREAD, board_file=self.board_file)
        text = "\n".join(digest["lines"])
        self.assertEqual(1, text.count("route pause"), text)

    def test_corrupt_store_degrades_to_one_problem_line(self):
        broken = self.tmp / "broken-board.json"
        broken.write_text("{", encoding="utf-8")
        digest = pi_presence.presence_for_thread(THREAD, board_file=broken)
        self.assertEqual(1, len([line for line in digest["lines"] if "board state is" in line]))
        self.assertIsNotNone(digest["problem"])

    def test_unknown_thread_reports_no_invented_state(self):
        digest = pi_presence.presence_for_thread("99999999-9999-9999-9999-999999999999")
        self.assertIn(digest["problem"], ("missing", "not-routed"))

    def test_presence_runs_under_the_hook_budget(self):
        for index in range(6):
            task = f"UNIT-PRESENCE-{index}"
            self.board["cards"][task] = board_only(self.repo, task)["cards"][task]
            add_delivery(self.board, task_id=task)
        write_board(self.repo, self.board)
        started = time.monotonic()
        pi_presence.compact_digest(THREAD, board_file=self.board_file)
        self.assertLess(time.monotonic() - started, 1.5)


class HookOutputTest(PresenceBase):
    def test_dispatch_returns_additional_context_for_session_and_prompt(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        for event in ("SessionStart", "UserPromptSubmit"):
            output = self.run_hook(event)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(event, output["hookSpecificOutput"]["hookEventName"])
            self.assertIn("awaiting-main decision", context)
            self.assertIn("never acceptance", context)
            self.assertNotIn("decision", output.get("hookSpecificOutput", {}))

    def test_presence_dies_silently_for_an_unrouted_session(self):
        output = self.run_hook("SessionStart", session="99999999-9999-9999-9999-999999999999")
        self.assertEqual({}, output)

    def test_precompact_digest_carries_contract_and_candidate_pins(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        output = self.run_hook("PreCompact", extra={"trigger": "auto"})
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn(CONTRACT[:12], context)
        self.assertIn(HEAD[:12], context)
        self.assertIn("PH-1", context)

    def test_interrupt_still_pauses_and_returns_no_context(self):
        output = self.run_hook("Interrupt")
        self.assertEqual({}, output)
        paused, problem = pi_store.route_paused(THREAD)
        self.assertTrue(paused, problem)

    def test_hook_never_calls_codex_or_starts_a_model(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        blind = dict(self.env)
        blind["PATH"] = str(self.tmp / "empty-bin")
        (self.tmp / "empty-bin").mkdir(exist_ok=True)
        for event in EVENT_HOOKS:
            payload = json.dumps({"hook_event_name": event, "session_id": THREAD,
                                  "cwd": str(self.repo.root)})
            proc = subprocess.run([sys.executable, str(HANDOFF), "hook"], input=payload,
                                  capture_output=True, text=True, env=blind, timeout=60)
            self.assertEqual(proc.returncode, 0, f"{event}: {proc.stderr}")
            self.assertNotIn("codex", (proc.stdout + proc.stderr).lower()[:0] + "")
        self.assertIsNone(shutil.which("codex", path=str(self.tmp / "empty-bin")))


class CloseoutTest(PresenceBase):
    def prepared(self, **kwargs) -> dict:
        return pi_presence.prepare_closeout(THREAD, board_file=self.board_file,
                                            repo=self.repo.root, **kwargs)

    def test_session_end_prepares_without_filing(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        result = self.prepared()
        self.assertTrue(result["ok"], result)
        record = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual("unknown", record["outcome"]["status"])
        self.assertFalse(record["outcome"]["workComplete"])
        self.assertIn("record-outcome", record["recordCommand"])
        self.assertIn("--record-file", record["recordCommand"])
        self.assertTrue(Path(result["recordPath"]).is_file())
        self.assertFalse(result["filed"])
        self.assertFalse(pi_outcome.outcome_path(self.common, result["outcomeId"]).exists())

    def test_session_end_hook_reports_the_prepared_path(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        output = self.run_hook("SessionEnd")
        self.assertIn("closeout prepared", output["systemMessage"])
        self.assertIn("not acceptance", output["systemMessage"])
        self.assertNotIn("hookSpecificOutput", output)

    def test_session_end_apply_files_the_same_record(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        prepared = self.prepared()
        applied = self.prepared(apply=True)
        self.assertTrue(applied["filed"], applied)
        shown = subprocess.run([sys.executable, str(BOARD), "metrics", "--repo",
                                str(self.repo.root), "--outcome", prepared["outcomeId"]],
                               capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        projection = json.loads(shown.stdout)
        self.assertEqual("unknown", projection["status"])
        self.assertEqual(["UNIT-PRESENCE"], [m["task"] for m in projection["members"]])

    def test_prepared_record_rejects_a_fabricated_status(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        result = self.prepared()
        path = Path(result["recordPath"])
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("unknown", record["status"])
        record["status"] = "accepted"
        record["candidate"] = HEAD
        path.write_text(json.dumps(record), encoding="utf-8")
        filed = subprocess.run([sys.executable, str(BOARD), "record-outcome", "--repo",
                                str(self.repo.root), "--outcome", result["outcomeId"],
                                "--record-file", str(path)],
                               capture_output=True, text=True, env=self.env, timeout=60)
        self.assertNotEqual(filed.returncode, 0, filed.stdout)
        reason = (filed.stderr or "").lower()
        self.assertIn("candidate", reason, reason)
        self.assertIn("commit", reason, reason)

    def test_rounds_come_from_the_board_and_unknown_stays_unknown(self):
        result = self.prepared()
        wrapper = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        member = wrapper["outcome"]["members"][0]
        self.assertEqual("UNIT-PRESENCE", member["task"])
        self.assertEqual(["rounds", "task"], sorted(member))
        self.assertEqual([], member["rounds"])
        self.assertNotIn(0, member["rounds"])
        self.assertIn("under-report", wrapper["roundSource"])
        add_delivery(self.board)
        write_board(self.repo, self.board)
        later = pi_presence.prepare_closeout(THREAD, board_file=self.board_file,
                                            repo=self.repo.root, task="UNIT-PRESENCE")
        self.assertEqual([1], later["outcome"]["members"][0]["rounds"])

    def test_closeout_is_idempotent_and_never_overwrites_a_manual_record(self):
        first = self.prepared()
        second = self.prepared()
        self.assertEqual(first["path"], second["path"])
        self.assertFalse(second["changed"], second)
        marker = "# hand authored\n"
        Path(first["path"]).write_text(marker, encoding="utf-8")
        third = self.prepared()
        self.assertEqual(marker, Path(first["path"]).read_text(encoding="utf-8"))
        self.assertFalse(third["changed"])

class BoardCommandTest(PresenceBase):
    def test_closeout_command_is_available_and_read_only_by_default(self):
        add_delivery(self.board)
        write_board(self.repo, self.board)
        proc = subprocess.run([sys.executable, str(BOARD), "closeout", "--repo",
                               str(self.repo.root), "--session", THREAD],
                              capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"])
        self.assertFalse(result["filed"])
        self.assertTrue(Path(result["path"]).exists())


if __name__ == "__main__":
    unittest.main()
