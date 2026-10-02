"""Deterministic tests for the bounded phase progress echo.

The progress echo is a non-blocking notification path distinct from decision
events: at most two per phase, at least the configured interval apart, merged
while busy, suppressed by pause, deduplicated by phase/milestone, and never
able to report a stale candidate as current. Milestones and failures come from
the normalized phase snapshot (real receipts build the snapshot in the
integration cases). No model, network or real CLI is invoked.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime_helpers import RUNTIME, Repo, cleanup_repos

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402
import pi_task  # noqa: E402

THREAD_A = "11111111-2222-3333-4444-555555555555"
HEAD_A = "a" * 40
HEAD_B = "b" * 40
CONTRACT = "c" * 64

REAL_COMMAND = shlex.join([sys.executable, "-c", "print('ok')"])
REAL_CONTRACT = {
    "schemaVersion": 1, "phaseId": "P-REAL", "goal": "g", "result": "r", "baseline": "HEAD",
    "scope": ["."], "designRef": "docs/design.md", "designSha256": "0" * 64,
    "acceptanceItems": [{"id": "A1", "description": "d", "command": REAL_COMMAND,
                         "passCondition": "exit 0", "evidence": "receipt"}],
    "budgetSeconds": 3600, "commandTimeoutSeconds": 900, "resourceLimits": [],
    "autonomousRepair": ["fix"], "escalateWhen": ["design"],
}


def make_card(task_id: str = "echo-task", *, transport=pi_board.TRANSPORT_CLI_QUEUE,
              paused: bool = False) -> dict:
    card = pi_board._new_card(task_id, THREAD_A, "Title", "Goal", "brief.md", None,
                              "/repo", "/common", "/worktree", transport, "/bin/true", 1)
    card["paused"] = paused
    return card


def covered_item(check_id: str = "A1", candidate: str = HEAD_A) -> dict:
    """A snapshot item that already passed candidate/log/count verification."""
    return {"id": check_id, "checkId": check_id, "status": "covered",
            "reason": "candidate-bound receipt with verified log hash",
            "receiptRef": f"/checks/{check_id}-{candidate[:8]}-pass.json",
            "logRef": f"/checks/{check_id}-{candidate[:8]}-pass.log",
            "attempts": 1, "logVerified": True, "evidenceLevel": "verified_result"}


def failed_item(check_id: str = "A1", candidate: str = HEAD_A) -> dict:
    return {"id": check_id, "checkId": check_id, "status": "failed", "reason": "exit 3",
            "receiptRef": f"/checks/{check_id}-fail.json",
            "logRef": f"/checks/{check_id}-fail.log",
            "attempts": 1, "logVerified": None, "evidenceLevel": "receipt_metadata"}


def base_status(checks_dir: Path, card: dict, *, phase_id: str = "P1", contract: str = CONTRACT,
                candidate: str = HEAD_A, round_number: int = 1, state: str = "running",
                end_head=None, items=None) -> dict:
    status = {
        "task": card["taskId"], "round": round_number, "state": state,
        "recordedState": state, "startHead": candidate, "endHead": end_head,
        "currentHead": None if state in pi_board.TERMINAL_STATES else candidate,
        "ownership": {"activeWorker": True, "supervisorAlive": True},
        "timedOut": False, "cancelled": False,
        "evidence": {"brief": "/evidence/brief.md", "state": "/evidence/state.json",
                     "roundDir": "/evidence/round"},
        "checks": {"dir": str(checks_dir), "running": None,
                   "receipts": {"latest": None, "latestSuccessful": None,
                                "failedRecent": [], "failedAttempts": 0,
                                "unknownExitRecent": [], "recent": []},
                   "resourceGuard": {}},
        "phase": {"phaseId": phase_id, "contractSha256": contract, "candidate": candidate,
                  "contractRef": "/evidence/phase.json", "budget": {}, "autoContinue": None,
                  "lastDecision": None, "readiness": None},
        "progress": None,
    }
    status["candidate"] = pi_task.normalize_candidate(status)
    status["phase"]["candidate"] = status["candidate"].get("head")
    status["phase"]["evidence"] = {
        "schemaVersion": 1, "round": round_number, "phaseId": phase_id,
        "contractSha256": contract, "candidate": status["candidate"],
        "execution": {"state": state, "exitCode": None, "timedOut": False, "cancelled": False},
        "items": list(items or []), "coverage": {}, "gaps": [],
        "scope": {"status": "ok", "outOfScope": []},
        "readiness": {"status": "not_ready", "reason": "test", "coverage": {}, "gaps": [],
                      "scope": "ok", "writerFree": False, "readyForReview": False,
                      "generatedAt": 0},
        "evidenceLevels": {"gptAcceptance": False},
    }
    return status


class ProgressEchoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-echo-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.addCleanup(os.environ.pop, "CODEX_PI_HANDOFF_ROOT", None)
        os.environ["CODEX_PI_HANDOFF_ROOT"] = str(self.tmp / "handoffs")
        self.checks_dir = self.tmp / "checks"

    def tearDown(self):
        cleanup_repos()

    def run_pi_check(self, repo_root: Path, checks: Path, check_id: str = "A1") -> Path:
        checks.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, str(RUNTIME / "pi_check.py"), "--output-dir", str(checks),
             "--id", check_id, "--timeout-seconds", "900",
             "--", *shlex.split(REAL_COMMAND)],
            cwd=str(repo_root), capture_output=True, text=True, env=os.environ.copy(), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return Path(json.loads(proc.stdout)["receipt"])

    def real_status(self, repo: Repo, checks: Path, head: str, task_id: str):
        """Build a status whose phase snapshot comes from real receipts."""
        card = make_card(task_id=task_id)
        scan = pi_task._scan_checks(checks)
        task_dir = self.tmp / f"task-{task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)
        contract = dict(REAL_CONTRACT, phaseId=f"P-{task_id}")
        (task_dir / "phase.json").write_text(json.dumps({
            "schemaVersion": 1, "contract": contract,
            "contractSha256": pi_task.contract_hash(contract),
            "baselineCommit": head, "createdAt": 1}), encoding="utf-8")
        (task_dir / "task.json").write_text(json.dumps({"worktree": str(repo.root)}),
                                            encoding="utf-8")
        state = {"state": "running", "currentHead": head, "startedAt": 1}
        candidate = pi_task.normalize_candidate(state)
        snapshot = pi_task.build_phase_snapshot(task_dir, {"worktree": str(repo.root)}, 1,
                                                state=state, checks=scan, candidate=candidate)
        status = base_status(checks, card, candidate=head)
        status["checks"] = scan
        status["candidate"] = candidate
        status["phase"]["contractSha256"] = snapshot["contractSha256"]
        status["phase"]["candidate"] = candidate.get("head")
        status["phase"]["evidence"] = snapshot
        return card, status

    def echo(self, card, status, start, wait=200, interval=600):
        """Observe a failure at ``start`` and again ``wait`` seconds later.

        Only a sustained anomaly may enqueue an echo; the first observation just
        records it. Returns the second call's result.
        """
        pi_board.maybe_publish_progress(card, status, now=start, interval=interval,
                                        anomaly_seconds=180)
        return pi_board.maybe_publish_progress(card, status, now=start + wait,
                                               interval=interval, anomaly_seconds=180)

    def milestone_keys(self, card, prefix):
        phases = (card.get("notify") or {}).get("phases") or {}
        return [key for entry in phases.values() for key in entry.get("milestones", {})
                if key.startswith(prefix)]

    def head(self, repo: Repo) -> str:
        return subprocess.check_output(["git", "-C", str(repo.root), "rev-parse", "HEAD"],
                                       text=True).strip()

    def board_file(self, card: dict, task_id: str | None = None) -> Path:
        task_id = task_id or card["taskId"]
        board = {"schemaVersion": 1, "revision": 1, "createdAt": 1, "updatedAt": 1,
                 "cards": {task_id: card}}
        path = self.tmp / f"board-{task_id}.json"
        path.write_text(json.dumps(board), encoding="utf-8")
        return path

    def read_card(self, path: Path, task_id: str) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))["cards"][task_id]

    def pending(self, card: dict) -> list:
        return [event for event in card.get("events", []) if not event.get("handled")]

    # ------------------------------------------------------------------
    def test_plain_progress_and_repeated_writes_never_publish(self):
        card = make_card()
        status = base_status(self.checks_dir, card)
        status["progress"] = {"round": 1, "activity": "implementing", "step": "wrote the parser",
                              "evidenceRefs": ["runtime/pi_task.py"], "completedCriteria": [],
                              "updatedAt": 10}
        for now in (100, 101, 102):
            result = pi_board.maybe_publish_progress(card, status, now=now, interval=600,
                                                     anomaly_seconds=180)
            self.assertIsNone(result["created"])
            self.assertIsNone(result["merged"])
        self.assertEqual(self.pending(card), [])
        self.assertEqual(card["notify"]["phases"]["P1"]["count"], 0)

    def test_ordinary_milestone_stays_on_the_board_and_never_enqueues(self):
        card = make_card()
        status = base_status(self.checks_dir, card, items=[covered_item()])
        first = pi_board.maybe_publish_progress(card, status, now=100, interval=600,
                                                anomaly_seconds=180)
        self.assertIsNone(first["created"])
        self.assertIsNone(first["merged"])
        self.assertEqual(self.pending(card), [])
        ledger = card["notify"]["phases"]["P1"]
        self.assertEqual(ledger["count"], 0, "ordinary milestones never spend the quota")
        recorded = [value for key, value in ledger["milestones"].items()
                    if key.startswith("first_result|")]
        self.assertEqual(len(recorded), 1)
        self.assertTrue(recorded[0]["boardOnly"])
        self.assertEqual(recorded[0]["factSource"], "verified_receipt")
        # check/repair self-reports with evidence are board-only too.
        checking = base_status(self.checks_dir, card, items=[covered_item()])
        checking["progress"] = {"round": 1, "activity": "checking", "step": "running tests",
                                "evidenceRefs": ["round.checks/A1.log"], "completedCriteria": [],
                                "updatedAt": 1200}
        again = pi_board.maybe_publish_progress(card, checking, now=1200, interval=600,
                                                anomaly_seconds=180)
        self.assertIsNone(again["created"])
        self.assertEqual(self.pending(card), [])
        self.assertTrue(any(key.startswith("check_checking|") for key in ledger["milestones"]))
        # The board projection shows the milestones without a queue event.
        path = self.board_file(card)
        refresh = pi_board.refresh_with_status(path, card["taskId"], status, now=300, block=False)
        self.assertEqual(refresh["newEvents"], [])

    def test_two_anomalies_interval_merge_and_quota(self):
        card = make_card()
        items = [failed_item(f"A{i}") for i in range(1, 5)]
        status = base_status(self.checks_dir, card, items=items)
        first = self.echo(card, status, 1000, wait=200)
        event_one = first["created"]
        self.assertIsNotNone(event_one)
        self.assertEqual(event_one["evidence"]["reason"], "sustained_anomaly")
        self.assertIn("Anomaly", event_one["question"])
        self.assertIn("pause", event_one["question"])
        # Within the interval a new anomaly is merged into the pending packet,
        # not queued as a second notification.
        merged = pi_board.maybe_publish_progress(card, status, now=1300, interval=600,
                                                 anomaly_seconds=180)
        self.assertIsNone(merged["created"])
        self.assertIsNotNone(merged["merged"])
        self.assertEqual(merged["merged"]["id"], event_one["id"])
        self.assertIn("A2", merged["merged"]["summary"])
        self.assertEqual(card["notify"]["phases"]["P1"]["count"], 1)
        # After the interval the next distinct anomaly is a second notification.
        second = pi_board.maybe_publish_progress(card, status, now=1900, interval=600,
                                                 anomaly_seconds=180)
        event_two = second["created"]
        self.assertIsNotNone(event_two)
        self.assertNotEqual(event_two["id"], event_one["id"])
        self.assertEqual(card["notify"]["phases"]["P1"]["count"], 2)
        self.assertEqual(len(self.pending(card)), 1, "older pending echo is superseded")
        # A third anomaly cannot exceed the two-notification quota.
        third = pi_board.maybe_publish_progress(card, status, now=2600, interval=600,
                                                anomaly_seconds=180)
        self.assertIsNone(third["created"])
        self.assertEqual(card["notify"]["phases"]["P1"]["count"], 2)
        self.assertEqual(len(self.pending(card)), 1)

    def test_quota_persists_across_a_board_round_trip(self):
        card = make_card()
        path = self.board_file(card)
        items = [failed_item("A1"), failed_item("A2")]
        status = base_status(self.checks_dir, card, items=items)
        pi_board.refresh_with_status(path, card["taskId"], status, now=100, block=False)
        refresh = pi_board.refresh_with_status(path, card["taskId"], status, now=300, block=False)
        self.assertEqual(len(refresh["newEvents"]), 1)
        stored = self.read_card(path, card["taskId"])
        self.assertEqual(stored["notify"]["phases"]["P1"]["count"], 1)
        # A fresh read (restart) keeps the spend and continues with the second.
        card2 = self.read_card(path, card["taskId"])
        status2 = base_status(self.checks_dir, card2, items=items)
        second = pi_board.maybe_publish_progress(card2, status2, now=2000, interval=600,
                                                 anomaly_seconds=180)
        self.assertIsNotNone(second["created"])
        self.assertEqual(card2["notify"]["phases"]["P1"]["count"], 2)

    def test_pause_suppresses_and_resume_allows(self):
        card = make_card(paused=True)
        status = base_status(self.checks_dir, card, items=[failed_item()])
        blocked = self.echo(card, status, 100)
        self.assertIsNone(blocked["created"])
        card["paused"] = False
        allowed = self.echo(card, status, 100)
        self.assertIsNotNone(allowed["created"])
        # A route (interrupt) pause is equally blocking.
        route_paused = make_card(task_id="echo-route")
        path = self.board_file(route_paused, "echo-route")
        pi_board.pause_route(THREAD_A, now=1)
        route_status = base_status(self.checks_dir, route_paused, items=[failed_item()])
        blocked = self.echo(route_paused, route_status, 100)
        self.assertIsNone(blocked["created"])
        pi_board.resume_route(THREAD_A)
        allowed = self.echo(route_paused, route_status, 100)
        self.assertIsNotNone(allowed["created"])
        del path

    def test_stale_candidate_is_superseded_not_reported_as_current(self):
        card = make_card(task_id="echo-stale")
        path = self.board_file(card, "echo-stale")
        first_status = base_status(self.checks_dir, card, items=[failed_item()])
        pi_board.refresh_with_status(path, "echo-stale", first_status, now=1000, block=False)
        pi_board.refresh_with_status(path, "echo-stale", first_status, now=1200, block=False)
        self.assertEqual(len(self.pending(self.read_card(path, "echo-stale"))), 1)
        second_status = base_status(self.checks_dir, card, candidate=HEAD_B, round_number=2,
                                    items=[failed_item(candidate=HEAD_B)])
        pi_board.refresh_with_status(path, "echo-stale", second_status, now=1800, block=False)
        refresh = pi_board.refresh_with_status(path, "echo-stale", second_status, now=2000,
                                               block=False)
        stored = self.read_card(path, "echo-stale")
        self.assertTrue(refresh["newEvents"])
        for event in self.pending(stored):
            self.assertNotEqual(event["candidate"]["head"], HEAD_A,
                                "an old-candidate progress echo must never remain current")
        self.assertEqual(stored["notify"]["phases"]["P1"]["count"], 2)

    def test_sustained_anomaly_echoes_once_per_failure_key(self):
        card = make_card()
        status = base_status(self.checks_dir, card, items=[failed_item()])
        # First observation records the failure without waking anyone.
        first = pi_board.maybe_publish_progress(card, status, now=100, interval=600,
                                                anomaly_seconds=180)
        self.assertIsNone(first["created"])
        self.assertTrue(first["changed"])
        early = pi_board.maybe_publish_progress(card, status, now=200, interval=600,
                                                anomaly_seconds=180)
        self.assertIsNone(early["created"])
        echo = pi_board.maybe_publish_progress(card, status, now=300, interval=600,
                                               anomaly_seconds=180)
        self.assertIsNotNone(echo["created"])
        self.assertEqual(echo["created"]["factSource"], "observed_receipt")
        self.assertEqual(echo["created"]["evidence"]["reason"], "sustained_anomaly")
        self.assertEqual(card["notify"]["phases"]["P1"]["count"], 1)
        repeat = pi_board.maybe_publish_progress(card, status, now=500, interval=600,
                                                 anomaly_seconds=180)
        self.assertIsNone(repeat["created"], "the same sustained failure never repeats")

    def test_repair_progress_suppresses_the_sustained_echo(self):
        card = make_card()
        status = base_status(self.checks_dir, card, items=[failed_item()])
        pi_board.maybe_publish_progress(card, status, now=100, interval=600, anomaly_seconds=180)
        repairing = base_status(self.checks_dir, card, items=[failed_item()])
        repairing["progress"] = {"round": 1, "activity": "repairing", "step": "fixing",
                                 "evidenceRefs": ["docs/repair.md"], "completedCriteria": [],
                                 "updatedAt": 150}
        # The repairing update is itself the key repair milestone; the sustained
        # anomaly fallback must not add a second echo for the same failure.
        pi_board.maybe_publish_progress(card, repairing, now=300, interval=600,
                                        anomaly_seconds=180)
        repeat = pi_board.maybe_publish_progress(card, repairing, now=400, interval=600,
                                                 anomaly_seconds=180)
        self.assertIsNone(repeat["created"])
        reasons = [event.get("evidence", {}).get("reason") for event in card.get("events", [])]
        self.assertNotIn("sustained_anomaly", reasons,
                         "verified repair progress suppresses the anomaly echo")
        self.assertIsNone(card["notify"]["phases"]["P1"]["abnormal"][
            "abnormal|%s|%s|A1" % (CONTRACT, HEAD_A)]["notifiedAt"])

    def test_decision_event_bypasses_progress_quota_and_interval(self):
        card = make_card()
        card["notify"] = {"schemaVersion": 1, "phases": {"P1": {
            "phaseId": "P1", "contractSha256": CONTRACT, "count": 2,
            "lastAt": 100, "milestones": {}, "abnormal": {}}}}
        status = base_status(self.checks_dir, card, state="completed", end_head=HEAD_A,
                             items=[covered_item()])
        status["phase"]["readiness"] = {"status": "ready", "candidate": HEAD_A,
                                        "coverage": {"required": 1, "covered": 1},
                                        "writerFree": False, "generatedAt": 100}
        added = pi_board.project_events(card, status, 101)
        kinds = {event["kind"] for event in added}
        self.assertIn("review_required", kinds,
                      "final acceptance must not be suppressed by the progress quota")
        self.assertEqual(card["notify"]["phases"]["P1"]["count"], 2)

    def test_candidate_identity_has_one_fail_closed_source(self):
        running = {"state": "running", "currentHead": None, "startHead": "a" * 40,
                   "endHead": None}
        self.assertIsNone(pi_board._phase_candidate_head(running))
        terminal = {"state": "completed", "currentHead": "b" * 40, "endHead": "c" * 40,
                    "startHead": "a" * 40}
        self.assertEqual(pi_board._phase_candidate_head(terminal), "c" * 40)
        unknown = {"state": "unknown", "currentHead": "b" * 40, "startHead": "a" * 40,
                   "endHead": None}
        self.assertIsNone(pi_board._phase_candidate_head(unknown))

    def test_verified_milestone_requires_a_matching_log(self):
        repo = Repo(self.tmp, name="echo-real")
        head = self.head(repo)
        # A real passing receipt with a matching log is a verified milestone.
        checks = self.tmp / "checks-valid"
        self.run_pi_check(repo.root, checks)
        card, status = self.real_status(repo, checks, head, "echo-real-valid")
        self.assertTrue(pi_board._verified_items(status))
        result = pi_board.maybe_publish_progress(card, status, now=100, interval=600,
                                                 anomaly_seconds=180)
        self.assertIsNone(result["created"], "a verified milestone is board-only now")
        self.assertTrue(self.milestone_keys(card, "first_result|"))
        # A missing log stays unknown and can never claim a verified result.
        checks_missing = self.tmp / "checks-missing"
        receipt_missing = self.run_pi_check(repo.root, checks_missing)
        (checks_missing / json.loads(receipt_missing.read_text())["log"]).unlink()
        card_missing, status_missing = self.real_status(repo, checks_missing, head,
                                                        "echo-real-missing")
        self.assertEqual(pi_board._verified_items(status_missing), [])
        self.assertEqual(status_missing["phase"]["evidence"]["items"][0]["status"], "unknown")
        pi_board.maybe_publish_progress(card_missing, status_missing, now=100,
                                        interval=600, anomaly_seconds=180)
        self.assertEqual(self.milestone_keys(card_missing, "first_result|"), [])
        # A content mismatch is not verified either.
        checks_tampered = self.tmp / "checks-tampered"
        receipt_tampered = self.run_pi_check(repo.root, checks_tampered)
        log_path = checks_tampered / json.loads(receipt_tampered.read_text())["log"]
        log_path.write_bytes(log_path.read_bytes() + b"tampered\n")
        card_tampered, status_tampered = self.real_status(repo, checks_tampered, head,
                                                          "echo-real-tampered")
        self.assertEqual(pi_board._verified_items(status_tampered), [])
        self.assertEqual(status_tampered["phase"]["evidence"]["items"][0]["status"], "unknown")
        pi_board.maybe_publish_progress(card_tampered, status_tampered, now=100,
                                        interval=600, anomaly_seconds=180)
        self.assertEqual(self.milestone_keys(card_tampered, "first_result|"), [])
        # A cancelled exit 0 is not a success.
        checks_cancelled = self.tmp / "checks-cancelled"
        receipt_cancelled = self.run_pi_check(repo.root, checks_cancelled)
        data = json.loads(receipt_cancelled.read_text())
        data["cancelled"] = True
        receipt_cancelled.write_text(json.dumps(data), encoding="utf-8")
        scan = pi_task._scan_checks(checks_cancelled)
        self.assertIsNone(scan["receipts"]["latestSuccessful"])
        self.assertEqual(scan["receipts"]["failedAttempts"], 1)
        card_cancelled, status_cancelled = self.real_status(repo, checks_cancelled, head,
                                                            "echo-real-cancelled")
        self.assertEqual(pi_board._verified_items(status_cancelled), [])
        self.assertEqual(status_cancelled["phase"]["evidence"]["items"][0]["status"], "failed")
        pi_board.maybe_publish_progress(card_cancelled, status_cancelled, now=100,
                                        interval=600, anomaly_seconds=180)
        self.assertEqual(self.milestone_keys(card_cancelled, "first_result|"), [])

    def test_snapshot_items_carry_exit_and_counts_for_the_card(self):
        repo = Repo(self.tmp, name="echo-items")
        head = self.head(repo)
        checks = self.tmp / "checks-items"
        self.run_pi_check(repo.root, checks)
        _card, status = self.real_status(repo, checks, head, "echo-items")
        item = status["phase"]["evidence"]["items"][0]
        self.assertEqual(item["exitCode"], 0)
        info = pi_board._delivery_info(status)
        self.assertEqual(info["items"][0]["id"], "A1")
        self.assertEqual(info["items"][0]["exit"], 0)
        self.assertEqual(info["items"][0]["st"], "covered")
        self.assertEqual(info["more"], 0)

    def test_unverified_receipt_never_dispatches(self):
        repo = Repo(self.tmp, name="echo-real-send")
        head = self.head(repo)
        checks = self.tmp / "checks-nodispatch"
        receipt = self.run_pi_check(repo.root, checks)
        (checks / json.loads(receipt.read_text())["log"]).unlink()
        card, status = self.real_status(repo, checks, head, "echo-nodispatch")
        path = self.board_file(card, "echo-nodispatch")
        refresh = pi_board.refresh_with_status(path, "echo-nodispatch", status, now=100, block=False)
        self.assertEqual(refresh["newEvents"], [])
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return {"status": "queued", "exitCode": 0, "timedOut": False,
                    "outputSha256": "0" * 64, "outputExcerpt": "", "argv0": argv[0]}

        with mock.patch.object(pi_board, "_resolve_codex_bin", return_value="/bin/true"):
            result = pi_board.dispatch_task(path, "echo-nodispatch", cli_runner=runner)
        self.assertFalse(result["dispatched"])
        self.assertEqual(calls, [], "an unverified receipt must never trigger a queue send")

    def test_progress_event_uncertain_send_is_never_auto_resent(self):
        card = make_card(task_id="echo-send")
        path = self.board_file(card, "echo-send")
        status = base_status(self.checks_dir, card, items=[failed_item()])
        pi_board.refresh_with_status(path, "echo-send", status, now=100, block=False)
        refreshed = pi_board.refresh_with_status(path, "echo-send", status, now=300, block=False)
        self.assertEqual(len(refreshed["newEvents"]), 1)
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return {"status": "uncertain", "exitCode": None, "timedOut": True,
                    "error": "ambiguous after spawn", "outputSha256": "0" * 64,
                    "outputExcerpt": "", "argv0": argv[0]}

        with mock.patch.object(pi_board, "_resolve_codex_bin", return_value="/bin/true"):
            first = pi_board.dispatch_task(path, "echo-send", cli_runner=runner)
            second = pi_board.dispatch_task(path, "echo-send", cli_runner=runner)
        self.assertEqual(first["status"], "uncertain")
        self.assertEqual(len(calls), 1)
        self.assertFalse(second["dispatched"])
        queue = pi_board.queue_view(path, "echo-send")
        self.assertEqual(queue["uncertain"], 1)
        self.assertEqual(queue["queued"], 0)


if __name__ == "__main__":
    unittest.main()
