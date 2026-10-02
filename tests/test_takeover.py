"""Two complete deliveries, whole-task replan, then takeover.

Every task allows two complete Pi deliveries. The first reviewed quality failure
requires the same Codex main session to reassess the whole outcome and remaining
plan and to record the repair in the existing design plus the next immutable
brief; the second failure latches takeover for that same main session.
No plan schema, heading parser or model judge exists in the runtime.

Every case runs against a temporary Git repository. The direct cases publish real
board events and call the real ``pi_board.decide`` authority; the lifecycle cases
start/continue the real CLI with the offline Pi double and real board
registration. No model, network or real Codex queue is invoked.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from runtime_helpers import (RUNTIME, Repo, base_env, cleanup_repos, default_config,
                             make_pi_trap, run_cli)

sys.path.insert(0, str(RUNTIME))
import pi_board
import pi_events
import pi_core
import pi_task
import pi_store
from pi_takeover import REVIEW_LIMIT, review_policy


def board_cli(*args, env: dict, expect: int = 0, timeout: float = 60):
    proc = subprocess.run([sys.executable, str(RUNTIME / "pi_board.py"),
                           *[str(arg) for arg in args]],
                          capture_output=True, text=True, env=env, timeout=timeout)
    if expect is not None and proc.returncode != expect:
        raise AssertionError(f"pi_board {' '.join(str(arg) for arg in args)} exited "
                             f"{proc.returncode}, expected {expect}\n"
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
    return json.loads(proc.stdout) if expect == 0 else proc


class TakeoverPolicyTest(unittest.TestCase):
    """Real board decisions against a temporary Git repository."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-takeover-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(cleanup_repos)
        self.repo = Repo(Path(self.temp.name), config=default_config())
        self.wt = self.repo.worktree("worker")
        self.head = pi_core.git(self.wt, "rev-parse", "HEAD")
        self.file = self.repo.state_dir / "board.json"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.board = None
        self.card = None

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------
    def new_card(self, *, phase=None, contract="a" * 64):
        card = pi_events._new_card("task", None, "task", "outcome", None, None,
                                  self.repo.root, self.repo.state_dir, str(self.wt),
                                  "offline", None, 1)
        if phase is not None:
            card["phase"] = {"phaseId": phase, "contractHash": contract,
                             "candidate": self.head, "status": "review_ready"}
        self.board = {"schemaVersion": 1, "revision": 1, "createdAt": 1, "updatedAt": 1,
                      "cards": {"task": card}}
        self.card = card
        self.write()

    def write(self):
        self.file.write_text(json.dumps(self.board))

    def reload(self):
        self.board = json.loads(self.file.read_text())
        self.card = self.board["cards"]["task"]

    def publish(self, number, *, kind="review_required", phase=None, contract="a" * 64,
                fingerprint=None, head=None):
        head = head or self.head
        self.card["pi"] = {"round": number, "state": "completed"}
        if phase is None:
            self.card.pop("phase", None)
        else:
            self.card["phase"] = {"phaseId": phase, "contractHash": contract,
                                  "candidate": head, "status": "review_ready"}
        event = pi_events.add_event(self.card, kind, number,
                                   fingerprint or f"event-{number}-{kind}",
                                   "delivery", {"head": head}, {}, "review", number * 10)
        self.assertIsNotNone(event, "event publication must be new")
        if phase is not None:
            event.update(phaseId=phase, contractHash=contract)
        self.write()
        return event

    def decide(self, event, decision="changes_requested", **kwargs):
        out = pi_board.decide(self.repo.root, "task", event["id"], decision, **kwargs)
        self.reload()
        return out

    def takeover_events(self):
        return [event for event in self.card["events"]
                if event["kind"] == "codex_takeover_required"]

    # ------------------------------------------------------------------
    # two complete deliveries
    # ------------------------------------------------------------------
    def test_two_deliveries_replan_then_takeover_on_second_failure(self):
        self.new_card()
        first = self.decide(self.publish(1, phase="P1"))
        policy = first["reviewPolicy"]
        self.assertEqual(policy["limit"], 2)
        self.assertEqual(policy["failedDeliveries"], 1)
        self.assertFalse(policy["takeoverRequired"])
        self.assertEqual(policy["implementationOwner"], "pi")
        self.assertIn("whole-task", policy["instruction"])
        self.assertIn("same Codex main session", policy["instruction"])
        self.assertIn("second complete Pi delivery", policy["instruction"])
        self.assertEqual(policy["outcome"]["phaseId"], "P1")
        self.assertEqual(self.takeover_events(), [])
        second = self.decide(self.publish(2, phase="P1", contract="b" * 64))
        policy = second["reviewPolicy"]
        self.assertEqual(policy["failedDeliveries"], 2)
        self.assertEqual(policy["failedReports"][-1]["contractHash"], "b" * 64)
        self.assertTrue(policy["takeoverRequired"])
        self.assertEqual(policy["implementationOwner"], "codex")
        self.assertIn("Codex takeover required", policy["instruction"])
        self.assertIn("all affected paths", policy["instruction"])
        self.assertEqual(len(self.takeover_events()), 1)

    def test_duplicate_events_for_one_round_count_once(self):
        self.new_card()
        self.decide(self.publish(1, phase="P1", fingerprint="first"))
        self.decide(self.publish(1, phase="P1", kind="phase_blocked", fingerprint="second"))
        self.assertEqual(review_policy(self.card)["failedDeliveries"], 1)
        out = self.decide(self.publish(2, phase="P1", fingerprint="third"))
        self.assertEqual(out["reviewPolicy"]["failedDeliveries"], 2)
        self.assertTrue(out["reviewPolicy"]["takeoverRequired"])

    def test_contract_revision_does_not_reset_counted_failures(self):
        self.new_card(phase="P1", contract="a" * 64)
        self.decide(self.publish(1, phase="P1", contract="a" * 64))
        self.decide(self.publish(2, phase="P1", contract="b" * 64))
        policy = review_policy(self.card)
        self.assertEqual(policy["limit"], 2)
        self.assertEqual(policy["failedDeliveries"], 2)
        self.assertTrue(policy["takeoverRequired"])
        self.assertEqual([report["contractHash"] for report in policy["failedReports"]],
                         ["a" * 64, "b" * 64])

    def test_phase_rename_cannot_reset_or_clear_a_takeover(self):
        self.new_card(phase="P1")
        self.decide(self.publish(1, phase="P1"))
        self.decide(self.publish(2, phase="P1"))
        self.assertTrue(review_policy(self.card)["takeoverRequired"])
        # Rename the failed outcome without any acceptance.
        self.card["phase"] = {"phaseId": "P2", "contractHash": "c" * 64,
                              "candidate": self.head, "status": "review_ready"}
        self.write()
        self.reload()
        policy = review_policy(self.card)
        self.assertEqual(policy["limit"], 2)
        self.assertTrue(policy["takeoverRequired"])
        # Even when visible history is pruned, the persisted latch refuses Pi.
        self.card["handled"] = {}
        self.card["events"] = []
        self.write()
        self.reload()
        policy = review_policy(self.card)
        self.assertTrue(policy["takeoverRequired"])
        self.assertEqual(policy["limit"], 2)
        self.assertEqual(policy["failedDeliveries"], 2)
        self.assertEqual(policy["implementationOwner"], "codex")

    def test_accepted_outcome_resets_count_before_takeover(self):
        self.new_card()
        first = self.decide(self.publish(1))
        self.assertFalse(first["reviewPolicy"]["takeoverRequired"])
        accepted = self.decide(self.publish(2), decision="accept", reviewed_head=self.head)
        self.assertEqual(accepted["decision"], "accepted")
        policy = accepted["reviewPolicy"]
        self.assertEqual(policy["failedDeliveries"], 0)
        self.assertFalse(policy["takeoverRequired"])
        self.assertEqual(policy["implementationOwner"], "pi")
        self.assertIsNone(policy["instruction"])
        # A fresh accepted outcome starts its own count.
        out = self.decide(self.publish(3))
        self.assertEqual(out["reviewPolicy"]["failedDeliveries"], 1)
        self.assertFalse(out["reviewPolicy"]["takeoverRequired"])

    def test_reached_latch_survives_a_later_injected_acceptance(self):
        self.new_card()
        self.decide(self.publish(1))
        self.decide(self.publish(2))
        self.assertTrue(review_policy(self.card)["takeoverRequired"])
        # A real Pi continuation is refused here. Even an injected later
        # acceptance must not silently return this task's work to Pi.
        accepted = self.decide(self.publish(3), decision="accept", reviewed_head=self.head)
        self.assertEqual(accepted["decision"], "accepted")
        self.assertTrue(accepted["reviewPolicy"]["takeoverRequired"])
        self.assertTrue(self.card["codex"]["takeover"]["required"])

    def test_external_blocker_never_counts_and_classification_is_immutable(self):
        self.new_card()
        event = self.publish(1, kind="phase_blocked")
        with self.assertRaisesRegex(ValueError, "external blockers require"):
            pi_board.decide(self.repo.root, "task", event["id"], "changes_requested",
                            failure_kind="external")
        out = self.decide(event, failure_kind="external",
                          note="needs the user's dashboard export; unlock when provided")
        self.assertEqual(out["reviewPolicy"]["failedDeliveries"], 0)
        self.assertFalse(out["reviewPolicy"]["takeoverRequired"])
        self.assertIsNone(out["reviewPolicy"]["instruction"])
        with self.assertRaisesRegex(ValueError, "classification is immutable"):
            self.decide(event, failure_kind="quality")

    def test_pause_and_resume_never_clear_the_count_or_latch(self):
        self.new_card()
        self.decide(self.publish(1))
        self.decide(self.publish(2))
        pi_board.set_paused(self.repo.root, "task", True, note="user paused")
        self.reload()
        self.assertTrue(review_policy(self.card)["takeoverRequired"])
        pi_board.set_paused(self.repo.root, "task", False)
        self.reload()
        policy = review_policy(self.card)
        self.assertTrue(policy["takeoverRequired"])
        self.assertEqual(policy["failedDeliveries"], 2)
        self.assertEqual(len(self.takeover_events()), 1)

    def test_non_delivery_decisions_never_count(self):
        self.new_card()
        self.card["handled"] = {
            str(number): {"decision": "changes_requested", "phaseId": "P1",
                          "eventKind": "ownership_unknown", "round": number, "at": number}
            for number in range(1, 6)}
        self.assertEqual(review_policy(self.card)["failedDeliveries"], 0)
        self.assertFalse(review_policy(self.card)["takeoverRequired"])


class TakeoverLifecycleTest(unittest.TestCase):
    """Real start/continue/register/decide cycles with the offline Pi double."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-takeover-life-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(cleanup_repos)
        self.repo = Repo(Path(self.temp.name), config=default_config())
        self.wt = self.repo.worktree("worker")
        self.env = base_env(CODEX_PI_HANDOFF_ROOT=str(Path(self.temp.name) / "handoff"))

    def start(self, task, *extra, expect=0):
        return run_cli("start", "--repo", self.repo.root, "--task", task,
                       "--worktree", self.wt, "--prompt", "Implement the outcome.", *extra,
                       env=self.env, expect=expect)

    def register(self, task, *extra):
        return board_cli("register", "--repo", self.repo.root, "--task", task,
                         "--transport", "offline", *extra, env=self.env)

    def refresh(self, task):
        return board_cli("refresh", "--repo", self.repo.root, "--task", task, env=self.env)

    def decide(self, task, event_id, decision, *extra):
        return board_cli("decide", "--repo", self.repo.root, "--task", task,
                         "--event-id", event_id, "--decision", decision, *extra, env=self.env)

    def board_card(self, task):
        return pi_store.read_board(self.repo.state_dir / "board.json")[0]["cards"][task]

    def review_event(self, task, number=None, kind="review_required"):
        events = [event for event in self.board_card(task)["events"] if event["kind"] == kind
                  and (number is None or event["round"] == number)]
        self.assertTrue(events, f"no {kind} event for round {number} in {task}")
        return events[-1]

    def card_view(self, task):
        shown = board_cli("show", "--repo", self.repo.root, "--task", task, env=self.env)
        self.assertEqual(shown["count"], 1)
        return shown["cards"][0]

    def test_new_task_takes_two_complete_deliveries_then_takes_over(self):
        trace = Path(self.temp.name) / "session-trace.jsonl"
        self.env["PI_DOUBLE_TRACE"] = str(trace)
        self.start("two-delivery")
        self.assertEqual(REVIEW_LIMIT, 2)
        self.assertEqual(self.repo.wait_terminal("two-delivery")["state"], "completed")
        self.register("two-delivery")
        self.refresh("two-delivery")
        first = self.decide("two-delivery", self.review_event("two-delivery", 1)["id"],
                            "changes_requested")
        policy = first["reviewPolicy"]
        self.assertEqual(policy["failedDeliveries"], 1)
        self.assertFalse(policy["takeoverRequired"])
        self.assertEqual(policy["implementationOwner"], "pi")
        self.assertIn("whole-task", policy["instruction"])
        self.assertEqual([event for event in self.board_card("two-delivery")["events"]
                          if event["kind"] == "codex_takeover_required"], [])
        # The same frozen Pi session and worktree take the second complete
        # delivery after the main session records its whole-task replan in the
        # next immutable brief. The runtime adds no plan gate or new model.
        run_cli("continue", "--repo", self.repo.root, "--task", "two-delivery",
                "--prompt", "Second complete delivery after the main-session replan.",
                env=self.env)
        self.assertEqual(self.repo.wait_terminal("two-delivery")["state"], "completed")
        sessions = [json.loads(line)["sessionId"] for line in trace.read_text().splitlines()]
        self.assertEqual(sessions, ["two-delivery", "two-delivery"])
        self.refresh("two-delivery")
        second = self.decide("two-delivery", self.review_event("two-delivery", 2)["id"],
                             "changes_requested")
        policy = second["reviewPolicy"]
        self.assertEqual(policy["failedDeliveries"], 2)
        self.assertTrue(policy["takeoverRequired"])
        self.assertEqual(policy["implementationOwner"], "codex")
        self.assertIn("all affected paths", policy["instruction"])
        refused = run_cli("continue", "--repo", self.repo.root, "--task", "two-delivery",
                          "--prompt", "must not run", env=self.env, expect=2)
        self.assertIn("Codex takeover required", refused.stderr)
        self.assertEqual(sorted(entry.name for entry in
                                (self.repo.task_dir("two-delivery") / "rounds").iterdir()),
                         ["1", "2"])

    def test_pause_then_resume_keeps_a_reached_takeover(self):
        self.start("paused-takeover")
        self.assertEqual(self.repo.wait_terminal("paused-takeover")["state"], "completed")
        self.register("paused-takeover")
        self.refresh("paused-takeover")
        first = self.decide("paused-takeover",
                            self.review_event("paused-takeover", 1)["id"], "changes_requested")
        self.assertFalse(first["reviewPolicy"]["takeoverRequired"])
        run_cli("continue", "--repo", self.repo.root, "--task", "paused-takeover",
                "--prompt", "second complete delivery", env=self.env)
        self.assertEqual(self.repo.wait_terminal("paused-takeover")["state"], "completed")
        self.refresh("paused-takeover")
        second = self.decide("paused-takeover",
                             self.review_event("paused-takeover", 2)["id"], "changes_requested")
        self.assertTrue(second["reviewPolicy"]["takeoverRequired"])
        board_cli("pause", "--repo", self.repo.root, "--task", "paused-takeover",
                  "--note", "user paused", env=self.env)
        self.refresh("paused-takeover")
        paused = self.card_view("paused-takeover")["reviewPolicy"]
        self.assertTrue(paused["takeoverRequired"])
        board_cli("resume", "--repo", self.repo.root, "--task", "paused-takeover", env=self.env)
        resumed = self.card_view("paused-takeover")["reviewPolicy"]
        self.assertTrue(resumed["takeoverRequired"])
        self.assertEqual(resumed["failedDeliveries"], 2)
        refused = run_cli("continue", "--repo", self.repo.root, "--task", "paused-takeover",
                          "--prompt", "must not run", env=self.env, expect=2)
        self.assertIn("Codex takeover required", refused.stderr)


class MissingEvidencePolicyTest(unittest.TestCase):
    """A round that only lacks evidence is not a reviewed quality failure."""

    PHASE = "P-MISSING"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-takeover-auto-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self.temp.name)
        self.repo = Repo(self.tmp, config=default_config())
        design = self.repo.root / "docs" / "design.md"
        design.parent.mkdir(parents=True, exist_ok=True)
        design.write_text("# missing receipt design\n", encoding="utf-8")
        self.repo._git("add", "-A")
        self.repo._git("-c", "user.email=test@example.invalid", "-c", "user.name=Test",
                       "commit", "-qm", "design")
        self.design_sha = hashlib.sha256(design.read_bytes()).hexdigest()
        self.wt = self.repo.worktree("worker")
        self.contract_path = self.tmp / "contract.json"
        self.contract_path.write_text(json.dumps({
            "schemaVersion": 1, "phaseId": self.PHASE, "goal": "Implement",
            "result": "Complete the missing-receipt outcome", "baseline": "HEAD",
            "scope": ["."], "designRef": "docs/design.md", "designSha256": self.design_sha,
            "acceptanceItems": [{
                "id": "A1", "description": "behavior works", "command": "python3 -c pass",
                "passCondition": "exit 0", "evidence": "receipt for A1"}],
            "budgetSeconds": 3600, "commandTimeoutSeconds": 900, "resourceLimits": [],
            "autonomousRepair": ["fix red tests"], "escalateWhen": ["design contradiction"]}),
            encoding="utf-8")
        self.env = base_env(CODEX_PI_HANDOFF_ROOT=str(self.tmp / "handoff"))

    def pending_blocked(self, task):
        raw = pi_store.read_board(self.repo.state_dir / "board.json")[0]["cards"][task]
        return [event for event in raw["events"] if event["kind"] == "phase_blocked"
                and not event["handled"]]

    def test_missing_evidence_does_not_count_but_two_quality_failures_do(self):
        run_cli("start", "--repo", self.repo.root, "--task", "missing-receipt",
                "--worktree", self.wt, "--prompt", "Implement the phase.",
                "--contract-file", self.contract_path, env=self.env)
        self.repo.wait_terminal("missing-receipt", round=1)
        self.assertEqual(sorted(entry.name for entry in
                                (self.repo.task_dir("missing-receipt") / "rounds").iterdir()),
                         ["1"], "there is no automatic second round")
        # Missing evidence alone is not a reviewed quality failure.
        board_cli("register", "--repo", self.repo.root, "--task", "missing-receipt",
                  "--transport", "offline", env=self.env)
        board_cli("refresh", "--repo", self.repo.root, "--task", "missing-receipt", env=self.env)
        raw = pi_store.read_board(self.repo.state_dir / "board.json")[0][
            "cards"]["missing-receipt"]
        shown = board_cli("show", "--repo", self.repo.root, "--task", "missing-receipt",
                          env=self.env)["cards"][0]
        self.assertEqual(shown["reviewPolicy"]["failedDeliveries"], 0)
        self.assertFalse(shown["reviewPolicy"]["takeoverRequired"])
        blocked = self.pending_blocked("missing-receipt")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["evidence"]["reason"], "missing_checks")
        self.assertFalse(any(event["kind"] == "review_required" and not event["handled"]
                             for event in raw["events"]))
        # First real quality failure: the same main session must replan before
        # the second complete delivery, and explicit continuation is still the
        # same committed Pi session/worktree (limit 2 is not reached).
        first = board_cli("decide", "--repo", self.repo.root, "--task", "missing-receipt",
                          "--event-id", blocked[0]["id"], "--decision", "changes_requested",
                          env=self.env)
        self.assertEqual(first["reviewPolicy"]["failedDeliveries"], 1)
        self.assertFalse(first["reviewPolicy"]["takeoverRequired"])
        self.assertIn("whole-task", first["reviewPolicy"]["instruction"])
        run_cli("continue", "--repo", self.repo.root, "--task", "missing-receipt",
                "--prompt", "Second complete delivery after the global replan.", env=self.env)
        self.repo.wait_terminal("missing-receipt", round=2)
        board_cli("refresh", "--repo", self.repo.root, "--task", "missing-receipt", env=self.env)
        blocked = self.pending_blocked("missing-receipt")
        self.assertEqual(len(blocked), 1)
        # Second real quality failure reaches the limit and latches.
        second = board_cli("decide", "--repo", self.repo.root, "--task", "missing-receipt",
                           "--event-id", blocked[0]["id"], "--decision", "changes_requested",
                           env=self.env)
        self.assertEqual(second["reviewPolicy"]["failedDeliveries"], 2)
        self.assertTrue(second["reviewPolicy"]["takeoverRequired"])
        refused = run_cli("continue", "--repo", self.repo.root, "--task", "missing-receipt",
                          "--prompt", "must not run", env=self.env, expect=2)
        self.assertIn("Codex takeover required", refused.stderr)


if __name__ == "__main__":
    unittest.main()
