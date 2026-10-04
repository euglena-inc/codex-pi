"""Behavioral tests for the 0.8.5 Main-reported outcome observation view.

Every scenario runs the real ``pi_board.py`` CLI (or the real lower-level API
for bounded paging) against real temporary git repositories and synthetic
fixtures. No real task ids, sessions, credentials, models or Codex queue are
used. A closeout is tested as a reported observation, never as acceptance.
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
import pi_recovery  # noqa: E402

BOARD = RUNTIME / "pi_board.py"
THREAD = "11111111-2222-3333-4444-555555555555"


def run_board(*args, env: dict, expect: int | None = None, timeout: float = 60):
    proc = subprocess.run([sys.executable, str(BOARD), *[str(arg) for arg in args]],
                          capture_output=True, text=True, env=env, timeout=timeout)
    if expect is not None and proc.returncode != expect:
        raise AssertionError(f"pi_board {' '.join(str(arg) for arg in args)} exited "
                             f"{proc.returncode}, expected {expect}\n"
                             f"stdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def board_json(*args, env: dict, timeout: float = 60):
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
        self.common = self.repo.root / ".git"
        self.records = 0

    # ------------------------------------------------------------------
    # fixture helpers
    # ------------------------------------------------------------------
    def make_task(self, task="task-a", rounds=(1,), created_at=100.0):
        task_dir = self.repo.task_dir(task)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task.json").write_text(json.dumps({"task": task, "createdAt": created_at}),
                                            encoding="utf-8")
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

    def write_summary(self, task, number, **overrides):
        summary = {"schema_version": 1, "source": "round.jsonl", "usage": {"input": 10},
                   "usage_complete": True, "reported_cost_usd": 0.25,
                   "models": {"deepseek/deepseek-flash": 2},
                   "expected_model": "deepseek/deepseek-flash",
                   "metrics": {"usage": {"uncachedInput": {"value": 10, "complete": True},
                                         "cacheRead": {"value": 20, "complete": True},
                                         "cacheWrite": {"value": 1, "complete": True},
                                         "output": {"value": 2, "complete": True},
                                         "reasoning": {"value": 1, "complete": True},
                                         "totalTokens": {"value": 33, "complete": True}},
                               "checks": {"attempts": 0, "completed": 0, "passed": 0, "failed": 0,
                                          "elapsedSeconds": {"value": 0.0, "complete": True}},
                               "timing": {"modelResponseSeconds": {"value": 7.0, "complete": True}}},
                   "check_receipts": []}
        summary.update(overrides)
        path = self.repo.task_dir(task) / "rounds" / str(number) / "round.summary.json"
        path.write_text(json.dumps(summary), encoding="utf-8")
        return summary

    def receipt(self, task, number, name, *, argv=None, started=2.0, ended=8.0, exit_code=0,
                head=None, **extra):
        round_dir = self.repo.task_dir(task) / "rounds" / str(number)
        checks = round_dir / "round.checks"
        checks.mkdir(parents=True, exist_ok=True)
        counts = {"run": 4, "pass": 4, "fail": 0, "skip": 0} if exit_code == 0 \
            else {"run": 1, "pass": 0, "fail": 1, "skip": 0}
        data = {"schema_version": 1, "id": name,
                "argv": argv if argv is not None else ["python3", "-m", "unittest", "discover"],
                "head": head if head is not None else self.head, "dirty": False,
                "tracked_diff_sha256": "d" * 64, "started_at": started, "ended_at": ended,
                "deadline_at": ended, "exit_code": exit_code, "timed_out": False,
                "cancelled": False, "signal": None, "error": None, "test_counts": counts,
                "log": name + ".log", "log_sha256": "a" * 64, "running_marker": None,
                "resource_limit": None, "resourceLimit": None, "acceptance": "not_verified"}
        data.update(extra)
        path = checks / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def receipt_meta(self, path, argv, started, ended, exit_code=0):
        return {"id": path.stem, "argv": argv, "head": self.head, "dirty": False,
                "exit_code": exit_code, "timed_out": False, "cancelled": False,
                "started_at": started, "ended_at": ended, "test_counts": {"run": 4},
                "log": path.stem + ".log", "receipt": str(path), "log_verified": True,
                "resource_limit": None}

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
        proc = self.record(outcome, payload, *extra)
        return json.loads(proc.stdout)

    def query(self, outcome, expect=0):
        return board_json("metrics", "--repo", str(self.repo.root), "--outcome", outcome,
                          env=self.env)

    def snapshot(self, root: Path, skip_parts=("outcomes",)) -> dict:
        result = {}
        for path in sorted(Path(root).rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in skip_parts for part in relative.parts):
                continue
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def accepted_payload(self, members=None, **overrides):
        payload = {"members": members if members is not None else [{"task": "task-a", "rounds": [1]}],
                   "status": "accepted", "candidate": self.head, "evidenceRefs": ["plan.md"],
                   "startedAt": 0.0, "finishedAt": 20.0, "workComplete": False}
        payload.update(overrides)
        return payload

    # ------------------------------------------------------------------
    # round trip, arithmetic controls and deduplication
    # ------------------------------------------------------------------
    def test_round_trip_arithmetic_controls_and_shared_receipt_dedup(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1, startedAt=0.0, endedAt=10.0)
        self.write_state("task-a", 2, startedAt=5.0, endedAt=15.0)
        first = self.receipt("task-a", 1, "check-one", argv=["python3", "-m", "unittest", "discover"],
                             started=2.0, ended=8.0)
        second = self.receipt("task-a", 1, "check-two", argv=["python3", "-m", "pytest"],
                              started=5.0, ended=9.0)
        self.write_summary("task-a", 1, check_receipts=[
            self.receipt_meta(first, ["python3", "-m", "unittest", "discover"], 2.0, 8.0),
            self.receipt_meta(second, ["python3", "-m", "pytest"], 5.0, 9.0)])
        self.write_summary("task-a", 2, check_receipts=[])
        relative = str(first.relative_to(self.repo.state_dir))
        payload = self.accepted_payload(
            members=[{"task": "task-a", "rounds": [1, 2]}],
            workComplete=True,
            work=[{"id": "w1", "kind": "review", "actor": "main", "reportedCostUsd": 0.5,
                   "evidenceRef": "main-review-notes.md"}],
            extraChecks=[relative, relative])
        recorded = self.accept_record("delivery-1", payload)
        self.assertFalse(recorded["idempotent"])
        self.assertEqual(recorded["revision"], 1)
        directory = self.repo.state_dir / "outcomes" / "delivery-1"
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         ["revision-000001.json", "write.lock"])

        view = self.query("delivery-1")
        self.assertEqual(view["status"], "accepted")
        self.assertEqual(view["acceptance"], "not_verified")
        self.assertEqual(view["delivery"]["reported"]["source"], "main_reported")
        self.assertEqual([row["state"] for row in view["delivery"]["nativeRounds"]],
                         ["completed", "completed"])
        # Rounds [0,10] and [5,15] share 15 seconds, never 25.
        self.assertEqual(view["timing"]["roundExecutionUnionSeconds"]["value"], 15.0)
        self.assertEqual(view["timing"]["elapsedWallSeconds"], 20.0)
        self.assertTrue(view["timing"]["trueDeliveryLatencyKnown"])
        # Checks [2,8] and [5,9] are 10 seconds of work but 7 seconds of coverage.
        self.assertEqual(view["verification"]["elapsedSeconds"]["value"], 10.0)
        self.assertEqual(view["verification"]["wallCoverageSeconds"]["value"], 7.0)
        self.assertEqual(view["verification"]["attempts"], 2)
        self.assertEqual(view["verification"]["sources"],
                         {"roundSummaryReceipts": 2, "extraCheckReceipts": 0})
        self.assertEqual(view["verification"]["deduplicatedSharedReceipts"], 1)
        # Pi known sums stay partial-aware; the same source is never counted twice.
        self.assertEqual(view["usageCost"]["piReported"]["usage"]["uncachedInput"]["known"], 20.0)
        self.assertTrue(view["usageCost"]["piReported"]["usage"]["uncachedInput"]["complete"])
        self.assertTrue(view["usageCost"]["piReported"]["costUsd"]["complete"])
        self.assertEqual(view["usageCost"]["combinedReportedCostUsd"]["value"], 1.0)
        self.assertFalse(view["usageCost"]["billing"]["verified"])
        self.assertIsNone(view["usageCost"]["billing"]["costUsd"])

    # ------------------------------------------------------------------
    # immutable revisions, idempotent replay, correction and conflicts
    # ------------------------------------------------------------------
    def test_idempotent_replay_correction_and_stale_conflict(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, check_receipts=[])
        payload = self.accepted_payload()
        first = self.accept_record("revisions", payload)
        self.assertEqual((first["revision"], first["idempotent"]), (1, False))
        directory = self.repo.state_dir / "outcomes" / "revisions"
        original = (directory / "revision-000001.json").read_bytes()
        # An uncertain write retry: identical payload with a stale expectation is a no-op.
        replay = json.loads(self.record("revisions", payload, "--expected-revision", "0").stdout)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         ["revision-000001.json", "write.lock"])
        # A correction needs the exact current revision and retains older snapshots.
        corrected = self.accept_record("revisions", self.accepted_payload(status="partial",
                                                                          candidate=None),
                                       "--expected-revision", "1")
        self.assertEqual((corrected["revision"], corrected["idempotent"]), (2, False))
        self.assertEqual((directory / "revision-000001.json").read_bytes(), original)
        self.assertTrue((directory / "revision-000002.json").is_file())
        # A conflicting change with a stale expectation leaves every file untouched.
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        conflict = self.record("revisions", payload, "--expected-revision", "1", expect=2)
        self.assertIn("stale expected revision", conflict.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)
        # A differing payload without an expectation refuses to overwrite revision 2.
        missing = self.record("revisions", payload, expect=2)
        self.assertIn("--expected-revision", missing.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)
        self.assertEqual(self.query("revisions")["revision"], 2)
        self.assertEqual(self.query("revisions")["status"], "partial")

    # ------------------------------------------------------------------
    # malformed, unsafe, oversized and conflicting inputs
    # ------------------------------------------------------------------
    def test_rejects_malformed_unsafe_and_oversized_inputs(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, check_receipts=[])
        base = self.accepted_payload()

        def mutate(**overrides):
            payload = json.loads(json.dumps(base))
            payload.update(overrides)
            return payload

        plain = self.repo.state_dir / "plain.json"
        plain.write_text('{"not": "a receipt"}', encoding="utf-8")

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
            "missing-extra-check": mutate(extraChecks=["tasks/task-a/rounds/1/round.checks/none.json"]),
            "non-receipt-extra-check": mutate(extraChecks=["plain.json"]),
            "bad-expected-revision": None,
        }
        outcome = "rejects"
        for label, payload in cases.items():
            with self.subTest(label=label):
                if label == "bad-expected-revision":
                    self.record(outcome, base, "--expected-revision", "-1", expect=2)
                else:
                    self.record(outcome, payload, expect=2)
                self.assertFalse((self.repo.state_dir / "outcomes" / outcome).exists())
        # Unsafe ids and non-object payloads never create a directory either.
        self.record("../../escape", base, expect=2)
        proc = self.record(outcome, ["not", "an", "object"], expect=2)
        self.assertFalse((self.repo.state_dir / "outcomes" / outcome).exists())
        # Oversized record files are rejected before parsing.
        huge = self.tmp / "huge.json"
        huge.write_text(json.dumps(base) + " " * 1_000_001, encoding="utf-8")
        proc = run_board("record-outcome", "--repo", str(self.repo.root), "--outcome", outcome,
                         "--record-file", str(huge), env=self.env, expect=2)
        self.assertIn("bounded input limit", proc.stderr)

    # ------------------------------------------------------------------
    # missing / corrupt legacy sources stay explicit
    # ------------------------------------------------------------------
    def test_legacy_missing_and_corrupt_sources_stay_explicit(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        self.write_state("task-a", 2)
        self.write_summary("task-a", 1, check_receipts=[])
        self.write_summary("task-a", 2, check_receipts=[])
        payload = self.accepted_payload(members=[{"task": "task-a", "rounds": [1, 2]}])
        self.accept_record("legacy", payload)
        # A source disappearing later is incomplete coverage, never a crash.
        (self.repo.task_dir("task-a") / "rounds" / "2" / "round.state.json").unlink()
        view = self.query("legacy")
        self.assertEqual(view["delivery"]["nativeRoundCoverage"]["status"], "partial")
        self.assertEqual(view["delivery"]["nativeRoundCoverage"]["missing"], ["task-a/2"])
        self.assertEqual(view["delivery"]["reported"]["status"], "accepted")
        # Corrupt or gapped revision history is a hard error for one observation.
        directory = self.repo.state_dir / "outcomes" / "legacy"
        original = (directory / "revision-000001.json").read_bytes()
        (directory / "revision-000001.json").write_text("{not json", encoding="utf-8")
        corrupt = run_board("metrics", "--repo", str(self.repo.root), "--outcome", "legacy",
                            env=self.env, expect=2)
        self.assertIn("revision 1", corrupt.stderr)
        (directory / "revision-000001.json").write_bytes(original)
        # A gap (revision 2 without revision 1) is ambiguous history, not a guess.
        (directory / "revision-000002.json").write_bytes(original)
        (directory / "revision-000001.json").unlink()
        gap = run_board("metrics", "--repo", str(self.repo.root), "--outcome", "legacy",
                        env=self.env, expect=2)
        self.assertIn("gap", gap.stderr)
        # The bounded listing flags the corrupt entry instead of guessing a winner.
        listing = board_json("metrics", "--repo", str(self.repo.root), "--outcomes", env=self.env)
        row, = listing["outcomes"]
        self.assertEqual(row["outcome"], "legacy")
        self.assertEqual(row["status"], "incomplete")
        self.assertIn("gap", row["problem"])
        # An unrecorded observation is a clear error, not an empty fabricated view.
        missing = run_board("metrics", "--repo", str(self.repo.root), "--outcome", "absent",
                            env=self.env, expect=2)
        self.assertIn("not recorded", missing.stderr)

    # ------------------------------------------------------------------
    # failed Pi execution plus successful Main report
    # ------------------------------------------------------------------
    def test_failed_pi_and_main_success_remains_two_separate_facts(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1, state="failed", exitCode=1, startedAt=0.0, endedAt=10.0)
        self.write_summary("task-a", 1, usage=None, usage_complete=False, reported_cost_usd=None,
                           models={}, metrics={"usage": {}, "checks": {
                               "attempts": 1, "completed": 1, "passed": 0, "failed": 1,
                               "elapsedSeconds": {"value": 4.0, "complete": True}}},
                           check_receipts=[])
        self.accept_record("failed-pi", self.accepted_payload())
        view = self.query("failed-pi")
        self.assertEqual(view["delivery"]["reported"]["status"], "accepted")
        self.assertEqual(view["delivery"]["nativeRounds"][0]["state"], "failed")
        self.assertEqual(view["delivery"]["nativeRounds"][0]["exitCode"], 1)
        self.assertEqual(view["interventionCoverage"]["completion"],
                         "main_reported_after_failed_pi_execution")
        self.assertEqual(view["reworkIncidents"]["failedRounds"][0]["state"], "failed")
        # The failed execution is never rewritten into a Pi success or a rejection.
        self.assertNotIn("accepted", json.dumps(view["delivery"]["nativeRounds"]))

    # ------------------------------------------------------------------
    # explicit membership only; no name or predecessor guessing
    # ------------------------------------------------------------------
    def test_explicit_cross_task_membership_and_no_recursive_grouping(self):
        self.make_task("task-a", (1, 2))
        self.make_task("task-b", (1,))
        for task, number in (("task-a", 1), ("task-a", 2), ("task-b", 1)):
            self.write_state(task, number)
            self.write_summary(task, number, check_receipts=[])
        payload = self.accepted_payload(members=[{"task": "task-a", "rounds": [1]},
                                                 {"task": "task-b", "rounds": [1]}])
        self.accept_record("cross-task", payload)
        (self.repo.task_dir("task-b") / "rounds" / "1" / "round.state.json").unlink()
        view = self.query("cross-task")
        self.assertEqual(view["members"], [{"task": "task-a", "rounds": [1]},
                                           {"task": "task-b", "rounds": [1]}])
        rows = view["delivery"]["nativeRounds"]
        self.assertEqual([(row["task"], row["round"]) for row in rows],
                         [("task-a", 1), ("task-b", 1)])
        self.assertEqual(rows[0]["state"], "completed")
        self.assertEqual(rows[1]["state"], "unknown")
        self.assertEqual(view["delivery"]["nativeRoundCoverage"]["missing"], ["task-b/1"])
        # Task-a round 2 exists but was not recorded: it is never inferred.
        self.assertEqual(view["delivery"]["nativeRoundCoverage"]["known"], ["task-a/1"])
        # Direct Codex-only work is valid with no Pi members at all.
        self.accept_record("codex-only", self.accepted_payload(members=[]))
        codex = self.query("codex-only")
        self.assertEqual(codex["members"], [])
        self.assertFalse(codex["usageCost"]["piReported"]["applicable"])
        self.assertEqual(codex["interventionCoverage"]["completion"], "main_reported")
        self.assertEqual(codex["delivery"]["nativeRounds"], [])

    # ------------------------------------------------------------------
    # partial external cost, unknown stays unknown, combined only when complete
    # ------------------------------------------------------------------
    def test_partial_costs_unknown_stays_null_and_combined_needs_completeness(self):
        self.make_task("task-a", (1, 2))
        self.write_state("task-a", 1)
        self.write_state("task-a", 2)
        self.write_summary("task-a", 1, check_receipts=[])
        self.write_summary("task-a", 2, usage=None, usage_complete=False, reported_cost_usd=None,
                           metrics={"usage": {}, "checks": {}}, check_receipts=[])
        payload = self.accepted_payload(members=[{"task": "task-a", "rounds": [1, 2]}],
                                        workComplete=False,
                                        work=[{"id": "w1", "kind": "review",
                                               "reportedCostUsd": 0.5, "evidenceRef": "r.md"}])
        self.accept_record("partial-cost", payload)
        view = self.query("partial-cost")
        self.assertFalse(view["usageCost"]["piReported"]["costUsd"]["complete"])
        self.assertEqual(view["usageCost"]["piReported"]["costUsd"]["known"], 0.25)
        self.assertIsNone(view["usageCost"]["combinedReportedCostUsd"]["value"])
        self.assertIn("incomplete", view["usageCost"]["combinedReportedCostUsd"]["reason"])
        self.assertTrue(view["interventionCoverage"]["missingExternalCoverage"])
        # A complete Pi cost plus a declared-complete external report can combine.
        self.make_task("task-c", (1,))
        self.write_state("task-c", 1)
        self.write_summary("task-c", 1, check_receipts=[])
        complete = self.accepted_payload(members=[{"task": "task-c", "rounds": [1]}],
                                         workComplete=True,
                                         work=[{"id": "w1", "kind": "review",
                                                "reportedCostUsd": 0.5, "evidenceRef": "r.md"}])
        self.accept_record("complete-cost", complete)
        combined = self.query("complete-cost")
        self.assertEqual(combined["usageCost"]["combinedReportedCostUsd"]["value"], 0.75)
        self.assertTrue(combined["usageCost"]["externalReported"]["complete"])
        self.assertFalse(combined["interventionCoverage"]["missingExternalCoverage"])

    # ------------------------------------------------------------------
    # repeated clean checks are potential repeats, never waste
    # ------------------------------------------------------------------
    def test_repeated_clean_checks_are_potential_repeats_only(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]
        first = self.receipt("task-a", 1, "repeat-one", argv=argv, started=0.0, ended=5.0)
        second = self.receipt("task-a", 1, "repeat-two", argv=list(argv), started=6.0, ended=11.0)
        other = self.receipt("task-a", 1, "distinct", argv=["python3", "scripts/check.py"],
                             started=12.0, ended=14.0)
        failed = self.receipt("task-a", 1, "failed-same-argv", argv=list(argv), started=15.0,
                              ended=16.0, exit_code=3)
        self.write_summary("task-a", 1, check_receipts=[
            self.receipt_meta(first, argv, 0.0, 5.0),
            self.receipt_meta(second, argv, 6.0, 11.0),
            self.receipt_meta(other, ["python3", "scripts/check.py"], 12.0, 14.0),
            self.receipt_meta(failed, argv, 15.0, 16.0, exit_code=3)])
        self.accept_record("repeats", self.accepted_payload())
        view = self.query("repeats")
        repeats = view["verification"]["potentialRepeats"]
        self.assertEqual(len(repeats), 1)
        self.assertEqual(repeats[0]["count"], 2)
        self.assertEqual(repeats[0]["argv"], argv)
        self.assertEqual(repeats[0]["classification"], "potential_repeat")
        # A failed attempt with the same argv is not a clean repeat.
        self.assertNotIn("failed-same-argv", json.dumps(repeats))
        self.assertEqual(view["verification"]["failed"], 1)
        self.assertEqual(view["verification"]["attempts"], 4)
        self.assertNotIn("waste", repeats[0])

    # ------------------------------------------------------------------
    # archived decisions beyond the visible card window
    # ------------------------------------------------------------------
    def test_archived_decisions_are_paged_beyond_the_card_window(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, check_receipts=[])
        card = pi_events._new_card("task-a", THREAD, "Synthetic", "Synthetic", None, None,
                                   str(self.repo.root), str(self.repo.state_dir),
                                   str(self.repo.root), "offline", None, 1)
        card["pi"] = {"round": 1, "state": "completed"}
        board = {"schemaVersion": 1, "revision": 10, "createdAt": 1, "updatedAt": 1,
                 "cards": {"task-a": card}}
        board_file = self.repo.state_dir / "board.json"
        board_file.write_text(json.dumps(board), encoding="utf-8")
        pi_recovery.recover_store(self.repo.root)
        # 55 archived decisions, more than one PAGE_SIZE (50) and more than the
        # visible card event window. Reading only the card window would lose some.
        with pi_archive.connection(board_file, write=True) as db:
            for number in range(1, 56):
                event_id = f"{number:064x}"
                body = json.dumps({"at": float(number),
                                   "decision": "rejected" if number <= 40 else "accepted",
                                   "round": number, "eventKind": "review_required",
                                   "failureKind": "quality", "reviewedHead": self.head})
                db.execute("INSERT OR REPLACE INTO decisions VALUES(?,?,?)",
                           ("task-a", event_id, body))
        self.accept_record("archived", self.accepted_payload())
        view = self.query("archived")
        decisions = view["delivery"]["nativeDecisions"]
        self.assertEqual(decisions["coverage"]["status"], "known")
        self.assertEqual(decisions["counts"]["rejected"], 40)
        self.assertEqual(decisions["counts"]["accepted"], 15)
        self.assertEqual(decisions["firstReviewedAcceptance"]["round"], 41)
        self.assertEqual(view["reworkIncidents"]["reviewedQualityFailures"]["count"], 40)

    # ------------------------------------------------------------------
    # read-only: no board/execution mutation, no session scan, no model call
    # ------------------------------------------------------------------
    def test_metrics_and_record_do_not_mutate_or_scan_sessions(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, check_receipts=[])
        session = self.repo.task_dir("task-a") / "session"
        session.mkdir(parents=True, exist_ok=True)
        session_file = session / "round.jsonl"
        session_file.write_text('{"type": "message", "text": "' + "x" * 1000 + '"}\n',
                               encoding="utf-8")
        (self.repo.task_dir("task-a") / "rounds" / "1" / "round.jsonl").write_text(
            "raw transcript bytes that must never be parsed for metrics\n", encoding="utf-8")
        before = self.snapshot(self.common)
        self.accept_record("readonly", self.accepted_payload())
        after_record = self.snapshot(self.common)
        self.assertEqual(after_record, before)
        self.query("readonly")
        board_json("metrics", "--repo", str(self.repo.root), "--task", "task-a", env=self.env)
        self.assertEqual(self.snapshot(self.common), before)
        # An unreadable raw session must not matter: no scan, no error, no model.
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
    def test_listing_is_bounded_with_a_cursor(self):
        self.make_task("task-a", (1,))
        self.write_state("task-a", 1)
        self.write_summary("task-a", 1, check_receipts=[])
        for name in ("alpha", "beta", "gamma"):
            self.accept_record(name, self.accepted_payload(status="partial", candidate=None))
        page_one = pi_outcome.list_outcomes(self.common, limit=2)
        self.assertEqual([row["outcome"] for row in page_one["outcomes"]], ["alpha", "beta"])
        self.assertEqual(page_one["nextCursor"], "beta")
        page_two = pi_outcome.list_outcomes(self.common, cursor="beta", limit=2)
        self.assertEqual([row["outcome"] for row in page_two["outcomes"]], ["gamma"])
        self.assertIsNone(page_two["nextCursor"])
        self.assertEqual(page_one["pageSize"], 2)
        listing = board_json("metrics", "--repo", str(self.repo.root), "--outcomes", env=self.env)
        self.assertEqual([row["outcome"] for row in listing["outcomes"]],
                         ["alpha", "beta", "gamma"])
        self.assertNotIn("successRate", json.dumps(listing))


if __name__ == "__main__":
    unittest.main()
