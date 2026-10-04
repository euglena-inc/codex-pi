#!/usr/bin/env python3
"""Read-only projection of recorded Main-reported outcome observations.

This module is the lower-level view half of the outcome metrics contract: it
consumes only bounded stored metadata (member task/round state, stored round
summaries, archived decisions/events, pinned check receipts and network
sidecars) and never invokes a model, never executes a task, never mutates
board/execution/budget/quality state and never scans a raw session or raw
check log. It imports shared validation, trusted-path and storage helpers from
``pi_outcome``; ``pi_outcome`` never imports this module.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import math
import sqlite3
from pathlib import Path

from pi_archive import STORE_FILE, decision_page, event_page
from pi_core import read_network_sidecar
from pi_store import FULL_OID_RE, _read_bounded_json, board_file_for_common, read_board
from pi_summary import union_seconds
from pi_outcome import (
    ASSISTANT_METRIC_KEYS,
    AUXILIARY_METRIC_KEYS,
    DELIVERY_DECISIONS,
    DELIVERY_EVENT_KINDS,
    LISTING_PAGE,
    MAX_ARCHIVE_PAGES,
    MAX_ARGV_CHARS,
    MAX_ARGV_ITEMS,
    MAX_MEMBER_SCAN_BYTES,
    MAX_POTENTIAL_REPEAT_SOURCES,
    MAX_PROBLEM_DETAILS,
    MAX_RECEIPT_BYTES,
    MAX_STATE_BYTES,
    MAX_SUMMARY_BYTES,
    MAX_TASK_BYTES,
    OUTCOME_ID_RE,
    RECEIPT_FIELDS,
    SCHEMA_VERSION,
    SUMMARY_TIMING_KEYS,
    TAKEOVER_EVENT_KIND,
    USAGE_KEYS,
    _codex_root,
    _finite,
    _inspect_under,
    _outcomes_root,
    _read_bounded_bytes,
    _same_value,
    load_outcome,
)

# ---------------------------------------------------------------------------
# read-only projection helpers
# ---------------------------------------------------------------------------

def _status(known, missing, unknown) -> dict:
    if unknown:
        state = "unknown"
    elif missing:
        state = "partial"
    elif known:
        state = "known"
    else:
        state = "not_applicable"
    return {"status": state, "known": list(known), "missing": list(missing),
            "unknown": list(unknown)}


def _selected_rounds(members) -> dict:
    """Merge the explicit member set by task; no name or predecessor guessing."""
    merged = {}
    for member in members:
        merged.setdefault(member["task"], set()).update(member["rounds"])
    return {task: sorted(rounds) for task, rounds in sorted(merged.items())}


def _load_member_rows(common, selected) -> tuple:
    """Bounded, symlink-safe read of the selected member sources."""
    state = _codex_root(common)
    rows, scan_bytes = [], 0

    def bounded_json(path, limit, label):
        nonlocal scan_bytes
        if path is None or not path.is_file():
            return None, None, "missing"
        try:
            size = path.stat().st_size
        except OSError:
            return None, None, "unreadable"
        if scan_bytes + size > MAX_MEMBER_SCAN_BYTES:
            return None, None, "member metadata scan budget exceeded"
        scan_bytes += size
        data, problem = _read_bounded_json(path, limit)
        if not isinstance(data, dict):
            return None, None, problem or "invalid"
        return data, data, None

    for task, rounds in selected.items():
        task_dir, task_problem = _inspect_under(state / "tasks" / task, state)
        task_meta, meta_problem = None, task_problem
        if task_dir is not None and task_dir.is_dir():
            task_json, problem = _inspect_under(task_dir / "task.json", state)
            task_meta, _, meta_problem = bounded_json(task_json, MAX_TASK_BYTES,
                                                      f"member task {task} metadata")
            if meta_problem == "missing" and problem:
                meta_problem = problem
            if task_meta is not None:
                declared_common = task_meta.get("commonDir")
                try:
                    common_matches = (declared_common is None or
                                      Path(declared_common).resolve() == Path(common).resolve())
                except (TypeError, ValueError, OSError, RuntimeError):
                    common_matches = False
                if task_meta.get("task", task) != task or not common_matches:
                    meta_problem = "task identity or git-common directory mismatch"
                    task_meta = None
        elif task_dir is not None:
            meta_problem = task_problem or "missing task directory"
        for number in rounds:
            round_dir = state_data = summary = None
            state_problem = summary_problem = checks_problem = None
            checks_dir = None
            if task_dir is not None and meta_problem is None:
                round_dir, problem = _inspect_under(task_dir / "rounds" / str(number), state)
                if round_dir is not None and round_dir.is_dir():
                    checks_dir, checks_problem = _inspect_under(round_dir / "round.checks", state)
                    if checks_problem is None and not checks_dir.is_dir():
                        checks_problem = "missing"
                    state_path, problem = _inspect_under(round_dir / "round.state.json", state)
                    state_data, _, state_problem = bounded_json(
                        state_path if not problem else None, MAX_STATE_BYTES,
                        f"member round {task}/{number} state")
                    if state_problem == "missing" and problem:
                        state_problem = problem
                    if state_data is not None and state_data.get("state") not in (
                            "pending", "starting", "running", "completed", "failed",
                            "timed_out", "cancelled", "interrupted"):
                        state_data, state_problem = None, "invalid execution state"
                    summary_path, problem = _inspect_under(round_dir / "round.summary.json", state)
                    summary, _, summary_problem = bounded_json(
                        summary_path if not problem else None, MAX_SUMMARY_BYTES,
                        f"member round {task}/{number} summary")
                    if summary_problem == "missing" and problem:
                        summary_problem = problem
                    if summary is not None and not isinstance(summary.get("metrics"), dict):
                        summary, summary_problem = None, "invalid summary metrics"
                else:
                    state_problem = summary_problem = checks_problem = \
                        problem or "missing round directory"
            else:
                state_problem = summary_problem = checks_problem = \
                    meta_problem or "missing task directory"
            rows.append({"task": task, "round": number, "roundDir": round_dir,
                         "checksDir": checks_dir, "checksProblem": checks_problem,
                         "state": state_data, "stateProblem": state_problem,
                         "summary": summary, "summaryProblem": summary_problem,
                         "taskMeta": task_meta, "taskProblem": meta_problem})
    return rows, scan_bytes


def _read_archive(common, selected) -> tuple:
    """Bounded cursor reads attributed to the selected (task, round) set."""
    board_file = board_file_for_common(Path(common))
    store = board_file.with_name(STORE_FILE)
    decisions, events = [], []
    coverage = {"status": "unknown", "problem": None, "truncated": False,
                "tasksComplete": [], "tasksFailed": [],
                "excludedDecisions": 0, "excludedEvents": 0,
                "unattributedDecisions": 0, "unattributedEvents": 0}
    if not selected:
        coverage.update(status="not_applicable",
                        note="no member rounds selected; nothing to attribute from the archive")
        return decisions, events, coverage
    for source in (board_file, store, Path(str(store) + "-wal"), Path(str(store) + "-shm")):
        _, problem = _inspect_under(source, _codex_root(common))
        if problem:
            coverage["problem"] = f"archive source {problem}"
            return decisions, events, coverage
    if not store.is_file():
        coverage["problem"] = "board archive is not initialized; decisions/events are unavailable"
        return decisions, events, coverage
    for task, rounds in selected.items():
        round_set = set(rounds)
        ok = True
        cursor, pages = "", 0
        while True:
            try:
                page = decision_page(board_file, task, cursor)
            except (ValueError, OSError, sqlite3.Error) as exc:
                coverage["problem"] = f"decision archive unreadable: {type(exc).__name__}"
                ok = False
                break
            for record in page["decisions"]:
                number = record.get("round")
                if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
                    if number in round_set:
                        decisions.append(dict(record, task=task, round=number))
                    else:
                        coverage["excludedDecisions"] += 1
                else:
                    coverage["unattributedDecisions"] += 1
            cursor = page.get("nextCursor")
            pages += 1
            if cursor is None:
                break
            if pages >= MAX_ARCHIVE_PAGES:
                coverage["truncated"] = True
                ok = False
                coverage["problem"] = "decision archive exceeded the bounded page count"
                break
        cursor, pages = 0, 0
        while True:
            try:
                page = event_page(board_file, task, cursor)
            except (ValueError, OSError, sqlite3.Error) as exc:
                coverage["problem"] = f"event archive unreadable: {type(exc).__name__}"
                ok = False
                break
            for event in page["events"]:
                number = event.get("round")
                if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
                    if number in round_set:
                        events.append(dict(event, task=task, round=number))
                    else:
                        coverage["excludedEvents"] += 1
                else:
                    coverage["unattributedEvents"] += 1
            cursor = page.get("nextCursor")
            pages += 1
            if cursor is None:
                break
            if pages >= MAX_ARCHIVE_PAGES:
                coverage["truncated"] = True
                ok = False
                coverage["problem"] = "event archive exceeded the bounded page count"
                break
        (coverage["tasksComplete"] if ok else coverage["tasksFailed"]).append(task)
    if coverage["tasksFailed"] or coverage["unattributedDecisions"] or coverage["unattributedEvents"]:
        coverage["status"] = "unknown"
    elif coverage["truncated"]:
        coverage["status"] = "partial"
    else:
        coverage["status"] = "known"
    coverage["note"] = ("archived cursor pages filtered to the selected member rounds; "
                        "unselected rounds are excluded and unattributable records keep "
                        "coverage unknown")
    return decisions, events, coverage


def _is_delivery_record(record) -> bool:
    return record.get("eventKind") in DELIVERY_EVENT_KINDS \
        or (record.get("eventKind") is None and bool(record.get("phaseId")))


def _attribution_facts(decisions) -> dict:
    """Distinct quality failures, first reviewed delivery and exact decision sources."""
    counts = Counter()
    quality_events, quality_rounds = [], {}
    first_delivery, first_acceptance = None, None
    for record in decisions:
        decision = record.get("decision")
        if decision:
            counts[decision] += 1
        if not _is_delivery_record(record) or decision not in DELIVERY_DECISIONS:
            continue
        if decision != "accepted" and record.get("failureKind", "quality") != "quality":
            continue
        view = {"task": record.get("task"), "round": record.get("round"),
                "eventId": record.get("eventId"), "decision": decision,
                "failureKind": record.get("failureKind", "quality") if decision != "accepted" else None,
                "at": record.get("at"), "reviewedHead": record.get("reviewedHead")}
        if not _finite(view["at"]) or view["at"] < 0:
            view["at"] = None
        if first_delivery is None or (_finite(view["at"])
                                      and (first_delivery.get("at") is None
                                           or view["at"] < first_delivery["at"])):
            first_delivery = view
        if decision == "accepted" and (first_acceptance is None
                                       or (_finite(view["at"])
                                           and (first_acceptance.get("at") is None
                                                or view["at"] < first_acceptance["at"]))):
            first_acceptance = view
        if decision in ("rejected", "changes_requested") \
                and record.get("failureKind", "quality") == "quality":
            quality_events.append(view)
            quality_rounds[(record.get("task"), record.get("round"))] = view
    return {"counts": counts, "qualityEvents": quality_events,
            "qualityCount": len(quality_rounds), "firstReviewedDelivery": first_delivery,
            "firstReviewedAcceptance": first_acceptance}


def _takeover_latches(common, selected, events) -> tuple:
    """Attributed takeover evidence: board latch plus archived takeover events."""
    latches, coverage = [], {"excluded": 0, "unattributed": 0, "problem": None}
    board_file = board_file_for_common(Path(common))
    for source in (board_file, board_file.with_name(STORE_FILE)):
        _, problem = _inspect_under(source, _codex_root(common))
        if problem:
            coverage["problem"] = f"board source {problem}"
            return latches, coverage
    board, problem = read_board(board_file)
    if isinstance(board, dict):
        cards = board.get("cards") or {}
        for task, rounds in selected.items():
            try:
                card = cards.get(task)
            except (ValueError, OSError):
                coverage["problem"] = "board card scan failed"
                break
            latch = ((card or {}).get("codex") or {}).get("takeover") \
                if isinstance(card, dict) else None
            if not (isinstance(latch, dict) and latch.get("required")):
                continue
            rounds_set = set(rounds)
            candidates = set()
            outcome = latch.get("outcome")
            if isinstance(outcome, dict) and isinstance(outcome.get("round"), int) \
                    and not isinstance(outcome["round"], bool):
                candidates.add(outcome["round"])
            # Failed earlier deliveries are not the round where takeover occurred.
            entry = {"task": task, "cause": latch.get("cause"), "at": latch.get("at"),
                     "scope": latch.get("scope"), "failedDeliveries": latch.get("failedDeliveries"),
                     "source": "board_takeover_latch"}
            if candidates & rounds_set:
                entry["attribution"] = "selected_round"
                latches.append(entry)
            elif candidates:
                coverage["excluded"] += 1
            else:
                entry["attribution"] = "unknown_round"
                coverage["unattributed"] += 1
                latches.append(entry)
    else:
        coverage["problem"] = problem or "board is unreadable"
    for event in events:
        if not isinstance(event, dict) or event.get("kind") != TAKEOVER_EVENT_KIND:
            continue
        review = ((event.get("evidence") or {}).get("reviewPolicy") or {}) \
            if isinstance(event.get("evidence"), dict) else {}
        if event.get("fingerprint") == "explicit-main-takeover":
            cause = "main_decision"
        else:
            cause = review.get("takeoverCause") or "quality_limit"
        latches.append({"task": event.get("task"), "round": event.get("round"),
                        "eventId": event.get("id"), "cause": cause,
                        "at": event.get("createdAt") or event.get("at"),
                        "attribution": "selected_round", "source": "archived_takeover_event"})
    return latches, coverage


def _leaf_value(section, key, integer=False):
    """A stored leaf value; invalid, bool, negative or fractional stays unknown."""
    if not isinstance(section, dict):
        return None
    leaf = section.get(key)
    if not isinstance(leaf, dict):
        return None
    value = leaf.get("value")
    if not _finite(value) or value < 0:
        return None
    if integer and not float(value).is_integer():
        return None
    return int(value) if integer else value


def _leaf_complete(section, key) -> bool:
    if not isinstance(section, dict):
        return False
    leaf = section.get(key)
    return bool(isinstance(leaf, dict) and leaf.get("complete") is True)


def _plain_number(value, integer=False):
    if not _finite(value) or value < 0:
        return None
    if integer and not float(value).is_integer():
        return None
    return int(value) if integer else value


def _round_intervals(rows) -> tuple:
    intervals, known, unknown = [], [], []
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        state = row["state"]
        started = state.get("startedAt") if isinstance(state, dict) else None
        ended = state.get("endedAt") if isinstance(state, dict) else None
        if _finite(started) and _finite(ended) and started >= 0 and ended >= started:
            intervals.append((started, ended))
            known.append(key)
        else:
            unknown.append(key)
    return intervals, known, unknown


def _work_intervals(work) -> tuple:
    intervals, with_boundaries, missing = [], [], []
    for entry in work:
        started, ended = entry.get("startedAt"), entry.get("finishedAt")
        if started is not None and ended is not None and ended >= started:
            intervals.append((started, ended))
            with_boundaries.append(entry["id"])
        else:
            missing.append(entry["id"])
    return intervals, with_boundaries, missing


# ---------------------------------------------------------------------------
# projection sections
# ---------------------------------------------------------------------------

def _delivery_section(payload, rows, facts, archive_coverage):
    rounds, known, missing = [], [], []
    for row in rows:
        key = {"task": row["task"], "round": row["round"]}
        label = f"{row['task']}/{row['round']}"
        if row["state"] is None:
            rounds.append(key | {"state": "unknown", "sourceProblem": row["stateProblem"]})
            missing.append(label)
            continue
        state = row["state"]
        code = state.get("exitCode")
        rounds.append(key | {"state": state.get("state"),
                             "exitCode": code if isinstance(code, int) and not isinstance(code, bool) else None,
                             "timedOut": state.get("timedOut") if isinstance(state.get("timedOut"), bool) else None,
                             "cancelled": state.get("cancelled") if isinstance(state.get("cancelled"), bool) else None,
                             "endHead": state.get("endHead") or state.get("head"),
                             "startedAt": _plain_number(state.get("startedAt")),
                             "endedAt": _plain_number(state.get("endedAt"))})
        known.append(label)
    first_delivery = facts["firstReviewedDelivery"]
    return {
        "source": "main_reported_observation_with_native_round_facts",
        "reported": {"source": "main_reported", "status": payload["status"],
                     "candidate": payload["candidate"], "evidenceRefs": payload["evidenceRefs"],
                     "note": "reported observation and references; not verified acceptance, "
                             "ownership or permission"},
        "nativeRounds": rounds,
        "nativeRoundCoverage": _status(known, missing, []),
        "nativeDecisions": {
            "selectedRounds": [{"task": task, "rounds": list(rounds)}
                               for task, rounds in _selected_rounds(payload["members"]).items()],
            "counts": {key: facts["counts"][key] for key in sorted(facts["counts"])},
            "firstReviewedDelivery": first_delivery,
            "firstReviewedDeliveryAccepted": (first_delivery["decision"] == "accepted")
                if first_delivery and archive_coverage.get("status") == "known" else None,
            "firstReviewedAcceptance": facts["firstReviewedAcceptance"],
            "reviewedQualityFailures": facts["qualityEvents"],
            "reviewedQualityFailureCount": facts["qualityCount"],
            "coverage": dict(archive_coverage,
                             note="cursor-paged archive filtered to the selected member rounds; "
                                  "unselected rounds never mix into this outcome, first reviewed "
                                  "delivery keeps its own decision, and no decision means unknown")}}


def _timing_section(payload, rows, events, archive_coverage):
    created, complete_metadata = [], bool(rows)
    for row in rows:
        value = row["taskMeta"].get("createdAt") if isinstance(row["taskMeta"], dict) else None
        if _finite(value) and value >= 0:
            created.append(value)
        else:
            complete_metadata = False
    if payload["startedAt"] is not None:
        start = {"value": payload["startedAt"], "source": "reported"}
    elif complete_metadata and created:
        start = {"value": min(created), "source": "earliest_member_task_createdAt",
                 "label": "dispatch scope; excludes prior planning"}
    else:
        start = {"value": None, "source": "unknown",
                 "reason": ("member task creation metadata is incomplete" if rows
                            else "no member tasks were reported")}
    finish = {"value": payload["finishedAt"],
              "source": "reported" if payload["finishedAt"] is not None else "unknown"}
    elapsed, latency_known, latency_reason = None, False, None
    if finish["value"] is None:
        latency_reason = "no reported finish time; true delivery latency is unknown"
    elif start["value"] is None:
        latency_reason = "no reported or deduced start time; true delivery latency is unknown"
    elif finish["value"] < start["value"]:
        latency_reason = "reported finish precedes the known start; latency is unknown, never negative"
    else:
        elapsed = finish["value"] - start["value"]
        latency_known = True
    intervals, round_known, round_unknown = _round_intervals(rows)
    if not rows:
        round_union = {"applicable": False, "value": None,
                       "reason": "no selected member rounds"}
    elif intervals:
        union = union_seconds(intervals)
        round_union = {"applicable": True, "value": union if math.isfinite(union) else None,
                       "reason": None if math.isfinite(union) else "interval union is non-finite"}
    else:
        round_union = {"applicable": True, "value": None,
                       "reason": "no selected round has both execution boundaries"}
    round_union.update({"rounds": round_known,
                        "coverage": _status(round_known, [], round_unknown),
                        "label": "native union of recorded round execution intervals"})
    work_intervals, work_boundaries, work_missing = _work_intervals(payload["work"])
    if not payload["work"]:
        external_union = {"applicable": False, "declaredZero": bool(payload["workComplete"]),
                          "value": 0.0 if payload["workComplete"] else None,
                          "reason": None if payload["workComplete"] else
                          "no external work entries and completeness was not declared",
                          "idsWithoutBothBoundaries": []}
    elif work_intervals:
        union = union_seconds(work_intervals)
        external_union = {"applicable": True, "declaredZero": False,
                          "value": union if math.isfinite(union) else None,
                          "reason": None if math.isfinite(union)
                                      else "external interval union is non-finite",
                          "idsWithBothBoundaries": work_boundaries,
                          "idsWithoutBothBoundaries": work_missing}
    else:
        external_union = {"applicable": True, "declaredZero": False, "value": None,
                          "idsWithBothBoundaries": [],
                          "idsWithoutBothBoundaries": work_missing,
                          "reason": "no external work entry has both boundaries"}
    external_union.update({"entries": len(payload["work"]), "source": "main_reported",
                           "label": "union of explicit external work intervals"})
    review_intervals, pending_reviews, missing_review_times = [], 0, 0
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in DELIVERY_EVENT_KINDS:
            continue
        created_at = event.get("createdAt")
        if created_at is None:
            created_at = event.get("at")
        handled = event.get("handledAt")
        if event.get("decision") in DELIVERY_DECISIONS and _finite(created_at) \
                and _finite(handled) and handled >= created_at >= 0:
            review_intervals.append((created_at, handled))
        elif not event.get("handled"):
            pending_reviews += 1
        else:
            missing_review_times += 1
    if archive_coverage.get("status") == "known" and pending_reviews == 0 and not missing_review_times:
        review_coverage = {"status": "known", "intervals": len(review_intervals),
                           "pendingExcluded": 0}
    else:
        review_coverage = {"status": "unknown" if not review_intervals else "partial",
                           "intervals": len(review_intervals),
                           "pendingExcluded": pending_reviews}
    review_coverage["missingBoundaries"] = missing_review_times
    sums = {key: {"sum": 0.0, "known": [], "incomplete": []}
            for key in SUMMARY_TIMING_KEYS + ("checkElapsedSeconds",)}
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        summary = row["summary"]
        timing = ((summary or {}).get("metrics") or {}).get("timing") \
            if isinstance(summary, dict) else None
        checks = ((summary or {}).get("metrics") or {}).get("checks") \
            if isinstance(summary, dict) else None
        for name in SUMMARY_TIMING_KEYS:
            value = _leaf_value(timing, name)
            if value is None:
                sums[name]["incomplete"].append(key)
            else:
                sums[name]["sum"] += value
                sums[name]["known"].append(key)
                if not _leaf_complete(timing, name):
                    sums[name]["incomplete"].append(key)
        value = _leaf_value(checks, "elapsedSeconds")
        if value is None:
            sums["checkElapsedSeconds"]["incomplete"].append(key)
        else:
            sums["checkElapsedSeconds"]["sum"] += value
            sums["checkElapsedSeconds"]["known"].append(key)
            if not _leaf_complete(checks, "elapsedSeconds"):
                sums["checkElapsedSeconds"]["incomplete"].append(key)
    review_union = union_seconds(review_intervals) if review_intervals else None
    if review_union is not None and not math.isfinite(review_union):
        review_union = None
    return {
        "source": "mixed_reported_and_native_metadata",
        "start": start,
        "finish": finish,
        "elapsedWallSeconds": elapsed,
        "trueDeliveryLatencySeconds": elapsed,
        "trueDeliveryLatencyKnown": latency_known,
        "trueDeliveryLatencyReason": latency_reason,
        "recordedAt": None,  # filled by the caller
        "roundExecutionUnionSeconds": round_union,
        "externalWorkUnionSeconds": external_union,
        "reportedWorkSumsSeconds": {key: {"value": (sums[key]["sum"] if sums[key]["known"]
                                                    and math.isfinite(sums[key]["sum"]) else None),
                                          "complete": bool(rows) and len(sums[key]["known"]) == len(rows)
                                                      and not sums[key]["incomplete"],
                                          "roundsKnown": sums[key]["known"],
                                          "roundsIncomplete": sums[key]["incomplete"]}
                                    for key in SUMMARY_TIMING_KEYS + ("checkElapsedSeconds",)},
        "reviewWaitSeconds": {"value": review_union,
                              "coverage": review_coverage,
                              "note": "archived delivery-event createdAt/handledAt union over the "
                                      "selected rounds; open reviews are excluded and missing "
                                      "history stays unknown"},
        "notes": ["never infer active reasoning time from silence or elapsed wall time; "
                  "reported work sums are labelled sums, never added to wall time; checks are "
                  "tool work, not separate wall-clock time"]}


def _summary_receipt_view(receipt):
    """Normalize one stored round-summary receipt entry; invalid fields stay None."""
    argv = receipt.get("argv")
    argv_list = [arg for arg in argv if isinstance(arg, str)] if isinstance(argv, list) else None
    argv_complete = (isinstance(argv, list) and len(argv) <= MAX_ARGV_ITEMS
                     and all(isinstance(arg, str) and len(arg) <= MAX_ARGV_CHARS for arg in argv))
    dirty = receipt.get("dirty") if isinstance(receipt.get("dirty"), bool) else None
    counts = receipt.get("test_counts")
    invalid_counts = counts is not None and (not isinstance(counts, dict) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()))
    counts = {key: value for key, value in counts.items()
              if isinstance(value, int) and not isinstance(value, bool) and value >= 0} \
        if isinstance(counts, dict) and not invalid_counts else None
    head = receipt.get("head")
    return {"id": receipt.get("id") if isinstance(receipt.get("id"), str) else None,
            "exitCode": receipt.get("exit_code") if not invalid_counts and receipt.get("log_verified") is True and (receipt.get("exit_code") is None
                                                     or (isinstance(receipt.get("exit_code"), int)
                                                         and not isinstance(receipt.get("exit_code"), bool)))
                        else None,
            "timedOut": receipt.get("timed_out") if isinstance(receipt.get("timed_out"), bool) else None,
            "cancelled": receipt.get("cancelled") if isinstance(receipt.get("cancelled"), bool) else None,
            "startedAt": _plain_number(receipt.get("started_at")),
            "endedAt": _plain_number(receipt.get("ended_at")),
            "head": head if isinstance(head, str) and FULL_OID_RE.fullmatch(head) else None,
            "dirty": dirty,
            "argv": argv_list if argv_complete else (argv_list if argv_list else None),
            "argvComplete": bool(argv_complete),
            "testCounts": counts}


def _extra_receipt_view(entry):
    return {"id": entry.get("id"), "exitCode": entry.get("exitCode"),
            "timedOut": entry.get("timedOut"), "cancelled": entry.get("cancelled"),
            "startedAt": entry.get("startedAt"), "endedAt": entry.get("endedAt"),
            "head": entry.get("head"), "dirty": entry.get("dirty"),
            "argv": list(entry.get("argv") or []), "argvComplete": entry.get("argvComplete") is True,
            "testCounts": entry.get("testCounts")}


def _receipt_canonical(value, checks_dir, state) -> tuple:
    """Resolve one receipt reference against its owning round, never the cwd."""
    if not isinstance(value, str) or not value.strip():
        return None, "receipt reference is missing"
    raw = Path(value)
    candidate = raw if raw.is_absolute() else (checks_dir / raw if checks_dir else None)
    if candidate is None:
        return None, "receipt reference cannot be resolved without its round checks directory"
    resolved, problem = _inspect_under(candidate, state)
    if problem is not None:
        return None, f"receipt {problem}"
    if checks_dir is None or not resolved.is_relative_to(checks_dir):
        return None, "native receipt is outside its owning round checks directory"
    return resolved, None


def _merge_trusted(contributions):
    merged = {}
    for field in RECEIPT_FIELDS:
        values = [entry["meta"].get(field) for entry in contributions if entry["trusted"]]
        present = [value for value in values if value is not None]
        if not present:
            merged[field] = None
        elif all(_same_value(value, present[0]) for value in present):
            merged[field] = present[0]
        else:
            return None
    return merged


def _verification_section(common, payload, rows):
    """Normalize every receipt once, then deduplicate, measure and group."""
    state = _codex_root(common)
    groups, round_known, round_missing, round_unknown = {}, [], [], []
    untrusted, conflicts, degraded = [], [], []
    unattributed = 0
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        summary = row["summary"]
        if summary is None:
            round_missing.append(key)
            continue
        receipts = summary.get("check_receipts")
        if not isinstance(receipts, list):
            round_unknown.append(key)
            continue
        round_known.append(key)
        for receipt in receipts:
            if not isinstance(receipt, dict):
                unattributed += 1
                continue
            canonical, problem = _receipt_canonical(receipt.get("receipt"), row["checksDir"], state)
            meta = _summary_receipt_view(receipt)
            if canonical is None:
                unattributed += 1
                if len(untrusted) < MAX_PROBLEM_DETAILS:
                    untrusted.append({"source": "round_summary", "task": row["task"],
                                      "round": row["round"], "problem": problem})
                continue
            groups.setdefault(str(canonical), []).append(
                {"source": "round_summary", "task": row["task"], "round": row["round"],
                 "meta": meta, "trusted": True,
                 "fileProblem": None if canonical.is_file() else "receipt file is missing"})
    for entry in payload["extraChecks"]:
        canonical, problem = _inspect_under(state / entry["path"], state)
        contribution = {"source": "extra_check", "task": None, "round": None,
                        "meta": _extra_receipt_view(entry), "trusted": False,
                        "fileProblem": problem}
        if canonical is not None and canonical.is_file():
            raw, read_problem = _read_bounded_bytes(canonical, MAX_RECEIPT_BYTES)
            if read_problem is not None:
                contribution["fileProblem"] = f"receipt file is {read_problem}"
            elif hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                contribution["fileProblem"] = "pinned metadata digest mismatch"
            else:
                contribution["trusted"] = True
                contribution["fileProblem"] = None
        elif contribution["fileProblem"] is None:
            contribution["fileProblem"] = "receipt file is missing"
        if canonical is None:
            unattributed += 1
            if len(untrusted) < MAX_PROBLEM_DETAILS:
                untrusted.append({"source": "extra_check", "path": entry["path"],
                                  "problem": problem})
            continue
        groups.setdefault(str(canonical), []).append(contribution)
    counts = Counter()
    elapsed, elapsed_missing, intervals = 0.0, 0, []
    clean_groups = {}
    for path, contributions in sorted(groups.items()):
        trusted = [entry for entry in contributions if entry["trusted"]]
        untrusted_copies = [entry for entry in contributions if not entry["trusted"]]
        file_problems = [entry for entry in contributions if entry.get("fileProblem")]
        if not trusted:
            counts["attempts"] += 1
            counts["unknown"] += 1
            if len(untrusted) < MAX_PROBLEM_DETAILS:
                untrusted.append({"path": path,
                                  "problem": untrusted_copies[0].get("fileProblem")
                                             if untrusted_copies else "no trusted copy"})
            continue
        merged = _merge_trusted(trusted)
        if merged is None:
            counts["attempts"] += 1
            counts["unknown"] += 1
            if len(conflicts) < MAX_PROBLEM_DETAILS:
                conflicts.append({"path": path, "sources": sorted({entry["source"]
                                                                   for entry in trusted})})
            continue
        if file_problems and len(degraded) < MAX_PROBLEM_DETAILS:
            degraded.append({"path": path, "problem": file_problems[0]["fileProblem"]})
        counts["attempts"] += 1
        if merged["timedOut"] or merged["cancelled"]:
            counts["interrupted"] += 1
            continue
        if merged["exitCode"] is None or merged["timedOut"] is None or merged["cancelled"] is None:
            counts["unknown"] += 1
            continue
        counts["completed"] += 1
        counts["passed" if merged["exitCode"] == 0 else "failed"] += 1
        started, ended = merged["startedAt"], merged["endedAt"]
        if _finite(started) and _finite(ended) and ended >= started:
            elapsed += ended - started
            intervals.append((started, ended))
        else:
            elapsed_missing += 1
        if merged["exitCode"] == 0 and merged["timedOut"] is False \
                and merged["cancelled"] is False and merged["dirty"] is False \
                and isinstance(merged["head"], str) and FULL_OID_RE.fullmatch(merged["head"]) \
                and merged["argvComplete"] is True and isinstance(merged["argv"], list):
            clean_groups.setdefault((merged["head"], tuple(merged["argv"])), []).append(path)
    repeats = []
    for (head, argv), paths in sorted(clean_groups.items()):
        if len(paths) < 2:
            continue
        repeats.append({"head": head, "argv": list(argv), "count": len(paths),
                        "receipts": paths[:MAX_POTENTIAL_REPEAT_SOURCES],
                        "classification": "potential_repeat",
                        "note": "same recorded clean candidate and argv only; this is not "
                                "evidence of waste and does not prove the same environment"})
    counts_complete = (not counts["unknown"] and not round_missing and not round_unknown and not unattributed
                       and not untrusted and not conflicts and not degraded
                       and not any(entry.get("fileProblem") for contributions in groups.values()
                                   for entry in contributions))
    sources = Counter(entry["source"] for contributions in groups.values()
                      for entry in contributions)
    shared = sum(1 for contributions in groups.values()
                 if {entry["source"] for entry in contributions} >= {"round_summary", "extra_check"})
    wall_union = union_seconds(intervals) if intervals else None
    if wall_union is not None and not math.isfinite(wall_union):
        wall_union = None
    return {
        "source": "stored_round_receipts_and_main_reported_extra_checks",
        "counts": {"attempts": counts["attempts"], "completed": counts["completed"],
                   "passed": counts["passed"], "failed": counts["failed"],
                   "interrupted": counts["interrupted"], "unknown": counts["unknown"]},
        "countsComplete": counts_complete,
        "uniqueReceipts": len(groups),
        "deduplicatedSharedReceipts": shared,
        "sources": {"roundSummaryReceipts": sources["round_summary"],
                    "extraCheckReceipts": sources["extra_check"]},
        "elapsedSeconds": {"value": elapsed if counts["completed"] and math.isfinite(elapsed)
                                    else None,
                           "complete": counts_complete and elapsed_missing == 0
                                       and math.isfinite(elapsed),
                           "samples": counts["completed"], "missingTime": elapsed_missing,
                           "label": "sum of completed receipt intervals"},
        "wallCoverageSeconds": {"value": wall_union,
                                "label": "union of recorded receipt intervals; checks are "
                                         "inside tool coverage"},
        "potentialRepeats": repeats,
        "coverage": _status(round_known, round_missing, round_unknown)
                    | {"unattributedReceipts": unattributed, "untrusted": untrusted,
                       "conflicts": conflicts, "degraded": degraded,
                       "note": "recorded-check coverage only; uninstrumented checks remain "
                               "outside, and references are never a new execution"}}


def _usage_section(payload, rows, *, group_models=True):
    """Assistant + auxiliary + total facts from stored summaries, never re-derived."""
    assistant = {key: {"sum": 0.0, "known": [], "incomplete": []}
                 for key in ASSISTANT_METRIC_KEYS + ("reasoning",)}
    total = {key: {"sum": 0.0, "known": [], "incomplete": []}
             for key in ASSISTANT_METRIC_KEYS}
    auxiliary = {key: {"sum": 0.0, "known": [], "incomplete": []}
                 for key in AUXILIARY_METRIC_KEYS}
    pi_cost_sum, pi_cost_known, pi_cost_unknown = 0.0, [], []
    missing_summary, missing_total, invalid = [], [], []
    models, expected = Counter(), set()
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        summary = row["summary"]
        if not isinstance(summary, dict) or not isinstance(summary.get("metrics"), dict):
            missing_summary.append(key)
            missing_total.append(key)
            pi_cost_unknown.append(key)
            for bucket in (assistant, total, auxiliary):
                for metric in bucket:
                    bucket[metric]["incomplete"].append(key)
            continue
        metrics = summary["metrics"]
        usage = metrics.get("usage")
        auxiliary_section = metrics.get("auxiliary")
        total_section = metrics.get("total")
        for metric in ASSISTANT_METRIC_KEYS + ("reasoning",):
            value = _leaf_value(usage, metric, integer=True)
            if value is None:
                if isinstance(usage, dict) and isinstance(usage.get(metric), dict) \
                        and usage[metric].get("value") is not None:
                    invalid.append({"round": key, "field": f"usage.{metric}"})
                assistant[metric]["incomplete"].append(key)
            else:
                assistant[metric]["sum"] += value
                assistant[metric]["known"].append(key)
                if not _leaf_complete(usage, metric):
                    assistant[metric]["incomplete"].append(key)
        if isinstance(auxiliary_section, dict):
            for metric in AUXILIARY_METRIC_KEYS:
                integer = metric != "reportedCostUsd"
                value = _leaf_value(auxiliary_section, metric, integer=integer)
                if value is None:
                    if isinstance(auxiliary_section.get(metric), dict) \
                            and auxiliary_section[metric].get("value") is not None:
                        invalid.append({"round": key, "field": f"auxiliary.{metric}"})
                    auxiliary[metric]["incomplete"].append(key)
                else:
                    auxiliary[metric]["sum"] += value
                    auxiliary[metric]["known"].append(key)
                    if not _leaf_complete(auxiliary_section, metric):
                        auxiliary[metric]["incomplete"].append(key)
        else:
            for metric in AUXILIARY_METRIC_KEYS:
                auxiliary[metric]["incomplete"].append(key)
        if isinstance(total_section, dict):
            combined = total_section.get("usage") if isinstance(total_section.get("usage"), dict) else {}
            total_complete = total_section.get("complete") is True
            for metric in ASSISTANT_METRIC_KEYS:
                source_key = "input" if metric == "uncachedInput" else metric
                value = _plain_number(combined.get(source_key), integer=True)
                if value is None:
                    if combined.get(metric) is not None:
                        invalid.append({"round": key, "field": f"total.usage.{metric}"})
                    total[metric]["incomplete"].append(key)
                else:
                    total[metric]["sum"] += value
                    total[metric]["known"].append(key)
                    if not total_complete:
                        total[metric]["incomplete"].append(key)
            cost = _plain_number(total_section.get("reportedCostUsd"))
            if cost is None:
                if total_section.get("reportedCostUsd") is not None:
                    invalid.append({"round": key, "field": "total.reportedCostUsd"})
                pi_cost_unknown.append(key)
            else:
                pi_cost_sum += cost
                pi_cost_known.append(key)
                if total_section.get("costComplete") is not True:
                    pi_cost_unknown.append(key)
        else:
            missing_total.append(key)
            pi_cost_unknown.append(key)
            for metric in ASSISTANT_METRIC_KEYS:
                total[metric]["incomplete"].append(key)
        if isinstance(summary.get("models"), dict):
            models.update({k: v for k, v in summary["models"].items()
                           if isinstance(k, str) and isinstance(v, int)
                           and not isinstance(v, bool) and v > 0})
        if isinstance(summary.get("expected_model"), str):
            expected.add(summary["expected_model"])
    def aggregate(bucket):
        result = {}
        for metric, data in bucket.items():
            overflow = not math.isfinite(data["sum"])
            result[metric] = {"known": (data["sum"] if data["known"] and not overflow else None),
                              "roundsKnown": data["known"], "roundsIncomplete": data["incomplete"],
                              "complete": bool(rows) and len(data["known"]) == len(rows)
                                          and not data["incomplete"] and not overflow,
                              "overflow": overflow}
        return result
    if not rows:
        pi_report = {"applicable": False, "assistant": None, "auxiliary": None, "total": None,
                     "reasoningTokens": None, "costUsd": None,
                     "note": "no Pi member rounds were reported for this outcome"}
        identity = {"recorded": {}, "expectedReported": [], "status": "not_applicable"}
    else:
        cost_overflow = not math.isfinite(pi_cost_sum)
        total_agg = aggregate(total)
        pi_report = {
            "applicable": True,
            "assistant": aggregate(assistant),
            "auxiliary": aggregate(auxiliary),
            "total": total_agg,
            "totalComplete": bool(rows) and not missing_total and all(
                item["complete"] for item in total_agg.values()),
            "costUsd": {"known": (pi_cost_sum if pi_cost_known and not cost_overflow else None),
                        "complete": bool(rows) and len(pi_cost_known) == len(rows)
                                    and not pi_cost_unknown and not cost_overflow,
                        "roundsKnown": pi_cost_known, "roundsUnknown": pi_cost_unknown,
                        "scope": "pi_assistant_and_auxiliary", "source": "provider_reported"},
            "roundsMissingSummaries": missing_summary,
            "roundsMissingTotal": missing_total,
            "note": "assistant, auxiliary and the combined total are consumed from the stored "
                    "round summary once; auxiliary usage/cost is never added twice; reasoning "
                    "already sits inside output and is shown separately; an absent field stays "
                    "null and invalid or overflowing sources stay unknown"}
        identity = _model_identity(models, expected, missing_summary)
    work = payload["work"]
    external_usage = {key: {"sum": 0.0, "known": []} for key in USAGE_KEYS}
    for entry in work:
        for key in USAGE_KEYS:
            value = (entry.get("usage") or {}).get(key)
            if value is not None:
                external_usage[key]["sum"] += value
                external_usage[key]["known"].append(entry["id"])
    external_usage_view = {key: (external_usage[key]["sum"]
                                 if external_usage[key]["known"]
                                 and math.isfinite(external_usage[key]["sum"]) else None)
                           for key in USAGE_KEYS}
    if not work:
        external_complete = bool(payload["workComplete"])
        external = {"declaredComplete": bool(payload["workComplete"]), "workEntries": 0,
                    "declaredZero": external_complete,
                    "usage": external_usage_view,
                    "reportedCostUsd": 0.0 if external_complete else None,
                    "complete": external_complete,
                    "reason": None if external_complete else
                    "no external work entries and completeness was not declared",
                    "source": "main_reported_work"}
    else:
        known, known_ids, missing_costs = 0.0, [], []
        for entry in work:
            cost = entry.get("reportedCostUsd")
            if cost is None:
                missing_costs.append(entry["id"])
            else:
                known += cost
                known_ids.append(entry["id"])
        overflow = not math.isfinite(known)
        external_complete = bool(payload["workComplete"]) and not missing_costs and not overflow
        external = {"declaredComplete": bool(payload["workComplete"]), "workEntries": len(work),
                    "declaredZero": False,
                    "usage": external_usage_view,
                    "reportedCostUsd": known if known_ids and not overflow else None,
                    "costEntries": known_ids, "missingCostEntries": missing_costs,
                    "complete": external_complete, "overflow": overflow,
                    "reason": None if external_complete else
                    ("a declared external work entry lacks a reported cost"
                     if payload["workComplete"] else
                     "external cost completeness was not declared (workComplete is false)"),
                    "source": "main_reported_work"}
    combined, reason = None, None
    if rows and not pi_report["costUsd"]["complete"]:
        reason = "Pi reported assistant+auxiliary cost coverage is incomplete"
    elif not payload["workComplete"]:
        reason = "external cost completeness was not declared (workComplete is false)"
    elif not external["complete"]:
        reason = external["reason"]
    else:
        combined = (pi_report["costUsd"]["known"] if rows else 0.0) \
            + (external["reportedCostUsd"] or 0.0)
        if not _finite(combined):
            combined, reason = None, "combined reported cost overflowed"
    if group_models:
        # Only single-identity assistant rounds can be allocated without guessing.
        # Native auxiliary summaries have no per-model identity, so their totals
        # remain in the explicitly aggregate view above, never assigned by share.
        buckets = {}
        for row in rows:
            identities = (row.get("summary") or {}).get("models")
            valid = isinstance(identities, dict) and identities and all(
                isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
                for k, v in identities.items())
            model = next(iter(identities)) if valid and len(identities) == 1 else None
            buckets.setdefault(model, []).append(row)
        pi_report["assistantByModel"] = []
        for model, model_rows in sorted(buckets.items(), key=lambda pair: pair[0] or ""):
            model_view = _usage_section(dict(payload, work=[], workComplete=False),
                                        model_rows, group_models=False)
            costs = [_plain_number((row.get("summary") or {}).get("reported_cost_usd"))
                     for row in model_rows]
            known_costs = [cost for cost in costs if cost is not None]
            cost_sum = sum(known_costs)
            pi_report["assistantByModel"].append({
                "model": model, "attribution": "recorded_single_model" if model else "unattributed",
                "rounds": [f"{row['task']}/{row['round']}" for row in model_rows],
                "usage": model_view["piReported"]["assistant"],
                "reportedCostUsd": cost_sum if known_costs and _finite(cost_sum) else None,
                "costComplete": model_view["piReported"]["costUsd"]["complete"]
                    and len(known_costs) == len(model_rows) and _finite(cost_sum)})
        pi_report["modelAttributionNote"] = (
            "assistant-only model groups; mixed/unknown rounds remain unattributed; "
            "auxiliary summaries lack model identity and are never allocated by message count")
        external["entries"] = work
    return {
        "source": "stored_round_summaries_and_main_reported_work",
        "modelIdentity": identity,
        "piReported": pi_report,
        "externalReported": external,
        "combinedReportedCostUsd": {"value": combined, "reason": reason,
                                     "note": "combined only from complete Pi assistant+auxiliary "
                                             "cost coverage plus a declared-complete external "
                                             "report; a work declaration never invents a number"},
        "billing": {"verified": False, "costUsd": None,
                    "note": "interface/SDK reported cost is not a provider bill; billing is "
                            "never verified here"},
        "invalidSourceValues": invalid[:MAX_PROBLEM_DETAILS],
        "notes": ["reasoning tokens are already inside output; they are reported separately and "
                  "never added to output a second time",
                  "unknown numeric fields stay null; a missing value is never zero, and mixed "
                  "model identities are never attributed to one configured model"]}


def _model_identity(models, expected, missing_summaries):
    if not models:
        return {"recorded": {}, "expectedReported": sorted(expected), "status": "unknown",
                "roundsMissingSummaries": missing_summaries}
    if len(models) == 1 and expected == set(models):
        return {"recorded": dict(models), "expectedReported": sorted(expected), "status": "matched"}
    if len(models) > 1:
        return {"recorded": dict(models), "expectedReported": sorted(expected), "status": "mixed",
                "note": "mixed model identities are never attributed to one configured model"}
    return {"recorded": dict(models), "expectedReported": sorted(expected),
            "status": "mismatched",
            "note": "recorded model identity differs from the frozen expected model"}


def _rework_section(payload, rows, facts, events, archive_coverage, verification,
                    takeover_evidence):
    failed_rounds, unknown_rounds = [], []
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        state = row["state"]
        if state is None:
            unknown_rounds.append(key)
            continue
        value = state.get("state")
        if value in ("failed", "timed_out", "cancelled", "interrupted"):
            failed_rounds.append({"round": key, "state": value, "exitCode": state.get("exitCode")})
        elif value not in ("completed",):
            unknown_rounds.append(key)
    transport, observations = Counter(), Counter()
    transport_rounds, transport_unknown = [], []
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        meta = row["taskMeta"]
        network = meta.get("network") if isinstance(meta, dict) else None
        diagnostics = network.get("diagnostics") if isinstance(network, dict) else None
        if diagnostics is not True or row["roundDir"] is None:
            transport_unknown.append(key)
            continue
        sidecar_path, problem = _inspect_under(row["roundDir"] / "round.network.jsonl",
                                               row["roundDir"])
        sidecar = read_network_sidecar(sidecar_path) if problem is None else {"status": "unreadable"}
        if sidecar.get("status") in ("missing", "unreadable"):
            transport_unknown.append(key)
            continue
        transport_rounds.append(key)
        if sidecar.get("truncated") or sidecar.get("oversized"):
            transport_unknown.append(key)
        for name, value in (sidecar.get("classes") or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                observations[name] += value
                if name != "abort_cleanup":
                    transport[name] += value
    return {
        "source": "native_rounds_receipts_decisions_network",
        "reviewedQualityFailures": {
            "count": facts["qualityCount"], "events": facts["qualityEvents"],
            "coverage": dict(archive_coverage,
                             note="distinct (task, round) quality delivery decisions from the "
                                  "selected rounds only; execution/resolve/external and duplicate "
                                  "events never count")},
        "failedCheckAttempts": {"value": verification["counts"]["failed"],
                                 "complete": verification["countsComplete"],
                                 "source": "unique receipts across selected member rounds and "
                                           "extra Main checks",
                                 "sources": verification["sources"]},
        "failedRounds": failed_rounds,
        "unknownRounds": unknown_rounds,
        "transportErrors": {"classes": {key: transport[key] for key in sorted(transport)},
                            "observations": dict(observations),
                            "roundsWithReadableSidecar": transport_rounds,
                            "roundsUnknown": transport_unknown,
                            "coverage": _status(transport_rounds, [], transport_unknown)},
        "nativeTakeover": {"observed": any(entry.get("source") == "archived_takeover_event"
                                            for entry in takeover_evidence),
                           "events": [entry for entry in takeover_evidence
                                      if entry.get("source") == "archived_takeover_event"],
                           "note": "archived takeover events attributed to the selected rounds; "
                                   "an explicit main takeover latch is reported separately in "
                                   "intervention coverage"}}


def _intervention_section(payload, rows, takeover_evidence, takeover_coverage):
    kinds = Counter(entry["kind"] for entry in payload["work"])
    observed = any(entry.get("attribution") == "selected_round" for entry in takeover_evidence)
    failed = [row for row in rows if isinstance(row.get("state"), dict)
              and row["state"].get("state") in ("failed", "timed_out", "cancelled", "interrupted")]
    if payload["status"] != "accepted":
        completion = "reported_not_accepted"
    elif observed and payload["members"]:
        completion = "assisted_main_completion"
    elif failed:
        completion = "main_reported_after_failed_pi_execution"
    else:
        completion = "main_reported"
    return {
        "source": "main_reported_work_and_native_takeover",
        "workEntries": {"count": len(payload["work"]),
                        "kinds": {key: kinds[key] for key in sorted(kinds)},
                        "declaredComplete": bool(payload["workComplete"]),
                        "note": "absence of work records is not proof of no intervention"},
        "nativeTakeover": {"observed": observed, "evidence": takeover_evidence,
                           "coverage": dict(takeover_coverage,
                                            note="board latch and archived takeover events "
                                                 "attributed to selected rounds; an explicit main "
                                                 "takeover keeps cause main_decision")},
        "completion": completion,
        "missingExternalCoverage": not payload["workComplete"],
        "coverage": {"status": "known" if payload["members"] else "not_applicable",
                     "memberTasks": len(payload["members"]),
                     "memberRounds": len(rows),
                     "note": "direct Codex outcomes may have no Pi members; a reported "
                             "accepted delivery with failed Pi execution keeps both facts"}}


def outcome_metrics(root, common, outcome_id) -> dict:
    """Six compact read-only sections over one immutable recorded observation."""
    envelope = load_outcome(common, outcome_id)
    payload = envelope["payload"]
    members = payload.get("members") if isinstance(payload.get("members"), list) else []
    selected = _selected_rounds(members)
    rows, _scan_bytes = _load_member_rows(common, selected)
    decisions, events, archive_coverage = _read_archive(common, selected)
    facts = _attribution_facts(decisions)
    takeover_evidence, takeover_coverage = _takeover_latches(common, selected, events)
    if takeover_coverage.get("problem"):
        archive_coverage = dict(archive_coverage,
                                status="unknown" if members else "not_applicable",
                                problem=takeover_coverage["problem"])
    verification = _verification_section(common, payload, rows)
    timing = _timing_section(payload, rows, events, archive_coverage)
    timing["recordedAt"] = envelope["recordedAt"]
    timing["recordedAtNote"] = ("observation time only; never substituted for the actual "
                                 "acceptance/delivery time")
    return {
        "outcome": outcome_id,
        "schemaVersion": SCHEMA_VERSION,
        "revision": envelope["revision"],
        "recordedAt": envelope["recordedAt"],
        "payloadDigest": envelope["payloadDigest"],
        "status": payload.get("status"),
        "candidate": payload.get("candidate"),
        "members": members,
        "delivery": _delivery_section(payload, rows, facts, archive_coverage),
        "timing": timing,
        "reworkIncidents": _rework_section(payload, rows, facts, events, archive_coverage,
                                            verification, takeover_evidence),
        "verification": verification,
        "usageCost": _usage_section(payload, rows),
        "interventionCoverage": _intervention_section(payload, rows, takeover_evidence,
                                                       takeover_coverage),
        "acceptance": "not_verified",
        "note": "Main-reported closeout observation over recorded metadata; never a phase "
                "acceptance, ownership decision or permission to execute",
    }


def _member_source_flags(common, members) -> dict:
    present, missing = [], []
    rows, _ = _load_member_rows(common, _selected_rounds(members))
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        (missing if row["taskProblem"] or row["stateProblem"] or row["summaryProblem"]
         else present).append(key)
    return {"present": present, "missing": missing}


def list_outcomes(common, cursor="", limit=LISTING_PAGE) -> dict:
    """Bounded cursor page of recorded observations; never a success-rate denominator."""
    if cursor and (not isinstance(cursor, str) or not OUTCOME_ID_RE.fullmatch(cursor)):
        raise ValueError("outcome cursor must be a valid recorded outcome id")
    try:
        page_size = int(limit)
    except (TypeError, ValueError):
        page_size = LISTING_PAGE
    page_size = max(1, min(page_size, LISTING_PAGE))
    root = _outcomes_root(common)
    ids = []
    if root.is_dir():
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.is_dir() and not entry.is_symlink() and OUTCOME_ID_RE.fullmatch(entry.name):
                if cursor and entry.name <= cursor:
                    continue
                ids.append(entry.name)
    rows, more = [], False
    for index, outcome_id in enumerate(ids):
        if index >= page_size:
            more = True
            break
        try:
            envelope = load_outcome(common, outcome_id)
        except ValueError as exc:
            rows.append({"outcome": outcome_id, "status": "incomplete", "problem": str(exc)})
            continue
        payload = envelope["payload"]
        rows.append({"outcome": outcome_id, "revision": envelope["revision"],
                     "status": payload.get("status"), "recordedAt": envelope["recordedAt"],
                     "members": payload.get("members"),
                     "memberSources": _member_source_flags(common, payload.get("members") or []),
                     "problem": None})
    return {"ok": True, "outcomes": rows,
            "nextCursor": rows[-1]["outcome"] if more and rows else None,
            "pageSize": page_size,
            "note": "bounded view of recorded observations, not a complete denominator of all "
                    "work; success rates or savings are never derived here"}
