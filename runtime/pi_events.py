"""Board card and event model: card creation, idempotent event publication, the
projection of task status into card, phase and events, and bounded progress echoes.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import time
from itertools import islice
from pathlib import Path

from pi_core import ACTIVE_STATES, TERMINAL_STATES
from pi_evidence import normalize_candidate
from pi_store import (
    BOARD_CLI,
    FULL_OID_RE,
    MAX_HANDLED_EVENTS,
    MAX_HANDLED_IDS,
    MAX_PENDING_DISPLAY,
    MAX_SUMMARY,
    REVIEW_KINDS,
    TRANSPORT_CLI_QUEUE,
    _text,
    route_paused,
)


MAX_PROGRESS_NOTIFICATIONS = 2
PROGRESS_NOTIFY_INTERVAL_SECONDS = 600.0
PROGRESS_ANOMALY_SECONDS = 180.0
MAX_NOTIFY_MILESTONES = 50
MAX_NOTIFY_ABNORMAL = 50
PROGRESS_EVENT_KIND = "progress_update"


# ---------------------------------------------------------------------------
# card / event model
# ---------------------------------------------------------------------------

def _default_codex() -> dict:
    return {"review": "pending", "reviewedHead": None, "decidedAt": None, "lastEventId": None,
            "lastDecision": None, "lastDecisionAt": None, "history": []}


def _new_card(task_id, thread, title, goal, brief_ref, plan_ref, repo, common, worktree,
              transport, codex_bin, now) -> dict:
    card = {
        "taskId": task_id, "ownerThread": thread, "codexTaskId": thread,
        "title": _text(title or task_id, 200), "goal": _text(goal or "", 600),
        "planRef": plan_ref, "briefRef": brief_ref,
        "repo": str(repo), "commonDir": str(common), "worktree": worktree,
        "transport": transport, "codexBin": codex_bin,
        "createdAt": now, "updatedAt": now,
        "paused": False, "pausedAt": None, "pauseNote": None,
        "pi": {"round": None, "state": None, "stage": None, "updatedAt": None},
        "evidence": {}, "check": None, "events": [], "handled": {}, "overflow": None,
        "nextSeq": 1, "attention": None, "codex": _default_codex(),
    }
    return card


def event_identity(task_id: str, round_number, kind: str, fingerprint: str) -> str:
    raw = "\x00".join((task_id, str(round_number), kind, str(fingerprint)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_event(card: dict, event_id: str):
    for event in card.get("events", []):
        if isinstance(event, dict) and event.get("id") == event_id:
            return event
    lookup=getattr(card,"lookup_event",None)
    return lookup(event_id) if lookup else None


def pending_events(card: dict) -> list:
    return [event for event in card.get("events", [])
            if isinstance(event, dict) and not event.get("handled")]


def _prune_events(card: dict) -> None:
    """Prune handled history only; unhandled events are never discarded."""
    if hasattr(card,"policy_base"):
        # The store persists every loaded/new event; only the next read window
        # is bounded, never its durable records.
        return
    events = [event for event in card.get("events", []) if isinstance(event, dict)]
    handled_map = card.setdefault("handled", {})
    handled = [event for event in events if event.get("handled")]
    for event in handled:
        handled_map[event['id']] = {**handled_map.get(event['id'],{}),**event,
            "at": event.get("handledAt") or 0, "decision": event.get("decision"),
            "eventKind":event.get('kind'),
            "reviewedHead": event.get("reviewedHead"), "round": event.get("round")}
    unhandled = [event for event in events if not event.get("handled")]
    keep_handled = handled[-MAX_HANDLED_EVENTS:]
    card["events"] = sorted(unhandled + keep_handled, key=lambda event: event.get("seq") or 0)


def _update_overflow(card: dict, now: float) -> None:
    count = len(pending_events(card))
    if count > MAX_PENDING_DISPLAY:
        card["overflow"] = {"active": True, "pendingCount": count, "updatedAt": now,
                            "note": "pending events exceed the display budget; the board retains "
                                    "all of them and requires explicit decisions"}
    elif isinstance(card.get("overflow"), dict) and card["overflow"].get("active"):
        card["overflow"] = {"active": False, "pendingCount": count, "resolvedAt": now}


def add_event(card: dict, kind: str, round_number, fingerprint: str, summary: str,
              candidate: dict, evidence: dict, question: str, now: float):
    """Idempotent publication: one immutable identity per task/round/kind/fingerprint."""
    identity = event_identity(card["taskId"], round_number, kind, fingerprint)
    if find_event(card, identity) is not None:
        return None
    if identity in (card.get("handled") or {}):
        return None
    seq = int(card.get("nextSeq") or 1)
    event = {
        "id": identity, "seq": seq, "round": round_number, "kind": kind,
        "fingerprint": _text(fingerprint, 200), "summary": _text(summary, MAX_SUMMARY),
        "question": _text(question, MAX_SUMMARY),
        "candidate": candidate, "evidence": evidence, "createdAt": now,
        "handled": False, "handledAt": None, "decision": None, "reviewedHead": None,
        "note": None,
    }
    card.setdefault("events", []).append(event)
    card["nextSeq"] = seq + 1
    card["attention"] = {"seq": seq, "kind": kind, "eventId": identity,
                         "summary": event["summary"], "updatedAt": now}
    codex = card.setdefault("codex", _default_codex())
    codex["review"] = "pending"
    _prune_events(card)
    _update_overflow(card, now)
    return event


def _review_episode(card: dict) -> int:
    """Durable ready-episode number for review-event identities.

    The counter only advances when a pending review event is invalidated. It is
    stored on the card so a recovered ready state on the SAME round/candidate
    publishes exactly one new event while unchanged refreshes stay idempotent.
    """
    value = card.get("reviewEpisode")
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10 ** 9:
        return value
    return 0


def derive_stage(state, running) -> str:
    if state == "starting":
        return "starting"
    if state == "running":
        return "checking" if running is not None else "implementing"
    if state == "completed":
        return "review"
    if state == "failed":
        return "repair"
    if state == "timed_out":
        return "timed_out"
    if state == "cancelled":
        return "cancelled"
    return "unknown"


def _receipt_ref(checks: dict, latest):
    if not isinstance(latest, dict):
        return None
    checks_dir = checks.get("dir")
    name = latest.get("receipt")
    if isinstance(checks_dir, str) and isinstance(name, str):
        return str(Path(checks_dir) / name)
    return None


def _phase_projection(card: dict, status: dict, now: float):
    """Stable phase card projection; returns (phase_dict_or_None, changed)."""
    phase = status.get("phase")
    if not isinstance(phase, dict) or not phase.get("phaseId"):
        return None, bool(card.get("phase"))
    readiness = phase.get("readiness") or {}
    candidate = _phase_candidate_head(status)
    scope_value = readiness.get("scope")
    if isinstance(scope_value, dict):
        scope_value = scope_value.get("status")
    existing = card.get("phase") if isinstance(card.get("phase"), dict) else {}
    phase_evidence = phase.get("evidence") if isinstance(phase.get("evidence"), dict) else {}
    resource_evidence = phase_evidence.get("resource")
    same_identity = (existing.get("phaseId") == phase.get("phaseId")
                     and existing.get("contractHash") == phase.get("contractSha256"))
    if same_identity and existing.get("status") == "accepted" \
            and existing.get("acceptedHead") == candidate:
        state = "accepted"
    elif same_identity and existing.get("status") == "changes_requested" \
            and existing.get("candidate") == candidate:
        state = "changes_requested"
    elif same_identity and existing.get("status") == "rejected" \
            and existing.get("candidate") == candidate:
        state = "rejected"
    elif readiness.get("status") == "ready" and status.get("state") == "completed":
        state = "review_ready"
    elif status.get("state") in ACTIVE_STATES:
        state = "executing"
    else:
        state = "blocked"
    new_phase = {
        "phaseId": phase.get("phaseId"), "contractHash": phase.get("contractSha256"),
        "contractRef": phase.get("contractRef"), "baselineCommit": phase.get("baselineCommit"),
        "candidate": candidate, "status": state,
        "acceptedHead": existing.get("acceptedHead") if same_identity else None,
        "acceptedAt": existing.get("acceptedAt") if same_identity else None,
        "reviewEventId": existing.get("reviewEventId") if same_identity else None,
        "decidedEventId": existing.get("decidedEventId") if same_identity else None,
        "lastDecision": existing.get("lastDecision") if same_identity else None,
        "readiness": {"status": readiness.get("status"),
                      "coverage": readiness.get("coverage"),
                      "writerFree": readiness.get("writerFree"),
                      "execution": (readiness.get("execution") or {}).get("status"),
                      "resource": resource_evidence,
                      "gaps": [{"id": item.get("id"), "status": item.get("status"),
                                "reason": item.get("reason")}
                               for item in (readiness.get("gaps") or [])[:10]],
                      "scope": scope_value,
                      "generatedAt": readiness.get("generatedAt")},
        "budget": phase.get("budget") or {},
        "updatedAt": now,
    }
    stable_keys = ("phaseId", "contractHash", "contractRef", "baselineCommit", "candidate",
                   "status", "acceptedHead", "acceptedAt", "reviewEventId", "decidedEventId")
    changed = any(existing.get(key) != new_phase.get(key) for key in stable_keys)
    if not changed:
        old_readiness = existing.get("readiness") or {}
        new_readiness = new_phase.get("readiness") or {}
        if (old_readiness.get("status"), old_readiness.get("coverage"), old_readiness.get("scope"),
                (old_readiness.get("execution") or {}), (old_readiness.get("resource") or {}).get("status")) \
                != (new_readiness.get("status"), new_readiness.get("coverage"),
                    new_readiness.get("scope"), (new_readiness.get("execution") or {}),
                    (new_readiness.get("resource") or {}).get("status")):
            changed = True
    return new_phase, changed


def project_status(card: dict, status: dict, now: float) -> bool:
    """Projection only (round/state/stage/evidence/check/progress/phase); never an event."""
    changed = False
    pi = card.setdefault("pi", {})
    new_pi = {
        "round": status.get("round"),
        "state": status.get("state"),
        "stage": derive_stage(status.get("state"), (status.get("checks") or {}).get("running")),
        "updatedAt": now,
    }
    if any(pi.get(key) != new_pi[key] for key in ("round", "state", "stage")):
        changed = True
    pi.update(new_pi)

    checks = status.get("checks") or {}
    receipts = checks.get("receipts") or {}
    latest = receipts.get("latest")
    evidence_dir = status.get("evidence") or {}
    new_evidence = {
        "candidateHead": _phase_candidate_head(status),
        "worktree": status.get("worktree"),
        "taskDir": evidence_dir.get("taskDir"),
        "roundDir": evidence_dir.get("roundDir"),
        "briefRef": evidence_dir.get("brief"),
        "checksRef": checks.get("dir"),
        "stateRef": evidence_dir.get("state"),
        "receiptRef": _receipt_ref(checks, latest),
        "latestCheckId": latest.get("id") if isinstance(latest, dict) else None,
    }
    if card.get("evidence") != new_evidence:
        changed = True
    card["evidence"] = new_evidence

    running = checks.get("running")
    new_check = None if not isinstance(running, dict) else {
        "id": running.get("id"), "marker": running.get("marker"),
        "pidAlive": running.get("pidAlive"), "startedAt": running.get("startedAt"),
        "deadlineAt": running.get("deadlineAt"), "resourceLimit": running.get("resourceLimit"),
    }
    if card.get("check") != new_check:
        changed = True
    card["check"] = new_check

    progress = status.get("progress")
    new_progress = None if not isinstance(progress, dict) else {
        "activity": progress.get("activity"), "step": progress.get("step"),
        "completedCriteria": progress.get("completedCriteria") or [],
        "next": progress.get("next"), "blocker": progress.get("blocker"),
        "updatedAt": progress.get("updatedAt"),
        "reportedBy": progress.get("reportedBy", "pi"), "verified": False,
    }
    if card.get("progress") != new_progress:
        changed = True
    card["progress"] = new_progress

    new_phase, phase_changed = _phase_projection(card, status, now)
    if phase_changed:
        changed = True
    if new_phase is None:
        card.pop("phase", None)
    else:
        card["phase"] = new_phase
    card["updatedAt"] = now
    return changed


def _phase_event_stale(card: dict, event: dict) -> bool:
    """Mechanical staleness for phase-bound events (never an acceptance decision)."""
    if not event.get("phaseId"):
        return False
    phase = card.get("phase") if isinstance(card.get("phase"), dict) else {}
    if event.get("phaseId") != phase.get("phaseId"):
        return True
    if event.get("contractHash") != phase.get("contractHash"):
        return True
    if event.get("round") != (card.get("pi") or {}).get("round"):
        return True
    candidate = (event.get("candidate") or {}).get("head")
    if candidate is not None and candidate != phase.get("candidate"):
        return True
    return False


def _mark_superseded(card: dict, event: dict, now: float) -> None:
    event.update(handled=True, handledAt=now, decision="superseded",
                 note="mechanical supersession: the phase candidate/contract advanced or newer "
                      "facts arrived; this is not an acceptance decision")
    card.setdefault("handled", {})[event["id"]] = {
        "at": now, "decision": "superseded", "reviewedHead": None,
        "round": event.get("round"), "superseded": True}


def _supersede_stale_phase_events(card: dict, now: float) -> int:
    count = 0
    for event in pending_events(card):
        if event.get("phaseId") and _phase_event_stale(card, event):
            _mark_superseded(card, event, now)
            count += 1
    if count:
        _prune_events(card)
        _update_overflow(card, now)
    return count


def _supersede_phase_kinds(card: dict, now: float, kinds, keep_event_id=None,
                           keep_fingerprint=None) -> int:
    count = 0
    for event in pending_events(card):
        if not event.get("phaseId") or event.get("kind") not in kinds:
            continue
        if keep_event_id and event.get("id") == keep_event_id:
            continue
        if keep_fingerprint and event.get("fingerprint") == keep_fingerprint:
            continue
        _mark_superseded(card, event, now)
        count += 1
    if count:
        _prune_events(card)
        _update_overflow(card, now)
    return count


def _phase_blocked_reason(readiness: dict, status: dict) -> str:
    resource = readiness.get("resource") or {}
    resource_status = resource.get("status")
    if resource_status == "breached":
        return "resource_breached"
    if resource_status == "escalated":
        return "resource_unknown"
    execution = readiness.get("execution") or {}
    execution_status = execution.get("status")
    if execution_status == "failed":
        return "round_execution_failed"
    if execution_status == "unknown":
        return "round_execution_unknown"
    if execution_status == "not_terminal":
        return "round_not_terminal"
    last = (status.get("phase") or {}).get("lastDecision") or {}
    if last.get("reason") and last.get("reason") != "ready":
        # The script's post-round classification (for example a pause or a
        # budget stop) is authoritative for that round's delivery gap. A stale
        # "ready" is not a blocked reason once the current receipts disagree.
        return str(last["reason"])
    scope = readiness.get("scope")
    if isinstance(scope, dict):
        scope = scope.get("status")
    if scope == "violation":
        return "scope_violation"
    if resource_status == "unknown":
        return "resource_unknown"
    items = readiness.get("items")
    if not isinstance(items, list):
        evidence = (status.get("phase") or {}).get("evidence")
        items = evidence.get("items") if isinstance(evidence, dict) else None
    statuses = {item.get("status") for item in items or [] if isinstance(item, dict)}
    if statuses & {"failed", "skipped", "unknown"}:
        return "required_check_failed"
    budget = readiness.get("budget") or {}
    if budget.get("exhausted"):
        return "budget_exhausted"
    if status.get("state") != "completed":
        return f"round_{status.get('state')}"
    if readiness.get("status") == "unknown":
        return "readiness_unknown"
    return "delivery_gap"


MAX_CARD_ITEMS = 8


def _delivery_info(status: dict) -> dict:
    """Per-acceptance-item facts the delivery card prints (id/status/exit/counts).

    Phase tasks use the normalized snapshot items; brief-only tasks use the latest
    receipt per check id. Unknown stays unknown; nothing is invented.
    """
    items = []
    snapshot = _snapshot_items(status)
    if snapshot:
        for item in snapshot:
            items.append({"id": item.get("id") or item.get("checkId"),
                          "st": item.get("status"), "exit": item.get("exitCode"),
                          "counts": item.get("testCounts")})
    else:
        latest = {}
        for receipt in ((status.get("checks") or {}).get("receipts") or {}).get("recent") or []:
            if isinstance(receipt, dict) and receipt.get("id"):
                latest[receipt["id"]] = receipt
        for check_id, receipt in latest.items():
            code = receipt.get("exitCode")
            st = "unknown" if code is None else ("failed" if code != 0 else "covered")
            items.append({"id": check_id, "st": st, "exit": code,
                          "counts": receipt.get("testCounts")})
    more = max(0, len(items) - MAX_CARD_ITEMS)
    return {"items": items[:MAX_CARD_ITEMS], "more": more}


def _project_phase_events(card: dict, status: dict, now: float) -> list:
    """Phase-aware classification: local failures stay local; only real decisions escalate."""
    added = []
    state = status.get("state")
    recorded = status.get("recordedState")
    checks = status.get("checks") or {}
    receipts = checks.get("receipts") or {}
    latest = receipts.get("latest")
    evidence_dir = status.get("evidence") or {}
    phase = status.get("phase") or {}
    readiness = phase.get("readiness") or {}
    phase_evidence = phase.get("evidence") if isinstance(phase.get("evidence"), dict) else {}
    resource_evidence = phase_evidence.get("resource")
    phase_id = phase.get("phaseId")
    contract_hash = phase.get("contractHash") or phase.get("contractSha256")
    candidate = _phase_candidate_head(status)
    base_evidence = {"briefRef": evidence_dir.get("brief"), "checksRef": checks.get("dir"),
                     "stateRef": evidence_dir.get("state"), "phaseRef": phase.get("contractRef")}

    def add(kind, fingerprint, summary, question, extra=None):
        evidence = dict(base_evidence)
        if extra:
            evidence.update(extra)
        event = add_event(card, kind, status.get("round"), fingerprint, summary,
                          {"round": status.get("round"), "head": candidate, "state": state},
                          evidence, question, now)
        if event is not None:
            event["phaseId"] = phase_id
            event["contractHash"] = contract_hash
            event["coverage"] = readiness.get("coverage")
            event["delivery"] = _delivery_info(status)
            added.append(event)
        return event

    if status.get("timedOut"):
        add("task_timeout",
            f"phase:{phase_id}:contract:{contract_hash}:timeout:{status.get('exitCode')}",
            f"round {status.get('round')} wrapper timed out (exit {status.get('exitCode')})",
            "Decide whether to repair, cancel or extend the deadline; the round state is exact "
            "evidence.",
            {"receiptRef": _receipt_ref(checks, latest)})
    if state != "completed" and (status.get("executionEvidence") or {}).get("status") in ("failed", "unknown"):
        _supersede_phase_kinds(card,now,("review_required","phase_blocked"))
        fault = status["executionEvidence"]
        add("execution_failed", f"execution:{fault.get('reason')}:{fault.get('tailSha256')}",
            f"round {status.get('round')} execution incomplete: {fault.get('error')}",
            "Resolve the execution incident separately from quality review. Inspect the preserved "
            "stream; any authorized retry uses the same session, worktree and remaining budget.",
            {"executionEvidence": fault,"reason":_phase_blocked_reason(readiness,status),
             "resource":resource_evidence or None,"coverage":readiness.get('coverage'),
             "gaps":readiness.get('gaps')})
    elif state in TERMINAL_STATES:
        if readiness.get("status") == "ready" and state == "completed":
            _supersede_phase_kinds(card, now, ("phase_blocked",))
            event = add(
                "review_required",
                f"phase:{phase_id}:contract:{contract_hash}:candidate:{candidate}:"
                f"ready:{_review_episode(card)}",
                f"phase {phase_id} round {status.get('round')} is delivery-ready: "
                f"{readiness.get('coverage', {}).get('covered')}/"
                f"{readiness.get('coverage', {}).get('required')} acceptance items covered",
                "Review the exact candidate and receipts, then decide accept or changes_requested with "
                "the phase identity bound. Exit 0 and readiness are execution evidence only, never "
                "acceptance.",
                {"candidateHead": candidate, "coverage": readiness.get("coverage"),
                 "phaseId": phase_id, "contractHash": contract_hash})
            if event is not None:
                card["phase"] = dict(card.get("phase") or {}, reviewEventId=event["id"])
        else:
            reason = _phase_blocked_reason(readiness, status)
            fingerprint = (f"phase:{phase_id}:contract:{contract_hash}:candidate:{candidate}:"
                           f"blocked:{reason}")
            # A review event from a previous, now-invalid state must not remain
            # current: the same snapshot gate owns both sides. Superseding a
            # pending review advances the durable ready episode so a later
            # recovery on this same round/candidate can publish exactly one new
            # review event instead of being rejected by the old identity.
            superseded_reviews = _supersede_phase_kinds(card, now, ("review_required",))
            if superseded_reviews:
                card["reviewEpisode"] = _review_episode(card) + 1
            _supersede_phase_kinds(card, now, ("phase_blocked",), keep_fingerprint=fingerprint)
            add("phase_blocked", fingerprint,
                f"phase {phase_id} round {status.get('round')} is not delivery-ready ({reason})",
                "Decide whether to continue repairs in the same Pi session (changes_requested), "
                "escalate for main design help, or explicitly accept a partial result. "
                "Missing, failed, skipped or unknown evidence is never ready.",
                {"candidateHead": candidate, "reason": reason,
                 "coverage": readiness.get("coverage"), "gaps": readiness.get("gaps"),
                 "resource": resource_evidence or None})
    ownership = status.get("ownership") or {}
    if state == "unknown" and recorded in ACTIVE_STATES:
        add("ownership_unknown", f"phase:{phase_id}:{recorded}",
            f"recorded active state {recorded} without a live supervisor lease",
            "Inspect ownership and decide cleanup; do not assume progress.",
            {"recordedState": recorded})
    if recorded in TERMINAL_STATES and ownership.get("activeWorker") \
            and not ownership.get("supervisorAlive"):
        add("ownership_lingering", f"phase:{phase_id}:{recorded}",
            "terminal record but a worker lock is still held",
            "Inspect the lingering owned worker before reuse.",
            {"recordedState": recorded})
    return added


def _phase_progress_budget_seconds() -> float:
    raw = os.environ.get("CODEX_PI_PROGRESS_NOTIFY_SECONDS")
    try:
        value = float(raw) if raw else PROGRESS_NOTIFY_INTERVAL_SECONDS
    except (TypeError, ValueError):
        value = PROGRESS_NOTIFY_INTERVAL_SECONDS
    return value if 0.0 <= value <= 86400.0 else PROGRESS_NOTIFY_INTERVAL_SECONDS


def _phase_anomaly_seconds() -> float:
    raw = os.environ.get("CODEX_PI_PROGRESS_ANOMALY_SECONDS")
    try:
        value = float(raw) if raw else PROGRESS_ANOMALY_SECONDS
    except (TypeError, ValueError):
        value = PROGRESS_ANOMALY_SECONDS
    return value if 0.0 <= value <= 86400.0 else PROGRESS_ANOMALY_SECONDS


def _phase_candidate_head(status: dict):
    """One candidate-identity accessor over the normalized candidate block.

    The rule lives in ``pi_task.normalize_candidate``; the board never derives a
    second candidate. A missing block normalizes the raw status with the same
    function, so fail-closed behavior is identical everywhere.
    """
    candidate = status.get("candidate")
    if not isinstance(candidate, dict):
        candidate = normalize_candidate(status)
    if candidate.get("status") != "known":
        return None
    head = candidate.get("head")
    return head if isinstance(head, str) and head else None


def _snapshot_items(status: dict) -> list:
    """Per-item evidence grades from the normalized phase snapshot."""
    phase = status.get("phase")
    if not isinstance(phase, dict):
        return []
    evidence = phase.get("evidence")
    if not isinstance(evidence, dict):
        return []
    return [item for item in evidence.get("items") or [] if isinstance(item, dict)]


def _verified_items(status: dict) -> list:
    return [item for item in _snapshot_items(status) if item.get("status") == "covered"]


def _failed_items(status: dict) -> list:
    return [item for item in _snapshot_items(status) if item.get("status") == "failed"]


def progress_milestones(status: dict) -> list:
    """Stable milestone candidates (key, summary, evidence) for one beat.

    Only verified snapshot items and meaningful check/repair progress with
    evidence refs qualify. Plain "implementing" narration is deliberately not a
    milestone and never queues.
    """
    phase = status.get("phase") or {}
    contract = phase.get("contractSha256")
    candidate = _phase_candidate_head(status)
    if not contract or candidate is None:
        return []
    result = []
    verified = _verified_items(status)
    if verified and not _failed_items(status):
        item = verified[0]
        check_id = item.get("checkId") or item.get("id")
        result.append((
            f"first_result|{contract}|{candidate}|{check_id}",
            f"verified check {check_id!r} passed on the current candidate",
            {"factSource": "verified_receipt", "checkId": check_id,
             "receiptRef": item.get("receiptRef"), "logVerified": True,
             "evidenceLevel": item.get("evidenceLevel")}))
    progress = status.get("progress") or {}
    if isinstance(progress, dict) and progress.get("round") == status.get("round") \
            and progress.get("activity") in ("checking", "repairing") \
            and (progress.get("completedCriteria") or progress.get("evidenceRefs")):
        result.append((
            f"check_{progress.get('activity')}|{contract}|{candidate}",
            f"pi reports {progress.get('activity')} progress with evidence references",
            {"factSource": "pi_self_report_unverified",
             "activity": progress.get("activity"),
             "completedCriteria": progress.get("completedCriteria") or [],
             "evidenceRefs": progress.get("evidenceRefs") or [],
             "step": progress.get("step"), "next": progress.get("next")}))
    return result


def _repair_progress_after(status: dict, since: float) -> bool:
    progress = status.get("progress") or {}
    updated = progress.get("updatedAt")
    if not isinstance(updated, (int, float)) or isinstance(updated, bool) or updated <= since:
        return False
    return (progress.get("activity") == "repairing"
            and bool(progress.get("completedCriteria") or progress.get("evidenceRefs")))


def _notify_ledger(card: dict, phase_id: str, contract: str, now: float) -> dict:
    store = card.get("notify")
    if not isinstance(store, dict):
        store = {"schemaVersion": 1, "phases": {}}
        card["notify"] = store
    phases = store.setdefault("phases", {})
    entry = phases.get(phase_id)
    if not isinstance(entry, dict):
        entry = {}
    entry.update({"phaseId": phase_id, "contractSha256": contract,
                  "count": int(entry.get("count") or 0),
                  "milestones": entry.get("milestones") if isinstance(entry.get("milestones"), dict) else {},
                  "abnormal": entry.get("abnormal") if isinstance(entry.get("abnormal"), dict) else {},
                  "updatedAt": now})
    phases[phase_id] = entry
    return entry


def _anomaly_milestone(entry: dict, status: dict, now: float, anomaly_seconds: float):
    """First sustained, unresolved applicable failure -> one bounded echo."""
    changed = False
    result = None
    contract = entry.get("contractSha256")
    candidate = _phase_candidate_head(status)
    for item in _failed_items(status):
        check_id = item.get("checkId") or item.get("id")
        key = f"abnormal|{contract}|{candidate}|{check_id}"
        record = entry["abnormal"].get(key)
        if not isinstance(record, dict):
            record = {"firstSeenAt": now, "notifiedAt": None}
            entry["abnormal"][key] = record
            changed = True
        if record.get("notifiedAt") or key in entry["milestones"]:
            continue  # already echoed, merged or quota-suppressed: it must not shadow the next one
        first = record.get("firstSeenAt")
        if not isinstance(first, (int, float)) or isinstance(first, bool) \
                or now - first < anomaly_seconds:
            continue
        if _repair_progress_after(status, first):
            continue
        if result is None:
            result = (key,
                      f"check {check_id!r} has an unresolved failure observed for "
                      f"{int(now - first)}s; pi is expected to be repairing",
                      {"factSource": "observed_receipt", "checkId": check_id,
                       "reason": "sustained_anomaly",
                       "receiptRef": item.get("receiptRef"), "logRef": item.get("logRef"),
                       "itemReason": item.get("reason"),
                       "evidenceLevel": item.get("evidenceLevel")})
    if len(entry["abnormal"]) > MAX_NOTIFY_ABNORMAL:
        ordered = sorted(entry["abnormal"].items(), key=lambda kv: (kv[1] or {}).get("firstSeenAt") or 0)
        entry["abnormal"] = dict(ordered[-MAX_NOTIFY_ABNORMAL:])
    return result, changed


def maybe_publish_progress(card: dict, status: dict, now=None, interval=None,
                           anomaly_seconds=None,
                           max_notifications: int = MAX_PROGRESS_NOTIFICATIONS) -> dict:
    """Publish at most two bounded anomaly echoes per phase; never a decision event.

    Ordinary milestones are recorded on the board ledger and never enqueue.

    Returns ``{"changed": bool, "created": event|None, "merged": event|None}``.
    The quota and the last-notification time live on the persisted card and are
    keyed by phaseId, so rounds, supervisor restarts and repeated refreshes do
    not reset them. Pause, route pause, offline transport and a candidate
    without phase identity fail closed (nothing is queued).
    """
    now = time.time() if now is None else now
    interval = _phase_progress_budget_seconds() if interval is None else float(interval)
    anomaly_seconds = _phase_anomaly_seconds() if anomaly_seconds is None else float(anomaly_seconds)
    phase = status.get("phase")
    if not isinstance(phase, dict) or not phase.get("phaseId"):
        return {"changed": False, "created": None, "merged": None, "reason": "no phase"}
    if card.get("transport") != TRANSPORT_CLI_QUEUE:
        return {"changed": False, "created": None, "merged": None,
                "reason": "transport is not cli-queue"}
    if status.get("state") not in ACTIVE_STATES:
        # Terminal delivery is owned by the review/blocked event path, which is
        # never suppressed by the progress quota; no duplicate progress echo.
        return {"changed": False, "created": None, "merged": None,
                "reason": "no active round"}
    if card.get("paused"):
        return {"changed": False, "created": None, "merged": None, "reason": "paused"}
    thread = card.get("ownerThread")
    if isinstance(thread, str):
        route_is_paused, problem = route_paused(thread)
        if problem is not None or route_is_paused:
            return {"changed": False, "created": None, "merged": None,
                    "reason": f"route pause {problem or 'active'}"}
    contract = phase.get("contractSha256")
    candidate = _phase_candidate_head(status)
    if not contract or candidate is None:
        return {"changed": False, "created": None, "merged": None,
                "reason": "phase contract or candidate unknown"}
    entry = _notify_ledger(card, phase["phaseId"], contract, now)
    anomaly, changed = _anomaly_milestone(entry, status, now, anomaly_seconds)
    # Ordinary milestones (first verified result, pi check/repair self-reports)
    # stay on the board: they are recorded in the phase ledger, never queued.
    # Only an anomaly may enqueue an echo, within the per-phase quota below.
    recorded = False
    for key, summary, evidence in progress_milestones(status):
        if key not in entry["milestones"]:
            entry["milestones"][key] = {"at": now, "boardOnly": True,
                                        "summary": _text(summary, 160),
                                        "factSource": evidence.get("factSource")}
            recorded = True
    if recorded:
        changed = True
        entry["updatedAt"] = now
        if len(entry["milestones"]) > MAX_NOTIFY_MILESTONES:
            ordered = sorted(entry["milestones"].items(),
                             key=lambda kv: (kv[1] or {}).get("at") or 0)
            entry["milestones"] = dict(ordered[-MAX_NOTIFY_MILESTONES:])
    chosen = anomaly if anomaly is not None and anomaly[0] not in entry["milestones"] else None
    if chosen is None:
        return {"changed": changed, "created": None, "merged": None}
    key, summary, evidence = chosen
    evidence_all = {"briefRef": (status.get("evidence") or {}).get("brief"),
                    "checksRef": (status.get("checks") or {}).get("dir"),
                    "stateRef": (status.get("evidence") or {}).get("state"),
                    **evidence}
    if int(entry.get("count") or 0) >= max_notifications:
        entry["milestones"][key] = {"at": now, "suppressed": "quota"}
        entry["updatedAt"] = now
        return {"changed": True, "created": None, "merged": None,
                "reason": "notification quota exhausted"}
    fingerprint = (f"phase:{phase['phaseId']}:contract:{contract}:candidate:{candidate}:"
                   f"progress:{key}")
    last = entry.get("lastAt")
    if isinstance(last, (int, float)) and not isinstance(last, bool) and now - last < interval:
        merged = None
        pending = [event for event in pending_events(card)
                   if event.get("kind") == PROGRESS_EVENT_KIND
                   and event.get("phaseId") == phase["phaseId"]]
        if pending:
            merged = pending[-1]
            merged["summary"] = _text(summary, MAX_SUMMARY)
            merged["fingerprint"] = _text(fingerprint, 200)
            merged["evidence"] = evidence_all
            merged["candidate"] = {"round": status.get("round"), "head": candidate,
                                   "state": status.get("state")}
            merged["updatedAt"] = now
        entry["milestones"][key] = {"at": now, "merged": bool(merged),
                                    "suppressedByInterval": True}
        entry["updatedAt"] = now
        return {"changed": True, "created": None, "merged": merged,
                "reason": "merged within the notification interval"}
    _supersede_phase_kinds(card, now, (PROGRESS_EVENT_KIND,), keep_fingerprint=fingerprint)
    event = add_event(card, PROGRESS_EVENT_KIND, status.get("round"), fingerprint, summary,
                      {"round": status.get("round"), "head": candidate,
                       "state": status.get("state")},
                      evidence_all,
                      "Anomaly, not a delivery: this check stayed failed with no repair progress. "
                      "Choose: leave Pi repairing (resolve this echo), pause the route, or cancel "
                      "the round; nothing is accepted by answering.", now)
    if event is None:
        entry["milestones"][key] = {"at": now, "deduplicated": True}
        entry["updatedAt"] = now
        return {"changed": True, "created": None, "merged": None, "reason": "identity dedup"}
    event["phaseId"] = phase["phaseId"]
    event["contractHash"] = contract
    event["factSource"] = evidence.get("factSource")
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["lastAt"] = now
    entry["milestones"][key] = {"at": now, "eventId": event["id"]}
    if anomaly is not None and anomaly[0] == key:
        entry["abnormal"][key]["notifiedAt"] = now
    if len(entry["milestones"]) > MAX_NOTIFY_MILESTONES:
        ordered = sorted(entry["milestones"].items(), key=lambda kv: (kv[1] or {}).get("at") or 0)
        entry["milestones"] = dict(ordered[-MAX_NOTIFY_MILESTONES:])
    entry["updatedAt"] = now
    return {"changed": True, "created": event, "merged": None}


def _notify_view(card: dict):
    store = card.get("notify")
    if not isinstance(store, dict):
        return None
    phases = store.get("phases") or {}
    return {phase_id: {"count": entry.get("count"), "lastAt": entry.get("lastAt"),
                       "milestones": len(entry.get("milestones") or {}),
                       "abnormal": len(entry.get("abnormal") or {})}
            for phase_id, entry in islice(phases.items(),5) if isinstance(entry, dict)}


def project_events(card: dict, status: dict, now: float) -> list:
    """Actionable facts only; phase contracts suppress local/self-repairable check events."""
    phase = status.get("phase")
    if isinstance(phase, dict) and phase.get("phaseId"):
        return _project_phase_events(card, status, now)
    return _project_brief_only_events(card, status, now)


def _project_brief_only_events(card: dict, status: dict, now: float) -> list:
    """Actionable facts for a task without a phase contract (brief-only)."""
    added = []
    state = status.get("state")
    recorded = status.get("recordedState")
    checks = status.get("checks") or {}
    receipts = checks.get("receipts") or {}
    latest = receipts.get("latest")
    guard = checks.get("resourceGuard") or {}
    evidence_dir = status.get("evidence") or {}
    head = _phase_candidate_head(status)
    candidate = {"round": status.get("round"), "head": head, "state": state}
    base_evidence = {"briefRef": evidence_dir.get("brief"), "checksRef": checks.get("dir"),
                     "stateRef": evidence_dir.get("state")}

    def add(kind, fingerprint, summary, question, extra=None):
        evidence = dict(base_evidence)
        if extra:
            evidence.update(extra)
        event = add_event(card, kind, status.get("round"), fingerprint, summary,
                          candidate, evidence, question, now)
        if event is not None:
            event["delivery"] = _delivery_info(status)
            added.append(event)

    if status.get("timedOut"):
        add("task_timeout", f"exit={status.get('exitCode')}",
            f"round {status.get('round')} wrapper timed out (exit {status.get('exitCode')})",
            "Decide whether to repair, cancel or extend the deadline; the round state is exact evidence.",
            {"receiptRef": _receipt_ref(checks, latest)})
    timeout_receipts = [item for item in (receipts.get("failedRecent") or [])
                        if isinstance(item, dict) and item.get("timedOut")]
    if not timeout_receipts and isinstance(latest, dict) and latest.get("timedOut"):
        timeout_receipts = [latest]
    for item in timeout_receipts:
        add("check_timeout", f"{item.get('receipt')}",
            f"check {item.get('id')!r} timed out",
            "Decide whether to repair the check or cancel; the receipt is the exact evidence.",
            {"receiptRef": _receipt_ref(checks, item)})
    for breach in guard.get("breaches") or []:
        if not isinstance(breach, dict):
            continue
        name = breach.get("name") or breach.get("source")
        add("resource_breach", f"{name}:{breach.get('path')}:{breach.get('maxBytes')}",
            f"resource budget breached under {breach.get('path')} "
            f"(at least {breach.get('observedBytes')} bytes > cap {breach.get('maxBytes')})",
            "Decide whether to stop, widen the declared budget or repair; guard evidence is exact.",
            {"guardPath": breach.get("path"), "observedBytes": breach.get("observedBytes"),
             "maxBytes": breach.get("maxBytes"), "source": breach.get("source"),
             "breachBasis": breach.get("breachBasis")})
    fault = status.get("executionEvidence") or {}
    if fault.get("status") in ("failed", "unknown"):
        for old in pending_events(card):
            if old.get("kind")=="review_required" and old.get("round")==status.get("round"):
                _mark_superseded(card,old,now)
        add("execution_failed", f"execution:{fault.get('reason')}:{fault.get('tailSha256')}",
            f"round {status.get('round')} execution incomplete: {fault.get('error')}",
            "Resolve the execution incident, not a quality delivery. Any authorized retry keeps "
            "the same session, worktree and remaining deadline.", {"executionEvidence": fault})
    elif recorded in TERMINAL_STATES and state != "unknown":
        add("review_required", f"{state}:{head}:{status.get('exitCode')}",
            f"round {status.get('round')} reached terminal state {state}",
            "Review the candidate and exact check receipts; accept or request changes. "
            "Pi exit 0 is execution evidence only.",
            {"candidateHead": head, "receiptRef": _receipt_ref(checks, latest)})
    if state == "unknown" and recorded in ACTIVE_STATES:
        add("ownership_unknown", f"{recorded}",
            f"recorded active state {recorded} without a live supervisor lease",
            "Inspect ownership and decide cleanup; do not assume progress.",
            {"recordedState": recorded})
    ownership = status.get("ownership") or {}
    if recorded in TERMINAL_STATES and ownership.get("activeWorker") \
            and not ownership.get("supervisorAlive"):
        add("ownership_lingering", f"{recorded}",
            "terminal record but a worker lock is still held",
            "Inspect the lingering owned worker before reuse.",
            {"recordedState": recorded})
    return added


def _decide_hint(repo, task_id, event: dict) -> str:
    """Short decide command (target <= 200 bytes with ordinary paths).

    The event id, candidate head and phase/contract hash are printed once above
    it on the card; the hint points back at them instead of repeating them.
    """
    head = (event.get("candidate") or {}).get("head")
    review = event.get("kind") in REVIEW_KINDS and isinstance(head, str) \
        and bool(FULL_OID_RE.fullmatch(head))
    options = "accept|reject|changes_requested" if review else "resolve|reject|changes_requested"
    return (f'python3 {shlex.quote(str(BOARD_CLI))} decide '
            f'--repo {shlex.quote(str(repo))} --task {shlex.quote(str(task_id))} '
            f'--event-id EVENT --decision {options}')
