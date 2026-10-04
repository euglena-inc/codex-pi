"""Behavioral tests for the 0.8.5 Main-reported outcome observation view.

Every scenario runs the real ``pi_board.py`` CLI (or the real lower-level API
for bounded paging) against real temporary git repositories and synthetic
fixtures calibrated to the production ``pi_summary``/``pi_check`` metadata
shapes: bare receipt filenames in stored summaries, assistant+auxiliary+total
usage blocks, and archived decisions with task/round identity.

A closeout is tested as a reported observation, never as acceptance. Positive
and negative controls cover attribution, coverage, deduplication, conflicts,
path safety, actual byte limits and timing without guessing.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Keep bytecode out of the source tree before importing runtime modules.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from runtime_helpers import RUNTIME, Repo, base_env, cleanup_repos, default_config, make_pi_trap

import pi_archive  # noqa: E402
import pi_core  # noqa: E402
import pi_events  # noqa: E402
import pi_outcome  # noqa: E402
import pi_outcome_view  # noqa: E402
import pi_recovery  # noqa: E402

BOARD = RUNTIME / "pi_board.py"
THREAD = "11111111-2222-3333-4444-555555555555"


def run_board(*args, env: dict, expect: int | None = None, timeout: float = 90):
    proc = subprocess.run([sys.executable, str(BOARD), *[str(arg) for arg in args]],
                          capture_output=True, text=True, env=env, timeout=timeout)
    if expect is not None and proc.returncode != expect:
        raise AssertionError(f"pi_board {' '.join(str(arg) for arg in args)} exited "
                             f"{proc.returncode}, expected {expect}\n"
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def board_json(*args, env: dict, timeout: float = 90):
    return json.loads(run_board(*args, env=env, expect=0, timeout=timeout).stdout)


class OutcomeMetricsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-outcome-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name)
        self.repo = Repo(self.tmp, name="repo", config=default_config())
        self.env = base_env()
        self.head = pi_core.git(self.repo.root, "rev-parse", "HEAD")
        self.common = self.repo.state_dir.parent
        self.records = 0

    # ------------------------------------------------------------------
    # fixture helpers aligned with real pi_task/pi_summary/pi_check shapes
    # ------------------------------------------------------------------
    def make_task(self, task="task-a", rounds=(1,), created_at=100.0, network=None):
        task_dir = self.repo.task_dir(task)
        task_dir.mkdir(parents=True, exist_ok=True)
        task_json = {"schemaVersion": 1, "task": task, "taskDir": str(task_dir),
                     "repo": str(self.repo.root), "commonDir": str(self.common),
                     "createdAt": created_at}
        if network is not None:
            task_json["network"] = network
        (task_dir / "task.json").write_text(json.dumps(task_json), encoding="utf-8")
        for number in rounds:
            (task_dir / "rounds" / str(number)).mkdir(parents=True, exist_ok=True)
        return task_dir

    def write_state(self, task, number, **overrides):
        state = {"state": "completed", "exitCode": 0, "startedAt": 0.0, "endedAt": 10.0,
                 "head": self.head}
        state.update(overrides)
        path = self.repo.task_dir(task) / "rounds" / str(number) / "round.state.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        return state

    def write_receipt(self, task, number, name, *, argv=None, started=2.0, ended=8.0,
                      exit_code=0, head=None, dirty=False, **overrides):
        round_dir = self.repo.task_dir(task) / "rounds" / str(number)
        checks = round_dir / "round.checks"
        checks.mkdir(parents=True, exist_ok=True)
        counts = {"run": 4, "pass": 4, "fail": 0, "skip": 0} if exit_code == 0 \
            else {"run": 1, "pass": 0, "fail": 1, "skip": 0}
        counts["format"] = "python_unittest_summary"
        data = {"schema_version": 1, "id": name,
                "argv": argv if argv is not None else ["python3", "-m", "unittest", "discover"],
                "cwd": str(self.repo.root), "head": head if head is not None else self.head,
                "dirty": dirty, "tracked_diff_sha256": "d" * 64, "started_at": started,
                "ended_at": ended, "deadline_at": ended, "exit_code": exit_code,
                "timed_out": False, "cancelled": False, "signal": None, "error": None,
                "test_counts": counts, "log": name + ".log", "log_sha256": "a" * 64,
                "running_marker": None, "resource_limit": None, "resourceLimit": None,
                "acceptance": "not_verified"}
        data.update(overrides)
        path = checks / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def receipt_meta(self, path, *, receipt=None, argv=None, started=2.0, ended=8.0,
                     exit_code=0, head=None, dirty=False, test_counts=None, **overrides):
        """A stored round.summary check_receipts entry; production stores a filename."""
        meta = {"id": path.stem, "argv": argv if argv is not None
                else ["python3", "-m", "unittest", "discover"],
                "head": head if head is not None else self.head, "dirty": dirty,
                "exit_code": exit_code, "timed_out": False, "cancelled": False,
                "started_at": started, "ended_at": ended,
                "test_counts": test_counts if test_counts is not None
                else ({"run": 4, "pass": 4, "fail": 0, "skip": 0} if exit_code == 0
                      else {"run": 1, "pass": 0, "fail": 1, "skip": 0}),
                "log": path.stem + ".log", "receipt": receipt if receipt is not None else path.name,
                "log_verified": True, "resource_limit": None}
        meta.update(overrides)
        return meta

    def checks_block(self, receipts):
        counts = {"attempts": len(receipts), "completed": 0, "passed": 0, "failed": 0,
                  "interrupted": 0, "unknown": 0}
        elapsed = 0.0
        for meta in receipts:
            if meta.get("timed_out") or meta.get("cancelled"):
                counts["interrupted"] += 1
                continue
            code = meta.get("exit_code")
            if code is None:
                counts["unknown"] += 1
                continue
            counts["completed"] += 1
            counts["passed" if code == 0 else "failed"] += 1
            if isinstance(meta.get("started_at"), (int, float)) \
                    and isinstance(meta.get("ended_at"), (int, float)):
                elapsed += meta["ended_at"] - meta["started_at"]
        return counts | {"elapsedSeconds": {"value": elapsed, "complete": True}}

    def write_summary(self, task, number, *, receipts=(), assistant_cost=0.25,
                      auxiliary_cost=0.5, total_cost=0.75, usage_complete=True,
                      auxiliary_complete=True, total_complete=True, cost_complete=True,
                      with_total=True, with_auxiliary=True, timing=None, checks=None,
                      models=None, expected_model="deepseek/deepseek-flash",
                      assistant_usage=None, total_usage=None):
        assistant = assistant_usage if assistant_usage is not None else {
            "uncachedInput": {"value": 10, "complete": True},
            "cacheRead": {"value": 20, "complete": True},
            "cacheWrite": {"value": 1, "complete": True},
            "output": {"value": 2, "complete": True},
            "reasoning": {"value": 1, "complete": True},
            "totalTokens": {"value": 33, "complete": True}}
        auxiliary = {"uncachedInput": {"value": 1, "complete": auxiliary_complete},
                     "cacheRead": {"value": 2, "complete": auxiliary_complete},
                     "cacheWrite": {"value": 0, "complete": auxiliary_complete},
                     "output": {"value": 0, "complete": auxiliary_complete},
                     "totalTokens": {"value": 3, "complete": auxiliary_complete},
                     "reportedCostUsd": {"value": auxiliary_cost, "complete": auxiliary_complete},
                     "complete": auxiliary_complete,
                     "status": "complete" if auxiliary_complete else "partial", "reason": None}
        combined = total_usage if total_usage is not None else {
            "input": 11, "cacheRead": 22, "cacheWrite": 1, "output": 2, "totalTokens": 36}
        total = {"assistantComplete": True, "auxiliaryComplete": auxiliary_complete,
                 "scope": "pi_execution", "costSource": "provider_reported",
                 "billingCostUsd": None, "billingComplete": False,
                 "complete": total_complete and auxiliary_complete, "usage": combined,
                 "reportedCostUsd": total_cost if cost_complete else None,
                 "costComplete": cost_complete, "reason": None, "note": "synthetic"}
        metrics = {"schema": 1, "usage": assistant,
                   "checks": checks if checks is not None else self.checks_block(list(receipts)),
                   "timing": timing if timing is not None else {
                       "windowSeconds": {"value": 10, "complete": True},
                       "modelResponseSeconds": {"value": 7, "complete": True},
                       "toolSeconds": {"value": 3, "complete": True},
                       "unattributedSeconds": {"value": 0, "complete": True}}}
        if with_auxiliary:
            metrics["auxiliary"] = auxiliary
        if with_total:
            metrics["total"] = total
        summary = {"schema_version": 1, "source": "round.jsonl",
                   "usage": {"input": 10, "cacheRead": 20, "cacheWrite": 1, "output": 2,
                             "totalTokens": 33},
                   "usage_complete": usage_complete, "reported_cost_usd": assistant_cost,
                   "auxiliary_usage": {"status": "complete", "complete": auxiliary_complete,
                                       "reliable": True, "entries": 0, "kinds": {},
                                       "usage": {}, "reported_cost_usd": auxiliary_cost,
                                       "reason": None, "sessionFile": "session.jsonl"},
                   "models": models if models is not None else {"deepseek/deepseek-flash": 2},
                   "expected_model": expected_model,
                   "metrics": metrics, "check_receipts": list(receipts)}
        path = self.repo.task_dir(task) / "rounds" / str(number) / "round.summary.json"
        path.write_text(json.dumps(summary), encoding="utf-8")
        return summary

    def record_file(self, payload) -> Path:
        self.records += 1
        path = self.tmp / f"record-{self.records}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def record(self, outcome, payload, *extra, expect=0):
        return run_board("record-outcome", "--repo", str(self.repo.root), "--outcome", outcome,
                         "--record-file", str(self.record_file(payload)), *extra,
                         env=self.env, expect=expect)

    def accept_record(self, outcome, payload, *extra):
        return json.loads(self.record(outcome, payload, *extra).stdout)

    def query(self, outcome, expect=0):
        return board_json("metrics", "--repo", str(self.repo.root), "--outcome", outcome,
                          env=self.env) if expect == 0 else run_board(
            "metrics", "--repo", str(self.repo.root), "--outcome", outcome, env=self.env,
            expect=expect)

    def accepted_payload(self, members=None, **overrides):
        payload = {"members": members if members is not None else [{"task": "task-a", "rounds": [1]}],
                   "status": "accepted", "candidate": self.head, "evidenceRefs": ["plan.md"],
                   "startedAt": 0.0, "finishedAt": 20.0, "workComplete": False}
        payload.update(overrides)
        return payload

    def seed_card(self, cards, state_dir=None):
        board = {"schemaVersion": 1, "revision": 10, "createdAt": 1, "updatedAt": 1,
                 "cards": cards}
        board_file = self.repo.state_dir / "board.json"
        board_file.write_text(json.dumps(board), encoding="utf-8")
        pi_recovery.recover_store(self.repo.root)
        return board_file

    def seed_decisions(self, board_file, task, records):
        with pi_archive.connection(board_file, write=True) as db:
            for index, record in enumerate(records):
                event_id = record.pop("_id", f"{index + 1:064x}")
                db.execute("INSERT OR REPLACE INTO decisions VALUES(?,?,?)",
                           (task, event_id, json.dumps(record)))

    def snapshot(self, root: Path, skip_parts=("outcomes",)) -> dict:
        result = {}
        for path in sorted(Path(root).rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in skip_parts for part in relative.parts):
                continue
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    # ------------------------------------------------------------------
    # round trip, arithmetic controls and relative-receipt deduplication
    # ------------------------------------------------------------------
    def test_round_trip_arithmetic_controls_and_shared_receipt_dedup(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1, startedAt=0.0, endedAt=10.0)
        self.write_state("task-a", 2, startedAt=5.0, endedAt=15.0)
        first = self.write_receipt("task-a", 1, "check-one",
                                   argv=["python3", "-m", "unittest", "discover"],
                                   started=2.0, ended=8.0)
        second = self.write_receipt("task-a", 1, "check-two", argv=["python3", "-m", "pytest"],
                                    started=5.0, ended=9.0)
        # Stored summaries name the receipt file relative to the owning round.checks.
        self.write_summary("task-a", 1, receipts=[
            self.receipt_meta(first, argv=["python3", "-m", "unittest", "discover"],
                              started=2.0, ended=8.0),
            self.receipt_meta(second, argv=["python3", "-m", "pytest"], started=5.0, ended=9.0)])
        self.write_summary("task-a", 2, receipts=[])
        relative = str(first.relative_to(self.repo.state_dir))
        payload = self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1, 2]}], workComplete=True,
            work=[{"id": "w1", "kind": "review", "reportedCostUsd": 0.5, "evidenceRef": "n.md"},
                  {"id": "w2", "kind": "supplemental_checks", "startedAt": 5.0, "finishedAt": 15.0,
                   "reportedCostUsd": 0.25, "evidenceRef": "n.md"}],
            extraChecks=[relative, relative])
        recorded = self.accept_record("delivery-1", payload)
        self.assertEqual((recorded["revision"], recorded["idempotent"]), (1, False))

        view = self.query("delivery-1")
        self.assertEqual(view["status"], "accepted")
        self.assertEqual(view["acceptance"], "not_verified")
        self.assertEqual([row["state"] for row in view["delivery"]["nativeRounds"]],
                         ["completed", "completed"])
        # Rounds [0,10] and [5,15] share 15 seconds, never 25.
        self.assertEqual(view["timing"]["roundExecutionUnionSeconds"]["value"], 15.0)
        self.assertEqual(view["timing"]["elapsedWallSeconds"], 20.0)
        self.assertTrue(view["timing"]["trueDeliveryLatencyKnown"])
        # The explicit external interval is separate; the boundary-less entry adds nothing.
        external = view["timing"]["externalWorkUnionSeconds"]
        self.assertEqual(external["value"], 10.0)
        self.assertEqual(external["idsWithoutBothBoundaries"], ["w1"])
        # Checks [2,8] and [5,9] are 10 seconds of work but 7 seconds of wall coverage.
        verification = view["verification"]
        self.assertEqual(verification["counts"]["attempts"], 2)
        self.assertEqual(verification["elapsedSeconds"]["value"], 10.0)
        self.assertEqual(verification["wallCoverageSeconds"]["value"], 7.0)
        self.assertEqual(verification["sources"],
                         {"roundSummaryReceipts": 2, "extraCheckReceipts": 1})
        self.assertEqual(verification["deduplicatedSharedReceipts"], 1)
        self.assertEqual(verification["uniqueReceipts"], 2)
        self.assertTrue(verification["countsComplete"])
        # Assistant+auxiliary total cost 0.75 per round plus external 0.75 = 2.25.
        self.assertEqual(view["usageCost"]["piReported"]["costUsd"]["known"], 1.5)
        self.assertTrue(view["usageCost"]["piReported"]["costUsd"]["complete"])
        self.assertEqual(view["usageCost"]["piReported"]["assistant"]["uncachedInput"]["known"], 20.0)
        self.assertEqual(view["usageCost"]["piReported"]["auxiliary"]["uncachedInput"]["known"], 2.0)
        self.assertEqual(view["usageCost"]["piReported"]["total"]["uncachedInput"]["known"], 22.0)
        self.assertEqual(view["usageCost"]["combinedReportedCostUsd"]["value"], 2.25)
        self.assertEqual(view["usageCost"]["modelIdentity"]["status"], "matched")
        self.assertFalse(view["usageCost"]["billing"]["verified"])

    # ------------------------------------------------------------------
    # auxiliary cost coverage and old summaries
    # ------------------------------------------------------------------
    def test_auxiliary_cost_is_included_and_old_summary_total_is_unknown(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        self.accept_record("aux-included", self.accepted_payload())
        view = self.query("aux-included")
        self.assertEqual(view["usageCost"]["piReported"]["costUsd"]["known"], 0.75)
        self.assertTrue(view["usageCost"]["piReported"]["costUsd"]["complete"])
        self.assertEqual(view["usageCost"]["piReported"]["auxiliary"]["reportedCostUsd"]["known"], 0.5)

        self.make_task("task-b", (1,))
        self.write_state("task-b", 1)
        # A legacy summary without auxiliary/total coverage: assistant stays visible,
        # the complete combined total must stay unknown.
        self.write_summary("task-b", 1, receipts=[], with_total=False, with_auxiliary=False)
        self.accept_record("old-summary", self.accepted_payload(
            members=[{"task": "task-b", "rounds": [1]}]))
        old = self.query("old-summary")
        pi = old["usageCost"]["piReported"]
        self.assertEqual(pi["assistant"]["uncachedInput"]["known"], 10.0)
        self.assertFalse(pi["costUsd"]["complete"])
        self.assertEqual(pi["roundsMissingTotal"], ["task-b/1"])
        self.assertIsNone(old["usageCost"]["combinedReportedCostUsd"]["value"])

    # ------------------------------------------------------------------
    # work completeness declarations never invent numbers
    # ------------------------------------------------------------------
    def test_work_completeness_does_not_invent_missing_cost(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        # A declared-complete report whose only work entry has no cost stays incomplete.
        self.accept_record("missing-work-cost", self.accepted_payload(
            workComplete=True,
            work=[{"id": "w1", "kind": "review", "evidenceRef": "notes.md"}]))
        view = self.query("missing-work-cost")
        self.assertFalse(view["usageCost"]["externalReported"]["complete"])
        self.assertTrue(view["usageCost"]["externalReported"]["missingCostEntries"] == ["w1"])
        self.assertIsNone(view["usageCost"]["combinedReportedCostUsd"]["value"])
        # An explicit no-external-work declaration is a reported zero, not a hole.
        self.accept_record("declared-zero", self.accepted_payload(workComplete=True))
        zero = self.query("declared-zero")
        self.assertTrue(zero["usageCost"]["externalReported"]["complete"])
        self.assertTrue(zero["usageCost"]["externalReported"]["declaredZero"])
        self.assertEqual(zero["usageCost"]["externalReported"]["reportedCostUsd"], 0.0)
        self.assertEqual(zero["usageCost"]["combinedReportedCostUsd"]["value"], 0.75)

    # ------------------------------------------------------------------
    # attribution: selected rounds only, first reviewed delivery keeps its decision
    # ------------------------------------------------------------------
    def test_member_attribution_filters_unselected_rounds(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        self.write_state("task-a", 2)
        self.write_summary("task-a", 1, receipts=[])
        self.write_summary("task-a", 2, receipts=[])
        card = pi_events._new_card("task-a", THREAD, "Synthetic", "Synthetic", None, None,
                                   str(self.repo.root), str(self.repo.state_dir),
                                   str(self.repo.root), "offline", None, 1)
        board_file = self.seed_card({"task-a": card})
        self.seed_decisions(board_file, "task-a", [
            {"_id": f"{1:064x}", "at": 1.0, "decision": "changes_requested",
             "round": 1, "eventKind": "execution_failed", "failureKind": "execution"},
            {"_id": f"{2:064x}", "at": 2.0, "decision": "rejected", "round": 2,
             "eventKind": "review_required", "failureKind": "quality"},
            {"_id": f"{3:064x}", "at": 3.0, "decision": "accepted", "round": 2,
             "eventKind": "review_required", "reviewedHead": self.head},
        ])
        self.accept_record("round-one-only",
                           self.accepted_payload(members=[{"task": "task-a", "rounds": [1]}]))
        view = self.query("round-one-only")
        decisions = view["delivery"]["nativeDecisions"]
        self.assertEqual(view["reworkIncidents"]["reviewedQualityFailures"]["count"], 0)
        self.assertIsNone(decisions["firstReviewedDelivery"])
        self.assertIsNone(decisions["firstReviewedAcceptance"])
        self.assertEqual(decisions["coverage"]["excludedDecisions"], 2)
        self.assertEqual(decisions["coverage"]["status"], "known")
        # The same archive with both rounds selected: the first reviewed delivery is
        # the rejected round 1, not the later round 2 acceptance.
        self.accept_record("both-rounds", self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1, 2]}]))
        both = self.query("both-rounds")
        first = both["delivery"]["nativeDecisions"]["firstReviewedDelivery"]
        self.assertEqual((first["decision"], first["round"]), ("rejected", 2))
        self.assertFalse(both["delivery"]["nativeDecisions"]["firstReviewedDeliveryAccepted"])
        self.assertEqual(both["delivery"]["nativeDecisions"]["firstReviewedAcceptance"]["round"], 2)
        self.assertEqual(both["reworkIncidents"]["reviewedQualityFailures"]["count"], 1)

    # ------------------------------------------------------------------
    # quality failures: delivery events only, distinct (task, round)
    # ------------------------------------------------------------------
    def test_quality_failures_are_delivery_quality_decisions_deduped_by_round(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        card = pi_events._new_card("task-a", THREAD, "Synthetic", "Synthetic", None, None,
                                   str(self.repo.root), str(self.repo.state_dir),
                                   str(self.repo.root), "offline", None, 1)
        board_file = self.seed_card({"task-a": card})
        self.seed_decisions(board_file, "task-a", [
            {"_id": f"{1:064x}", "at": 1.0, "decision": "rejected", "round": 1,
             "eventKind": "review_required", "failureKind": "quality"},
            {"_id": f"{2:064x}", "at": 2.0, "decision": "changes_requested", "round": 1,
             "eventKind": "review_required", "failureKind": "quality"},
            {"_id": f"{3:064x}", "at": 3.0, "decision": "rejected", "round": 1,
             "eventKind": "review_required", "failureKind": "external"},
            {"_id": f"{4:064x}", "at": 4.0, "decision": "changes_requested", "round": 1,
             "eventKind": "execution_failed", "failureKind": "execution"},
            {"_id": f"{5:064x}", "at": 5.0, "decision": "resolved", "round": 1,
             "eventKind": "execution_failed"},
        ])
        self.accept_record("dedupe", self.accepted_payload())
        view = self.query("dedupe")
        failures = view["reworkIncidents"]["reviewedQualityFailures"]
        self.assertEqual(failures["count"], 1)
        self.assertEqual({event["eventId"] for event in failures["events"]}, {f"{1:064x}", f"{2:064x}"})
        self.assertEqual(view["reworkIncidents"]["reviewedQualityFailures"]["count"], 1)

    # ------------------------------------------------------------------
    # archive paging beyond the card window, attributed to selected rounds
    # ------------------------------------------------------------------
    def test_archive_paging_beyond_window_with_selected_rounds(self):
        self.make_task("task-a", tuple(range(1, 61)))
        for number in range(1, 61):
            self.write_state("task-a", number, endedAt=float(number))
        card = pi_events._new_card("task-a", THREAD, "Synthetic", "Synthetic", None, None,
                                   str(self.repo.root), str(self.repo.state_dir),
                                   str(self.repo.root), "offline", None, 1)
        board_file = self.seed_card({"task-a": card})
        records = []
        for number in range(1, 42):
            records.append({"_id": f"{number:064x}", "at": float(number), "decision": "rejected",
                            "round": number, "eventKind": "review_required",
                            "failureKind": "quality"})
        for number in range(42, 61):
            records.append({"_id": f"{number:064x}", "at": float(number), "decision": "accepted",
                            "round": number, "eventKind": "review_required",
                            "reviewedHead": self.head})
        for number in range(90, 96):  # unselected rounds must never mix in
            records.append({"_id": f"{number:064x}", "at": float(number), "decision": "rejected",
                            "round": number, "eventKind": "review_required",
                            "failureKind": "quality"})
        self.seed_decisions(board_file, "task-a", records)
        self.accept_record("paged", self.accepted_payload(
            members=[{"task": "task-a", "rounds": list(range(1, 61))}]))
        view = self.query("paged")
        decisions = view["delivery"]["nativeDecisions"]
        self.assertEqual(decisions["counts"]["rejected"], 41)
        self.assertEqual(decisions["counts"]["accepted"], 19)
        self.assertEqual(decisions["coverage"]["excludedDecisions"], 6)
        self.assertEqual(view["reworkIncidents"]["reviewedQualityFailures"]["count"], 41)
        self.assertEqual(decisions["coverage"]["status"], "known")

    # ------------------------------------------------------------------
    # one task split across several explicit members is merged, not rescanned
    # ------------------------------------------------------------------
    def test_duplicate_task_members_are_merged(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        self.write_state("task-a", 2)
        self.write_summary("task-a", 1, receipts=[])
        self.write_summary("task-a", 2, receipts=[])
        card = pi_events._new_card("task-a", THREAD, "Synthetic", "Synthetic", None, None,
                                   str(self.repo.root), str(self.repo.state_dir),
                                   str(self.repo.root), "offline", None, 1)
        board_file = self.seed_card({"task-a": card})
        self.seed_decisions(board_file, "task-a", [
            {"_id": f"{1:064x}", "at": 1.0, "decision": "rejected", "round": 1,
             "eventKind": "review_required", "failureKind": "quality"},
            {"_id": f"{2:064x}", "at": 2.0, "decision": "accepted", "round": 2,
             "eventKind": "review_required", "reviewedHead": self.head}])
        self.accept_record("merged", self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1]}, {"task": "task-a", "rounds": [2]}]))
        view = self.query("merged")
        self.assertEqual(view["delivery"]["nativeDecisions"]["selectedRounds"],
                         [{"task": "task-a", "rounds": [1, 2]}])
        self.assertEqual([(row["task"], row["round"]) for row in view["delivery"]["nativeRounds"]],
                         [("task-a", 1), ("task-a", 2)])
        self.assertEqual(view["delivery"]["nativeDecisions"]["counts"]["rejected"], 1)
        self.assertEqual(view["delivery"]["nativeDecisions"]["counts"]["accepted"], 1)

    # ------------------------------------------------------------------
    # verification: conflicts, hash mismatch, dirty repeats, extra failures
    # ------------------------------------------------------------------
    def test_verification_conflicts_dirty_repeats_and_extra_failures(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]
        clean_a = self.write_receipt("task-a", 1, "clean-a", argv=argv, started=0.0, ended=5.0)
        clean_b = self.write_receipt("task-a", 1, "clean-b", argv=list(argv), started=6.0, ended=11.0)
        dirty = self.write_receipt("task-a", 1, "dirty-same", argv=list(argv), started=12.0,
                                   ended=13.0, dirty=True)
        conflicted = self.write_receipt("task-a", 1, "conflicted", argv=["x"], started=14.0,
                                        ended=15.0, exit_code=3)
        self.write_summary("task-a", 1, receipts=[
            self.receipt_meta(clean_a, argv=argv, started=0.0, ended=5.0),
            self.receipt_meta(clean_b, argv=list(argv), started=6.0, ended=11.0),
            self.receipt_meta(dirty, argv=list(argv), started=12.0, ended=13.0, dirty=True),
            # The summary claims exit 0 for the file the extra check pins at exit 3.
            self.receipt_meta(conflicted, argv=["x"], started=14.0, ended=15.0, exit_code=0)])
        payload = self.accepted_payload(extraChecks=[
            str(conflicted.relative_to(self.repo.state_dir))])
        self.accept_record("verify", payload)
        view = self.query("verify")
        verification = view["verification"]
        repeats = verification["potentialRepeats"]
        self.assertEqual(len(repeats), 1)
        self.assertEqual(repeats[0]["count"], 2)
        self.assertNotIn("dirty-same", json.dumps(repeats))
        self.assertTrue(verification["coverage"]["conflicts"])
        self.assertFalse(verification["countsComplete"])
        self.assertGreaterEqual(verification["counts"]["unknown"], 1)
        # A failed extra Main check still contributes to the failure count.
        failed_extra = self.write_receipt("task-a", 1, "extra-failed", argv=["make", "check"],
                                          exit_code=2)
        self.accept_record("verify-extra", self.accepted_payload(extraChecks=[
            str(failed_extra.relative_to(self.repo.state_dir))]))
        extra_view = self.query("verify-extra")
        self.assertEqual(extra_view["verification"]["counts"]["failed"], 1)
        self.assertEqual(extra_view["reworkIncidents"]["failedCheckAttempts"]["value"], 1)
        # A receipt whose pinned copy was modified is untrusted, not silently dropped.
        self.accept_record("verify-mismatch", self.accepted_payload(extraChecks=[
            str(clean_a.relative_to(self.repo.state_dir))]))
        clean_a.write_text(json.dumps({"schema_version": 1, "id": "tampered"}),
                           encoding="utf-8")
        mismatch = self.query("verify-mismatch")
        self.assertFalse(mismatch["verification"]["countsComplete"])
        self.assertTrue(mismatch["verification"]["coverage"]["degraded"])

    # ------------------------------------------------------------------
    # timing guards and explicit coverage
    # ------------------------------------------------------------------
    def test_timing_guards_never_report_negative_unknown_or_false_zero(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1, startedAt=0.0, endedAt=10.0)
        # No start is reported and it cannot be deduced later than the finish.
        reject = self.record("negative-latency", self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1]}], startedAt=None, finishedAt=20.0),
            expect=2)
        self.assertIn("earliest selected member task creation", reject.stderr)
        # A stored revision whose derived start is later than the reported finish
        # (bypassing record validation) still yields unknown latency at query time.
        self.write_state("task-a", 2, startedAt=5.0, endedAt=15.0)
        self.write_summary("task-a", 2, receipts=[])
        self.accept_record("forged", self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1]}], startedAt=None, finishedAt=200.0))
        revision_path = self.repo.state_dir / "outcomes" / "forged" / "revision-000001.json"
        envelope = json.loads(revision_path.read_text(encoding="utf-8"))
        envelope["payload"]["finishedAt"] = 20.0
        envelope["payloadDigest"] = pi_outcome.payload_digest(envelope["payload"])
        pi_core.atomic(revision_path, envelope)
        view = self.query("forged")
        self.assertIsNone(view["timing"]["elapsedWallSeconds"])
        self.assertFalse(view["timing"]["trueDeliveryLatencyKnown"])
        self.assertIn("precedes", view["timing"]["trueDeliveryLatencyReason"])
        # A reported finish without a start stays unknown.
        self.accept_record("finish-only", self.accepted_payload(members=[], finishedAt=20.0,
                                                                startedAt=None))
        finish_only = self.query("finish-only")
        self.assertIsNone(finish_only["timing"]["elapsedWallSeconds"])
        self.assertFalse(finish_only["timing"]["trueDeliveryLatencyKnown"])
        # A deduced start needs every selected member's creation metadata; a source
        # that later became unreadable makes the latency unknown, not known.
        self.make_task("task-c", (1,))
        self.write_state("task-c", 1)
        self.write_summary("task-c", 1, receipts=[])
        self.accept_record("deduced", self.accepted_payload(
            members=[{"task": "task-c", "rounds": [1]}], startedAt=None, finishedAt=200.0))
        self.assertTrue(self.query("deduced")["timing"]["trueDeliveryLatencyKnown"])
        (self.repo.task_dir("task-c") / "task.json").write_text("{broken", encoding="utf-8")
        deduced = self.query("deduced")
        self.assertEqual(deduced["timing"]["start"]["source"], "unknown")
        self.assertFalse(deduced["timing"]["trueDeliveryLatencyKnown"])
        # Missing work boundaries are unknown, not a declared zero.
        self.accept_record("boundary-less", self.accepted_payload(
            members=[], workComplete=False,
            work=[{"id": "w1", "kind": "review"}], startedAt=0.0, finishedAt=20.0))
        boundary = self.query("boundary-less")
        self.assertIsNone(boundary["timing"]["externalWorkUnionSeconds"]["value"])
        self.assertEqual(boundary["timing"]["externalWorkUnionSeconds"]["idsWithoutBothBoundaries"],
                         ["w1"])

    # ------------------------------------------------------------------
    # idempotent replay, correction and stale conflicts (unchanged contract)
    # ------------------------------------------------------------------
    def test_idempotent_replay_correction_and_stale_conflict(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        payload = self.accepted_payload()
        first = self.accept_record("revisions", payload)
        self.assertEqual((first["revision"], first["idempotent"]), (1, False))
        directory = self.repo.state_dir / "outcomes" / "revisions"
        original = (directory / "revision-000001.json").read_bytes()
        replay = json.loads(self.record("revisions", payload, "--expected-revision", "0").stdout)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         ["revision-000001.json", "write.lock"])
        corrected = self.accept_record("revisions",
                                       self.accepted_payload(status="partial", candidate=None),
                                       "--expected-revision", "1")
        self.assertEqual((corrected["revision"], corrected["idempotent"]), (2, False))
        self.assertEqual((directory / "revision-000001.json").read_bytes(), original)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        conflict = self.record("revisions", payload, "--expected-revision", "1", expect=2)
        self.assertIn("stale expected revision", conflict.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)
        missing = self.record("revisions", payload, expect=2)
        self.assertIn("--expected-revision", missing.stderr)
        self.assertEqual(self.query("revisions")["revision"], 2)

    # ------------------------------------------------------------------
    # malformed, unsafe, oversized and inconsistent inputs
    # ------------------------------------------------------------------
    def test_rejects_malformed_unsafe_and_oversized_inputs(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        base = self.accepted_payload()
        plain = self.repo.state_dir / "plain.json"
        plain.write_text('{"not": "a receipt"}', encoding="utf-8")

        def mutate(**overrides):
            payload = json.loads(json.dumps(base))
            payload.update(overrides)
            return payload

        cases = {
            "unknown-field": mutate(surprise=1),
            "bad-status": mutate(status="shipped"),
            "missing-candidate": mutate(candidate=None),
            "unreal-candidate": mutate(candidate="f" * 40),
            "missing-evidence": mutate(evidenceRefs=[]),
            "fractional-tokens": mutate(work=[{"id": "w", "kind": "review", "evidenceRef": "e",
                                               "usage": {"input": 1.5}}]),
            "boolean-token": mutate(work=[{"id": "w", "kind": "review", "evidenceRef": "e",
                                           "usage": {"input": True}}]),
            "negative-cost": mutate(work=[{"id": "w", "kind": "review", "evidenceRef": "e",
                                           "reportedCostUsd": -1}]),
            "bad-timestamps": mutate(startedAt=10, finishedAt=5),
            "duplicate-work": mutate(work=[{"id": "w", "kind": "review"},
                                           {"id": "w", "kind": "debug"}]),
            "quantity-without-ref": mutate(work=[{"id": "w", "kind": "review",
                                                  "usage": {"input": 1}}]),
            "duplicate-member-round": mutate(members=[{"task": "task-a", "rounds": [1, 1]}]),
            "unknown-kind": mutate(work=[{"id": "w", "kind": "unknown-kind"}]),
            "missing-member-task": mutate(members=[{"task": "ghost", "rounds": [1]}]),
            "missing-member-round": mutate(members=[{"task": "task-a", "rounds": [7]}]),
            "escaping-extra-check": mutate(extraChecks=["../board.json"]),
            "absolute-extra-check": mutate(extraChecks=[str(plain)]),
            "missing-extra-check": mutate(extraChecks=["tasks/task-a/rounds/1/round.checks/x.json"]),
            "non-receipt-extra-check": mutate(extraChecks=["plain.json"]),
            "negative-latency": mutate(startedAt=None, finishedAt=20.0,
                                       members=[{"task": "task-a", "rounds": [1]}]),
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                self.record("rejects", payload, expect=2)
                self.assertFalse((self.repo.state_dir / "outcomes" / "rejects").exists())
        self.record("rejects", base, "--expected-revision", "-1", expect=2)
        self.record("../../escape", base, expect=2)
        self.record("rejects", ["not", "an", "object"], expect=2)
        # An oversized extra receipt is refused before any full parse.
        big = self.repo.task_dir("task-a") / "rounds" / "1" / "round.checks" / "big.json"
        big.parent.mkdir(parents=True, exist_ok=True)
        big.write_bytes(b"x" * 1_000_001)
        proc = self.record("oversized-extra", self.accepted_payload(
            extraChecks=[str(big.relative_to(self.repo.state_dir))]), expect=2)
        self.assertIn("oversized", proc.stderr)
        # A member task frozen to a different git-common identity cannot be smuggled in.
        self.make_task("task-b", (1,))
        self.write_state("task-b", 1)
        task_b_json = self.repo.task_dir("task-b") / "task.json"
        foreign = json.loads(task_b_json.read_text(encoding="utf-8"))
        foreign["commonDir"] = str(self.tmp / "elsewhere")
        task_b_json.write_text(json.dumps(foreign), encoding="utf-8")
        proc = self.record("foreign-member", self.accepted_payload(
            members=[{"task": "task-b", "rounds": [1]}]), expect=2)
        self.assertIn("different git-common", proc.stderr)
        self.assertFalse((self.repo.state_dir / "outcomes" / "foreign-member").exists())
        huge = self.tmp / "huge.json"
        huge.write_text(json.dumps(base) + " " * 1_000_001, encoding="utf-8")
        proc = run_board("record-outcome", "--repo", str(self.repo.root), "--outcome", "rejects",
                         "--record-file", str(huge), env=self.env, expect=2)
        self.assertIn("record file is oversized", proc.stderr)
        # A metadata file symlink cannot smuggle a member from outside the state root.
        outside = self.tmp / "outside-task.json"
        outside.write_text(json.dumps({"task": "task-a", "createdAt": 1.0}), encoding="utf-8")
        task_json = self.repo.task_dir("task-a") / "task.json"
        original_task = task_json.read_bytes()
        task_json.unlink()
        task_json.symlink_to(outside)
        try:
            self.record("symlinked-member", base, expect=2)
        finally:
            task_json.unlink()
            task_json.write_bytes(original_task)
        self.assertFalse((self.repo.state_dir / "outcomes" / "symlinked-member").exists())

    def test_oversized_normalized_revision_is_refused_before_publish(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        huge_argv = ["x" * 500] * 100
        paths = []
        for index in range(50):
            path = self.write_receipt("task-a", 1, f"bulk-{index}", argv=huge_argv)
            paths.append(str(path.relative_to(self.repo.state_dir)))
        payload = self.accepted_payload(extraChecks=paths)
        record = self.record("too-big", payload, expect=2)
        self.assertIn("bounded revision limit", record.stderr)
        directory = self.repo.state_dir / "outcomes" / "too-big"
        leftover = [path.name for path in directory.iterdir()] if directory.is_dir() else []
        self.assertEqual(leftover, ["write.lock"])

    def test_outcomes_root_symlink_is_rejected_for_record_query_and_listing(self):
        outside = self.tmp / "outside-outcomes"
        outside.mkdir()
        self.repo.state_dir.mkdir(parents=True, exist_ok=True)
        symlink = self.repo.state_dir / "outcomes"
        symlink.symlink_to(outside)
        try:
            self.record("escape", self.accepted_payload(), expect=2)
            self.query("escape", expect=2)
            run_board("metrics", "--repo", str(self.repo.root), "--outcomes", env=self.env, expect=2)
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            symlink.unlink()

    # ------------------------------------------------------------------
    # missing / corrupt legacy sources stay explicit
    # ------------------------------------------------------------------
    def test_legacy_missing_and_corrupt_sources_stay_explicit(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        self.write_state("task-a", 2)
        self.write_summary("task-a", 1, receipts=[])
        self.write_summary("task-a", 2, receipts=[])
        self.accept_record("legacy", self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1, 2]}]))
        (self.repo.task_dir("task-a") / "rounds" / "2" / "round.state.json").unlink()
        view = self.query("legacy")
        self.assertEqual(view["delivery"]["nativeRoundCoverage"]["status"], "partial")
        self.assertEqual(view["delivery"]["nativeRoundCoverage"]["missing"], ["task-a/2"])
        self.assertEqual(view["timing"]["roundExecutionUnionSeconds"]["value"], 10.0)
        self.assertEqual(view["timing"]["roundExecutionUnionSeconds"]["coverage"]["unknown"],
                         ["task-a/2"])
        directory = self.repo.state_dir / "outcomes" / "legacy"
        original = (directory / "revision-000001.json").read_bytes()
        (directory / "revision-000001.json").write_text("{not json", encoding="utf-8")
        corrupt = run_board("metrics", "--repo", str(self.repo.root), "--outcome", "legacy",
                            env=self.env, expect=2)
        self.assertIn("revision 1", corrupt.stderr)
        (directory / "revision-000001.json").write_bytes(original)
        (directory / "revision-000002.json").write_bytes(original)
        (directory / "revision-000001.json").unlink()
        gap = run_board("metrics", "--repo", str(self.repo.root), "--outcome", "legacy",
                        env=self.env, expect=2)
        self.assertIn("gap", gap.stderr)
        listing = board_json("metrics", "--repo", str(self.repo.root), "--outcomes", env=self.env)
        row, = listing["outcomes"]
        self.assertEqual(row["status"], "incomplete")
        self.assertIn("gap", row["problem"])
        missing = run_board("metrics", "--repo", str(self.repo.root), "--outcome", "absent",
                            env=self.env, expect=2)
        self.assertIn("not recorded", missing.stderr)
        # A member round with no stored summary declares no definite check zero.
        self.make_task("task-b", (1,))
        self.write_state("task-b", 1)
        self.accept_record("no-source", self.accepted_payload(
            members=[{"task": "task-b", "rounds": [1]}]))
        no_source = self.query("no-source")
        self.assertEqual(no_source["verification"]["counts"]["attempts"], 0)
        self.assertFalse(no_source["verification"]["countsComplete"])
        self.assertIn(no_source["verification"]["coverage"]["status"], ("partial", "unknown"))
        self.assertIsNone(no_source["verification"]["elapsedSeconds"]["value"])

    # ------------------------------------------------------------------
    # failed Pi execution plus successful Main report
    # ------------------------------------------------------------------
    def test_failed_pi_and_main_success_remains_two_separate_facts(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1, state="failed", exitCode=1, startedAt=0.0, endedAt=10.0)
        self.write_summary("task-a", 1, receipts=[], with_total=False, with_auxiliary=False,
                           assistant_cost=0.0)
        self.accept_record("failed-pi", self.accepted_payload())
        view = self.query("failed-pi")
        self.assertEqual(view["delivery"]["reported"]["status"], "accepted")
        self.assertEqual(view["delivery"]["nativeRounds"][0]["state"], "failed")
        self.assertEqual(view["delivery"]["nativeRounds"][0]["exitCode"], 1)
        self.assertEqual(view["interventionCoverage"]["completion"],
                         "main_reported_after_failed_pi_execution")
        self.assertEqual(view["reworkIncidents"]["failedRounds"][0]["state"], "failed")
        self.assertNotIn("accepted", json.dumps(view["delivery"]["nativeRounds"]))

    # ------------------------------------------------------------------
    # takeover cause attribution: explicit main_decision is never quality_limit
    # ------------------------------------------------------------------
    def test_takeover_cause_is_preserved_and_attributed(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        self.write_state("task-a", 2)
        self.write_summary("task-a", 1, receipts=[])
        self.write_summary("task-a", 2, receipts=[])
        card = pi_events._new_card("task-a", THREAD, "Synthetic", "Synthetic", None, None,
                                   str(self.repo.root), str(self.repo.state_dir),
                                   str(self.repo.root), "offline", None, 1)
        card["codex"] = {"takeover": {"required": True, "cause": "main_decision", "at": 3.0,
                                      "scope": "task:task-a", "failedDeliveries": 1,
                                      "outcome": {"round": 1},
                                      "failedReports": [{"round": 1}]}}
        event = pi_events.add_event(card, "codex_takeover_required", 2, "quality-failure-limit-reached",
                                    "Codex must take over", {"head": self.head},
                                    {"reviewPolicy": {"takeoverCause": "quality_limit"}},
                                    "take over", 4.0)
        board_file = self.seed_card({"task-a": card})
        self.accept_record("takeover", self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1, 2]}]))
        view = self.query("takeover")
        evidence = view["interventionCoverage"]["nativeTakeover"]["evidence"]
        causes = {entry["source"]: entry["cause"] for entry in evidence}
        self.assertEqual(causes["board_takeover_latch"], "main_decision")
        self.assertEqual(causes["archived_takeover_event"], "quality_limit")
        self.assertEqual(view["interventionCoverage"]["nativeTakeover"]["observed"], True)
        self.assertEqual(view["interventionCoverage"]["completion"], "assisted_main_completion")
        # The archived takeover for an unselected round never enters this outcome.
        self.accept_record("takeover-round-one", self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1]}]))
        round_one = self.query("takeover-round-one")
        sources = {entry["source"] for entry in round_one["interventionCoverage"]["nativeTakeover"]["evidence"]}
        self.assertEqual(sources, {"board_takeover_latch"})

    # ------------------------------------------------------------------
    # read-only: no board/execution mutation, no session scan, no model call
    # ------------------------------------------------------------------
    def test_metrics_and_record_do_not_mutate_or_scan_sessions(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        session = self.repo.task_dir("task-a") / "session"
        session.mkdir(parents=True, exist_ok=True)
        session_file = session / "round.jsonl"
        session_file.write_text('{"type": "message", "text": "' + "x" * 1000 + '"}\n',
                                encoding="utf-8")
        (self.repo.task_dir("task-a") / "rounds" / "1" / "round.jsonl").write_text(
            "raw transcript bytes that must never be parsed for metrics\n", encoding="utf-8")
        before = self.snapshot(self.common)
        self.accept_record("readonly", self.accepted_payload())
        self.assertEqual(self.snapshot(self.common), before)
        self.query("readonly")
        board_json("metrics", "--repo", str(self.repo.root), "--task", "task-a", env=self.env)
        self.assertEqual(self.snapshot(self.common), before)
        trap, marker = make_pi_trap(self.tmp)
        env = base_env(PI_BIN=str(trap))
        os.chmod(session_file, 0)
        try:
            self.query("readonly")
            board_json("metrics", "--repo", str(self.repo.root), "--task", "task-a", env=env)
        finally:
            os.chmod(session_file, 0o600)
        self.assertFalse(marker.exists())
        self.assertEqual(self.snapshot(self.common), before)

    # ------------------------------------------------------------------
    # bounded cursor listing
    # ------------------------------------------------------------------
    def test_listing_is_bounded_with_a_cursor_and_missing_sources(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, receipts=[])
        for name in ("alpha", "beta", "gamma"):
            self.accept_record(name, self.accepted_payload(status="partial", candidate=None))
        page_one = pi_outcome_view.list_outcomes(self.common, limit=2)
        self.assertEqual([row["outcome"] for row in page_one["outcomes"]], ["alpha", "beta"])
        self.assertEqual(page_one["nextCursor"], "beta")
        page_two = pi_outcome_view.list_outcomes(self.common, cursor="beta", limit=2)
        self.assertEqual([row["outcome"] for row in page_two["outcomes"]], ["gamma"])
        self.assertIsNone(page_two["nextCursor"])
        (self.repo.task_dir("task-a") / "rounds" / "1" / "round.state.json").unlink()
        listing = board_json("metrics", "--repo", str(self.repo.root), "--outcomes", env=self.env)
        self.assertEqual([row["outcome"] for row in listing["outcomes"]],
                         ["alpha", "beta", "gamma"])
        self.assertEqual(listing["outcomes"][0]["memberSources"]["missing"], ["task-a/1"])
        self.assertNotIn("successRate", json.dumps(listing))


    def test_native_total_generator_and_model_attribution(self):
        import pi_summary
        self.make_task("task-a", (1, 2))
        for number in (1, 2):
            self.write_state("task-a", number)
            summary = self.write_summary("task-a", number)
            summary["metrics"]["total"] = pi_summary.total_metrics(
                summary["usage"], True, 0.25, 2, 2, summary["metrics"]["auxiliary"])
            if number == 2:
                summary["models"] = {"model-a": 1, "model-b": 1}
            path = self.repo.task_dir("task-a") / "rounds" / str(number) / "round.summary.json"
            path.write_text(json.dumps(summary))
        self.accept_record("native", self.accepted_payload(members=[{"task": "task-a", "rounds": [1, 2]}]))
        pi = self.query("native")["usageCost"]["piReported"]
        self.assertEqual(pi["total"]["uncachedInput"]["known"], 22)
        self.assertTrue(pi["totalComplete"])
        by_model = {entry["model"]: entry for entry in pi["assistantByModel"]}
        self.assertEqual(by_model[None]["rounds"], ["task-a/2"])
        self.assertEqual(by_model["deepseek/deepseek-flash"]["usage"]["uncachedInput"]["known"], 10)
        self.assertNotIn("model-a", by_model)

    def test_combined_overflow_is_unknown_and_json_finite(self):
        self.make_task()
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, total_cost=1e308)
        self.accept_record("overflow", self.accepted_payload(workComplete=True, work=[{
            "id": "review", "kind": "review", "reportedCostUsd": 1e308,
            "evidenceRef": "review.json"}]))
        view = self.query("overflow")
        json.dumps(view, allow_nan=False)
        cost = view["usageCost"]["combinedReportedCostUsd"]
        self.assertIsNone(cost["value"])
        self.assertIn("overflow", cost["reason"])
        self.assertFalse(pi_outcome._finite(10 ** 1000))

    def test_history_limits_refuse_before_publish_and_allow_replay(self):
        from unittest.mock import patch
        payload = {"status": "unknown", "finishedAt": 1}
        pi_outcome.record_outcome(self.repo.root, self.common, "bounded", payload)
        directory = pi_outcome.outcome_path(self.common, "bounded")
        original = (directory / "revision-000001.json").read_bytes()
        with patch.object(pi_outcome, "MAX_REVISIONS", 1):
            replay = pi_outcome.record_outcome(self.repo.root, self.common, "bounded", payload)
            self.assertTrue(replay["idempotent"])
            with self.assertRaisesRegex(ValueError, "revision count"):
                pi_outcome.record_outcome(self.repo.root, self.common, "bounded",
                                          dict(payload, finishedAt=2), expected_revision=1)
        with patch.object(pi_outcome, "MAX_HISTORY_BYTES", len(original) + 1):
            with self.assertRaisesRegex(ValueError, "cumulative size"):
                pi_outcome.record_outcome(self.repo.root, self.common, "bounded",
                                          dict(payload, finishedAt=2), expected_revision=1)
        self.assertFalse((directory / "revision-000002.json").exists())
        self.assertEqual((directory / "revision-000001.json").read_bytes(), original)
        self.assertEqual(self.query("bounded")["revision"], 1)

    def test_intermediate_alias_and_changed_identity_are_not_member_sources(self):
        import shutil
        a = self.make_task("task-a")
        b = self.make_task("task-b")
        for task in ("task-a", "task-b"):
            self.write_state(task, 1)
            self.write_summary(task, 1)
        self.accept_record("original", self.accepted_payload())
        shutil.rmtree(a / "rounds")
        (a / "rounds").symlink_to(b / "rounds", target_is_directory=True)
        self.record("aliased", self.accepted_payload(), expect=2)
        view = self.query("original")
        self.assertEqual(view["delivery"]["nativeRounds"][0]["state"], "unknown")
        self.assertIsNone(view["usageCost"]["piReported"]["total"]["uncachedInput"]["known"])
        listing = pi_outcome_view.list_outcomes(self.common)
        self.assertEqual(listing["outcomes"][0]["memberSources"]["missing"], ["task-a/1"])
        (a / "rounds").unlink()
        shutil.copytree(b / "rounds", a / "rounds")
        meta = json.loads((a / "task.json").read_text())
        meta["task"] = "task-b"
        (a / "task.json").write_text(json.dumps(meta))
        view = self.query("original")
        self.assertEqual(view["delivery"]["nativeRounds"][0]["state"], "unknown")
        self.assertEqual(pi_outcome_view.list_outcomes(self.common)["outcomes"][0]["memberSources"]["missing"], ["task-a/1"])

    def test_native_receipt_cannot_name_another_round_and_bad_flags_are_unknown(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        foreign = self.write_receipt("task-a", 2, "foreign")
        local = self.write_receipt("task-a", 1, "local")
        self.write_summary("task-a", 1, receipts=[
            self.receipt_meta(foreign, receipt=str(foreign)),
            self.receipt_meta(local, cancelled="false")])
        self.accept_record("receipts", self.accepted_payload())
        verify = self.query("receipts")["verification"]
        self.assertEqual(verify["uniqueReceipts"], 1)
        self.assertEqual(verify["counts"]["passed"], 0)
        self.assertEqual(verify["counts"]["unknown"], 1)
        self.assertFalse(verify["countsComplete"])

    def test_earlier_failed_round_is_not_later_takeover(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1)
        card = pi_events._new_card("task-a", THREAD, "Synthetic", "Synthetic", None, None,
            str(self.repo.root), str(self.repo.state_dir), str(self.repo.root), "offline", None, 1)
        card["codex"] = {"takeover": {"required": True, "cause": "quality_limit", "at": 4,
            "outcome": {"round": 2}, "failedReports": [{"round": 1}, {"round": 2}]}}
        self.seed_card({"task-a": card})
        self.accept_record("earlier", self.accepted_payload())
        intervention = self.query("earlier")["interventionCoverage"]
        self.assertFalse(intervention["nativeTakeover"]["observed"])
        self.assertEqual(intervention["nativeTakeover"]["coverage"]["excluded"], 1)

    def test_handled_review_with_missing_time_has_partial_coverage(self):
        coverage = {"status": "known"}
        payload = {"startedAt": 0, "finishedAt": 20, "work": [], "workComplete": False}
        event = {"kind": "review_required", "createdAt": 1, "handled": True,
                 "decision": "accepted"}
        view = pi_outcome_view._timing_section(payload, [], [event], coverage)
        self.assertEqual(view["reviewWaitSeconds"]["coverage"]["status"], "unknown")
        self.assertEqual(view["reviewWaitSeconds"]["coverage"]["missingBoundaries"], 1)


if __name__ == "__main__":
    unittest.main()
