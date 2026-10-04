#!/usr/bin/env python3
"""Source-backed, main-reported outcome observations for the metrics CLI (0.8.5).

Storage is a bounded sequence of immutable JSON revisions under the private
git-common ``codex-pi/outcomes/<ID>/`` directory. A closeout is a *reported
observation*: it never proves acceptance, never owns execution and never grants
permission. Recording and projection only read existing bounded metadata
(task/round state, stored round summaries, archived decisions/events, standard
check receipts and network sidecars). This module never invokes a model, never
executes a task, never mutates board/execution/budget/quality state and never
scans a raw session or raw check log for statistics.

Dependency direction: this is a lower-level helper. It imports storage/core
modules only and must never import an entry module (``pi_task``/``pi_board``/
``pi_handoff``).
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path
import time

from pi_archive import STORE_FILE, decision_page, event_page
from pi_core import (
    TASK_RE,
    atomic,
    git,
    inside,
    lock_fd,
    read_network_sidecar,
    state_root,
    task_dir_for,
)
from pi_store import FULL_OID_RE, _read_bounded_json, board_file_for_common, read_board
from pi_summary import union_seconds

SCHEMA_VERSION = 1
OUTCOME_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
WORK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
CHECK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
REVISION_RE = re.compile(r"revision-(\d{6})\.json\Z")

STATUSES = ("accepted", "partial", "failed", "cancelled", "unknown")
WORK_KINDS = ("design", "review", "debug", "supplemental_checks", "takeover", "other")
USAGE_KEYS = ("input", "cacheRead", "cacheWrite", "output", "totalTokens")
SUMMARY_USAGE_KEYS = ("uncachedInput", "cacheRead", "cacheWrite", "output", "reasoning",
                      "totalTokens")
SUMMARY_TIMING_KEYS = ("windowSeconds", "modelResponseSeconds", "toolSeconds",
                       "unattributedSeconds")
DELIVERY_EVENT_KINDS = ("review_required", "phase_blocked")
TAKEOVER_EVENT_KIND = "codex_takeover_required"

# Bounded input: nothing here is allowed to grow with transcript size.
MAX_RECORD_BYTES = 1_000_000
MAX_REVISION_BYTES = 2_000_000
MAX_TASK_BYTES = 2_000_000
MAX_STATE_BYTES = 2_000_000
MAX_SUMMARY_BYTES = 4_000_000
MAX_RECEIPT_BYTES = 1_000_000
MAX_MEMBERS = 100
MAX_MEMBER_ROUNDS = 200
MAX_EVIDENCE_REFS = 50
MAX_REF_CHARS = 500
MAX_WORK = 200
MAX_WORK_TEXT_CHARS = 200
MAX_EXTRA_CHECKS = 50
MAX_PATH_CHARS = 500
MAX_ARGV_ITEMS = 100
MAX_ARGV_CHARS = 500
LISTING_PAGE = 20
MAX_ARCHIVE_PAGES = 40
MAX_POTENTIAL_REPEAT_SOURCES = 5

# Fields that may appear on exactly one reported work entry.
WORK_FIELDS = frozenset(("id", "kind", "actor", "model", "startedAt", "finishedAt",
                         "usage", "reportedCostUsd", "evidenceRef"))
PAYLOAD_FIELDS = frozenset(("members", "status", "candidate", "evidenceRefs", "startedAt",
                            "finishedAt", "work", "workComplete", "extraChecks"))


# ---------------------------------------------------------------------------
# small validators
# ---------------------------------------------------------------------------

def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _time_value(value, name):
    """A finite non-negative Unix timestamp; ``None`` stays an explicit absence."""
    if value is None:
        return None
    if not _finite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Unix timestamp")
    return value


def _token_count(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value < 0 or not float(value).is_integer():
        raise ValueError(f"{name} must be a non-negative whole token count")
    return int(value)


def _cost_value(value, name):
    if not _finite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative reported cost")
    return value


def _bool_value(value, name):
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean when present")
    return value


def _text_value(value, name, limit=MAX_REF_CHARS, required=False):
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return value


def validate_outcome_id(value) -> str:
    if not isinstance(value, str) or not OUTCOME_ID_RE.fullmatch(value) or ".." in value:
        raise ValueError("outcome id must be 1..100 characters of [A-Za-z0-9._-], "
                         "not starting with a dot and without '..'")
    return value


def outcome_path(common, outcome_id) -> Path:
    """The bounded outcome directory; the id regex plus ``inside`` block escapes."""
    outcome_id = validate_outcome_id(outcome_id)
    root = (state_root(Path(common)) / "outcomes").resolve()
    directory = root / outcome_id
    if not inside(directory, root):
        raise ValueError("outcome id resolves outside the outcomes directory")
    return directory


def payload_digest(payload) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# payload validation / normalization
# ---------------------------------------------------------------------------

def _normalize_members(raw, common):
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("members must be a list")
    if len(raw) > MAX_MEMBERS:
        raise ValueError(f"members exceeds {MAX_MEMBERS} entries")
    result, seen = [], set()
    total_rounds = 0
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"task", "rounds"}:
            raise ValueError("each member must be an object with exactly task and rounds")
        task = item["task"]
        if not isinstance(task, str) or not TASK_RE.fullmatch(task):
            raise ValueError("member task must match [A-Za-z0-9][A-Za-z0-9_-]{0,99}")
        rounds = item["rounds"]
        if not isinstance(rounds, list) or not rounds:
            raise ValueError(f"member {task!r} needs at least one round")
        numbers = []
        for number in rounds:
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise ValueError(f"member {task!r} rounds must be positive integers")
            key = (task, number)
            if key in seen:
                raise ValueError(f"duplicate member round {task}/{number}")
            seen.add(key)
            numbers.append(number)
        total_rounds += len(numbers)
        if total_rounds > MAX_MEMBER_ROUNDS:
            raise ValueError(f"member rounds exceed {MAX_MEMBER_ROUNDS} total")
        task_dir = task_dir_for(Path(common), task)
        if not (task_dir / "task.json").is_file():
            raise ValueError(f"member task {task!r} has no task.json in this git-common directory")
        for number in numbers:
            state_path = task_dir / "rounds" / str(number) / "round.state.json"
            if not state_path.is_file():
                raise ValueError(f"member round {task}/{number} has no round.state.json")
        result.append({"task": task, "rounds": sorted(numbers)})
    result.sort(key=lambda member: member["task"])
    return result


def _normalize_evidence_refs(raw, status):
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("evidenceRefs must be a list")
    refs = sorted({_text_value(ref, "evidenceRefs entry", MAX_REF_CHARS, required=True)
                   for ref in raw})
    if len(refs) > MAX_EVIDENCE_REFS:
        raise ValueError(f"evidenceRefs exceeds {MAX_EVIDENCE_REFS} entries")
    if status == "accepted" and not refs:
        raise ValueError("an accepted report requires at least one Main-reported evidenceRef")
    return refs


def _normalize_work(raw):
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("work must be a list")
    if len(raw) > MAX_WORK:
        raise ValueError(f"work exceeds {MAX_WORK} entries")
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each work entry must be an object")
        unknown = sorted(set(item) - WORK_FIELDS)
        if unknown:
            raise ValueError(f"unknown work fields: {', '.join(unknown)}")
        work_id = item.get("id")
        if not isinstance(work_id, str) or not WORK_ID_RE.fullmatch(work_id):
            raise ValueError("work id must match [A-Za-z0-9][A-Za-z0-9._-]{0,99}")
        if work_id in seen:
            raise ValueError(f"duplicate work id {work_id!r}")
        seen.add(work_id)
        kind = item.get("kind")
        if kind not in WORK_KINDS:
            raise ValueError(f"work kind must be one of {', '.join(WORK_KINDS)}")
        entry = {"id": work_id, "kind": kind}
        entry["actor"] = _text_value(item.get("actor"), f"work {work_id} actor", MAX_WORK_TEXT_CHARS)
        entry["model"] = _text_value(item.get("model"), f"work {work_id} model", MAX_WORK_TEXT_CHARS)
        entry["startedAt"] = _time_value(item.get("startedAt"), f"work {work_id} startedAt")
        entry["finishedAt"] = _time_value(item.get("finishedAt"), f"work {work_id} finishedAt")
        if entry["startedAt"] is not None and entry["finishedAt"] is not None \
                and entry["startedAt"] > entry["finishedAt"]:
            raise ValueError(f"work {work_id} finishedAt precedes startedAt")
        usage_raw = item.get("usage")
        usage = {key: None for key in USAGE_KEYS}
        if usage_raw is not None:
            if not isinstance(usage_raw, dict):
                raise ValueError(f"work {work_id} usage must be an object")
            unknown = sorted(set(usage_raw) - set(USAGE_KEYS))
            if unknown:
                raise ValueError(f"unknown work usage fields: {', '.join(unknown)}")
            for key in USAGE_KEYS:
                if usage_raw.get(key) is not None:
                    usage[key] = _token_count(usage_raw[key], f"work {work_id} usage.{key}")
        cost = None
        if item.get("reportedCostUsd") is not None:
            cost = _cost_value(item["reportedCostUsd"], f"work {work_id} reportedCostUsd")
        ref = _text_value(item.get("evidenceRef"), f"work {work_id} evidenceRef", MAX_REF_CHARS)
        supplies = any(value is not None for value in usage.values()) or cost is not None \
            or entry["startedAt"] is not None or entry["finishedAt"] is not None
        if supplies and ref is None:
            raise ValueError(f"work {work_id} supplies quantities and needs an evidenceRef")
        entry.update(usage=usage, reportedCostUsd=cost, evidenceRef=ref)
        result.append(entry)
    result.sort(key=lambda entry: entry["id"])
    return result


def _receipt_metadata(data, digest, rel):
    """Bounded standard pi_check receipt metadata plus a pinned file digest."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"extra check {rel!r} is not a standard pi_check receipt")
    receipt_id = data.get("id")
    if not isinstance(receipt_id, str) or not CHECK_ID_RE.fullmatch(receipt_id):
        raise ValueError(f"extra check {rel!r} has no valid check id")
    argv = data.get("argv")
    if not isinstance(argv, list) or any(not isinstance(arg, str) for arg in argv):
        raise ValueError(f"extra check {rel!r} has no valid argv")
    exit_code = data.get("exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise ValueError(f"extra check {rel!r} has an invalid exit code")
    started = data.get("started_at")
    ended = data.get("ended_at")
    if not _finite(started) or not _finite(ended) or started < 0 or ended < started:
        raise ValueError(f"extra check {rel!r} has invalid timestamps")
    if not isinstance(data.get("log_sha256"), str):
        raise ValueError(f"extra check {rel!r} has no pinned log digest")
    timed_out = data.get("timed_out")
    cancelled = data.get("cancelled")
    if not isinstance(timed_out, bool) or not isinstance(cancelled, bool):
        raise ValueError(f"extra check {rel!r} has invalid interruption flags")
    counts = data.get("test_counts")
    test_counts = {key: value for key, value in counts.items()
                   if isinstance(value, int) and not isinstance(value, bool)} \
        if isinstance(counts, dict) else None
    argv_out = [arg[:MAX_ARGV_CHARS] for arg in argv[:MAX_ARGV_ITEMS]]
    argv_complete = len(argv) <= MAX_ARGV_ITEMS and all(len(arg) <= MAX_ARGV_CHARS for arg in argv)
    head = data.get("head")
    return {"path": rel, "sha256": digest,
            "id": receipt_id, "exitCode": exit_code, "startedAt": started, "endedAt": ended,
            "timedOut": timed_out, "cancelled": cancelled,
            "head": head if isinstance(head, str) and FULL_OID_RE.fullmatch(head) else None,
            "argv": argv_out, "argvComplete": argv_complete,
            "testCounts": test_counts}


def _normalize_extra_checks(raw, common):
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("extraChecks must be a list of receipt paths")
    if len(raw) > MAX_EXTRA_CHECKS:
        raise ValueError(f"extraChecks exceeds {MAX_EXTRA_CHECKS} entries")
    root = state_root(Path(common)).resolve()
    entries = {}
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("each extraChecks entry must be a non-empty relative path")
        if len(value) > MAX_PATH_CHARS:
            raise ValueError(f"extraChecks path exceeds {MAX_PATH_CHARS} characters")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe extraChecks path {value!r}")
        resolved = (root / relative).resolve()
        if not inside(resolved, root):
            raise ValueError(f"extraChecks path escapes the codex-pi root: {value!r}")
        if not resolved.is_file():
            raise ValueError(f"extraChecks receipt does not exist: {value!r}")
        try:
            raw_bytes = resolved.read_bytes()
        except OSError as exc:
            raise ValueError(f"extraChecks receipt is unreadable: {value!r}") from exc
        if len(raw_bytes) > MAX_RECEIPT_BYTES:
            raise ValueError(f"extraChecks receipt is oversized: {value!r}")
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"extraChecks receipt is invalid JSON: {value!r}") from exc
        rel = str(resolved.relative_to(root))
        entries[rel] = _receipt_metadata(data, hashlib.sha256(raw_bytes).hexdigest(), rel)
    return [entries[key] for key in sorted(entries)]


def _normalize_payload(payload, common, root):
    if not isinstance(payload, dict):
        raise ValueError("outcome record must be a JSON object")
    unknown = sorted(set(payload) - PAYLOAD_FIELDS)
    if unknown:
        raise ValueError(f"unknown outcome fields: {', '.join(unknown)}")
    status = payload.get("status")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    members = _normalize_members(payload.get("members"), common)
    candidate = payload.get("candidate")
    if candidate is not None:
        if not isinstance(candidate, str) or not FULL_OID_RE.fullmatch(candidate):
            raise ValueError("candidate must be a full 40- or 64-hex commit id")
        try:
            git(Path(root), "cat-file", "-e", candidate + "^{commit}")
        except Exception as exc:  # noqa: BLE001 - any git failure means not a real commit
            raise ValueError(f"candidate {candidate!r} is not a real commit in the repository") from exc
    elif status == "accepted":
        raise ValueError("an accepted report requires the full candidate commit")
    started = _time_value(payload.get("startedAt"), "startedAt")
    finished = _time_value(payload.get("finishedAt"), "finishedAt")
    if started is not None and finished is not None and started > finished:
        raise ValueError("finishedAt precedes startedAt")
    return {"members": members, "status": status, "candidate": candidate,
            "evidenceRefs": _normalize_evidence_refs(payload.get("evidenceRefs"), status),
            "startedAt": started, "finishedAt": finished,
            "work": _normalize_work(payload.get("work")),
            "workComplete": _bool_value(payload.get("workComplete"), "workComplete") is True,
            "extraChecks": _normalize_extra_checks(payload.get("extraChecks"), common)}


# ---------------------------------------------------------------------------
# storage: immutable numbered revisions
# ---------------------------------------------------------------------------

def _revision_files(directory: Path) -> dict:
    found = {}
    if not directory.is_dir():
        return found
    for entry in directory.iterdir():
        match = REVISION_RE.fullmatch(entry.name)
        if not match:
            continue
        if not entry.is_file() or entry.is_symlink():
            raise ValueError("outcome history contains an unsafe revision entry")
        number = int(match.group(1))
        if number in found:
            raise ValueError("outcome history is ambiguous: duplicate revision numbers")
        found[number] = entry
    return found


def _validate_envelope(data, revision: int):
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("outcome revision has an invalid schema")
    if data.get("revision") != revision:
        raise ValueError("outcome revision identity mismatch")
    recorded = data.get("recordedAt")
    if not _finite(recorded) or recorded < 0:
        raise ValueError("outcome revision has an invalid recordedAt")
    payload = data.get("payload")
    digest = data.get("payloadDigest")
    if not isinstance(payload, dict) or not isinstance(digest, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("outcome revision has an invalid payload digest")
    if payload_digest(payload) != digest:
        raise ValueError("outcome revision payload digest does not match its content")
    return {"schemaVersion": SCHEMA_VERSION, "revision": revision, "recordedAt": recorded,
            "payloadDigest": digest, "payload": payload}


def _load_history(directory: Path) -> list:
    """All valid revisions in order; missing/gapped/corrupt history raises."""
    found = _revision_files(directory)
    if not found:
        return []
    numbers = sorted(found)
    if numbers != list(range(1, numbers[-1] + 1)):
        raise ValueError("history has a gap or an ambiguous latest revision")
    envelope = []
    for number in numbers:
        data, problem = _read_bounded_json(found[number], MAX_REVISION_BYTES)
        if problem is not None:
            raise ValueError(f"revision {number} is {problem}")
        try:
            envelope.append(_validate_envelope(data, number))
        except ValueError as exc:
            raise ValueError(f"revision {number}: {exc}") from None
    return envelope


def load_outcome(common, outcome_id) -> dict:
    """Latest immutable revision; missing/gapped/corrupt history is a hard error."""
    directory = outcome_path(common, outcome_id)
    try:
        history = _load_history(directory)
    except ValueError as exc:
        raise ValueError(f"outcome {outcome_id!r} {exc}") from None
    if not history:
        raise ValueError(f"outcome {outcome_id!r} is not recorded")
    return history[-1]


def record_outcome(root, common, outcome_id, payload, expected_revision=None, now=None) -> dict:
    """Compare-and-publish one immutable revision under a local write lock.

    An identical normalized payload replay is idempotent, including an uncertain
    write retry. A correction needs the exact current revision; a stale or
    conflicting update fails without changing any file.
    """
    root, common = Path(root), Path(common)
    outcome_id = validate_outcome_id(outcome_id)
    if expected_revision is not None:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
                or expected_revision < 0:
            raise ValueError("expected revision must be a non-negative integer")
    normalized = _normalize_payload(payload, common, root)
    digest = payload_digest(normalized)
    timestamp = time.time() if now is None else float(now)
    directory = outcome_path(common, outcome_id)
    fd = lock_fd(directory / "write.lock", blocking=True, timeout=30)
    try:
        try:
            history = _load_history(directory)
        except ValueError as exc:
            raise ValueError(f"outcome {outcome_id!r} history is corrupt ({exc}); "
                             "refusing to publish") from None
        latest = history[-1] if history else None
        if latest is not None and latest["payloadDigest"] == digest:
            return {"ok": True, "outcome": outcome_id, "revision": latest["revision"],
                    "idempotent": True, "recordedAt": latest["recordedAt"],
                    "payloadDigest": digest,
                    "note": "identical normalized payload replay; no new revision written"}
        current_number = latest["revision"] if latest else 0
        if expected_revision is None:
            if current_number:
                raise ValueError(f"outcome {outcome_id!r} already has revision {current_number}; "
                                 f"pass --expected-revision {current_number} to correct it")
            expected_revision = 0
        if expected_revision != current_number:
            raise ValueError(f"stale expected revision {expected_revision}: "
                             f"current revision is {current_number}")
        number = current_number + 1
        envelope = {"schemaVersion": SCHEMA_VERSION, "revision": number, "recordedAt": timestamp,
                    "payloadDigest": digest, "payload": normalized}
        atomic(directory / f"revision-{number:06d}.json", envelope)
        return {"ok": True, "outcome": outcome_id, "revision": number, "idempotent": False,
                "recordedAt": timestamp, "payloadDigest": digest}
    finally:
        os.close(fd)


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


def _load_member_rows(common, members) -> list:
    rows = []
    for member in members:
        task = member["task"]
        task_dir = task_dir_for(Path(common), task)
        task_meta, task_problem = _read_bounded_json(task_dir / "task.json", MAX_TASK_BYTES)
        for number in member["rounds"]:
            round_dir = task_dir / "rounds" / str(number)
            state, state_problem = _read_bounded_json(round_dir / "round.state.json", MAX_STATE_BYTES)
            summary, summary_problem = _read_bounded_json(round_dir / "round.summary.json",
                                                          MAX_SUMMARY_BYTES)
            rows.append({"task": task, "round": number, "roundDir": round_dir,
                         "state": state if isinstance(state, dict) else None,
                         "stateProblem": state_problem,
                         "summary": summary if isinstance(summary, dict) else None,
                         "summaryProblem": summary_problem,
                         "taskMeta": task_meta if isinstance(task_meta, dict) else None,
                         "taskProblem": task_problem})
    return rows


def _read_archive(common, members) -> tuple:
    """Bounded cursor reads of archived decisions and events; never the card window."""
    board_file = board_file_for_common(Path(common))
    store = board_file.with_name(STORE_FILE)
    decisions, events = [], []
    coverage = {"status": "unknown", "problem": None, "truncated": False,
                "tasksComplete": [], "tasksFailed": []}
    if not members:
        coverage.update(status="not_applicable", problem=None,
                        note="no member tasks: nothing to read from the board archive")
        return decisions, events, coverage
    if not store.is_file():
        coverage["problem"] = "board archive is not initialized; decisions/events are unavailable"
        return decisions, events, coverage
    for member in members:
        task = member["task"]
        task_decisions, task_events = [], []
        ok = True
        # Decisions page by event id; events page by sequence. Each loop is bound.
        cursor, pages = "", 0
        while True:
            try:
                page = decision_page(board_file, task, cursor)
            except (ValueError, OSError, sqlite3.Error) as exc:
                coverage["problem"] = f"decision archive unreadable: {type(exc).__name__}"
                ok = False
                break
            task_decisions.extend(page["decisions"])
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
            task_events.extend(page["events"])
            cursor = page.get("nextCursor")
            pages += 1
            if cursor is None:
                break
            if pages >= MAX_ARCHIVE_PAGES:
                coverage["truncated"] = True
                ok = False
                coverage["problem"] = "event archive exceeded the bounded page count"
                break
        decisions.extend(task_decisions)
        events.extend(task_events)
        (coverage["tasksComplete"] if ok else coverage["tasksFailed"]).append(task)
    if coverage["tasksFailed"]:
        coverage["status"] = "unknown"
    elif coverage["truncated"]:
        coverage["status"] = "partial"
    else:
        coverage["status"] = "known"
    return decisions, events, coverage


def _takeover_latches(common, members) -> tuple:
    """Board latch facts for the selected member tasks; card reads only, bounded."""
    board_file = board_file_for_common(Path(common))
    board, problem = read_board(board_file)
    if not isinstance(board, dict):
        return [], (problem or "board is unreadable")
    cards = board.get("cards")
    if cards is None:
        return [], "board has no cards"
    result = []
    for member in members:
        try:
            card = cards.get(member["task"])
        except (ValueError, OSError):
            return result, "board card scan failed"
        latch = ((card or {}).get("codex") or {}).get("takeover") \
            if isinstance(card, dict) else None
        if isinstance(latch, dict) and latch.get("required"):
            result.append({"task": member["task"], "cause": latch.get("cause"),
                           "at": latch.get("at"), "scope": latch.get("scope"),
                           "failedDeliveries": latch.get("failedDeliveries"),
                           "source": "board_takeover_latch"})
    return result, None


def _leaf_value(section, key):
    if not isinstance(section, dict):
        return None
    leaf = section.get(key)
    if not isinstance(leaf, dict):
        return None
    value = leaf.get("value")
    return value if _finite(value) else None


def _leaf_complete(section, key) -> bool:
    if not isinstance(section, dict):
        return False
    leaf = section.get(key)
    return bool(isinstance(leaf, dict) and leaf.get("complete") is True)


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
    intervals, unknown = [], []
    for entry in work:
        started, ended = entry.get("startedAt"), entry.get("finishedAt")
        if started is not None and ended is not None and ended >= started:
            intervals.append((started, ended))
        else:
            unknown.append(entry["id"])
    return intervals, unknown


def _delivery_section(payload, rows, decisions, archive_coverage):
    rounds, known, missing, unknown = [], [], [], []
    for row in rows:
        key = {"task": row["task"], "round": row["round"]}
        if row["state"] is None:
            rounds.append(key | {"state": "unknown", "sourceProblem": row["stateProblem"]})
            missing.append(f"{key['task']}/{key['round']}")
            continue
        state = row["state"]
        rounds.append(key | {"state": state.get("state"), "exitCode": state.get("exitCode"),
                             "timedOut": bool(state.get("timedOut")),
                             "cancelled": bool(state.get("cancelled")),
                             "endHead": state.get("endHead") or state.get("head"),
                             "startedAt": state.get("startedAt"),
                             "endedAt": state.get("endedAt")})
        known.append(f"{key['task']}/{key['round']}")
    decision_counts, quality_failures = Counter(), []
    first_acceptance = None
    for record in decisions:
        decision = record.get("decision")
        if decision:
            decision_counts[decision] += 1
        if decision in ("rejected", "changes_requested") \
                and record.get("failureKind", "quality") != "external":
            quality_failures.append({"eventId": record.get("eventId"),
                                     "round": record.get("round"),
                                     "failureKind": record.get("failureKind", "quality"),
                                     "at": record.get("at")})
        if decision == "accepted":
            at = record.get("at")
            if first_acceptance is None or (at is not None and (first_acceptance.get("at") is None
                                                                or at < first_acceptance["at"])):
                first_acceptance = {"eventId": record.get("eventId"), "at": at,
                                    "round": record.get("round"),
                                    "reviewedHead": record.get("reviewedHead")}
    seen_failures = []
    for failure in sorted(quality_failures, key=lambda item: (item["at"] or 0, item["eventId"] or "")):
        if failure["eventId"] not in [seen["eventId"] for seen in seen_failures]:
            seen_failures.append(failure)
    return {
        "source": "main_reported_observation_with_native_round_facts",
        "reported": {"source": "main_reported", "status": payload["status"],
                     "candidate": payload["candidate"], "evidenceRefs": payload["evidenceRefs"],
                     "note": "reported observation and references; not verified acceptance, "
                             "ownership or permission"},
        "nativeRounds": rounds,
        "nativeRoundCoverage": _status(known, missing, unknown),
        "nativeDecisions": {"counts": {key: decision_counts[key] for key in sorted(decision_counts)},
                            "firstReviewedAcceptance": first_acceptance,
                            "reviewedQualityFailures": seen_failures,
                            "coverage": dict(archive_coverage,
                                             note="read through archived cursor pages, not the "
                                                  "visible card window; no quality decision means "
                                                  "unknown, never an invented failure")}}


def _timing_section(payload, rows, events, archive_coverage):
    known_created = [row["taskMeta"].get("createdAt") for row in rows
                     if isinstance(row["taskMeta"], dict)]
    known_created = [value for value in known_created if _finite(value) and value >= 0]
    if payload["startedAt"] is not None:
        start = {"value": payload["startedAt"], "source": "reported"}
    elif known_created:
        start = {"value": min(known_created), "source": "earliest_member_task_createdAt",
                 "label": "dispatch scope; excludes prior planning"}
    else:
        start = {"value": None, "source": "unknown"}
    finish = {"value": payload["finishedAt"],
              "source": "reported" if payload["finishedAt"] is not None else "unknown"}
    elapsed = None
    if start["value"] is not None and finish["value"] is not None:
        elapsed = finish["value"] - start["value"]
    intervals, round_known, round_unknown = _round_intervals(rows)
    work_intervals, work_unknown = _work_intervals(payload["work"])
    review_intervals, pending_reviews = [], 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("kind") not in DELIVERY_EVENT_KINDS:
            continue
        created = event.get("createdAt")
        if created is None:
            created = event.get("at")
        handled = event.get("handledAt")
        if event.get("decision") in ("accepted", "rejected", "changes_requested") \
                and _finite(created) and _finite(handled) and handled >= created:
            review_intervals.append((created, handled))
        elif not event.get("handled"):
            pending_reviews += 1
    if archive_coverage.get("status") == "known" and pending_reviews == 0:
        review_coverage = {"status": "known", "intervals": len(review_intervals),
                           "pendingExcluded": 0}
    else:
        review_coverage = {"status": "partial" if review_intervals else "unknown",
                           "intervals": len(review_intervals),
                           "pendingExcluded": pending_reviews}
    sums, sum_samples = Counter(), Counter()
    for row in rows:
        summary = row["summary"]
        timing = ((summary or {}).get("metrics") or {}).get("timing") \
            if isinstance(summary, dict) else None
        for key in SUMMARY_TIMING_KEYS:
            value = _leaf_value(timing, key)
            if value is not None:
                sums[key] += value
                sum_samples[key] += 1
        checks = ((summary or {}).get("metrics") or {}).get("checks") \
            if isinstance(summary, dict) else None
        value = _leaf_value(checks, "elapsedSeconds")
        if value is not None:
            sums["checkElapsedSeconds"] += value
            sum_samples["checkElapsedSeconds"] += 1
    return {
        "source": "mixed_reported_and_native_metadata",
        "start": start,
        "finish": finish,
        "elapsedWallSeconds": elapsed,
        "trueDeliveryLatencySeconds": elapsed,
        "trueDeliveryLatencyKnown": finish["value"] is not None,
        "recordedAt": None,  # filled by the caller
        "roundExecutionUnionSeconds": {
            "value": union_seconds(intervals), "rounds": round_known,
            "coverage": _status(round_known, [], round_unknown),
            "label": "native union of recorded round execution intervals"},
        "externalWorkUnionSeconds": {
            "value": union_seconds(work_intervals), "entries": len(work_intervals),
            "idsWithoutBothBoundaries": work_unknown, "source": "main_reported",
            "label": "union of explicit external work intervals"},
        "reportedWorkSumsSeconds": {key: (sums[key] if sum_samples[key] else None)
                                    for key in SUMMARY_TIMING_KEYS + ("checkElapsedSeconds",)},
        "reviewWaitSeconds": {"value": union_seconds(review_intervals) if review_intervals else None,
                              "coverage": review_coverage,
                              "note": "archived delivery-event createdAt/handledAt union; open "
                                      "reviews are excluded, and missing history stays unknown"},
        "notes": ["never infer active reasoning time from silence or elapsed wall time; "
                  "reported work sums are labelled sums, never added to wall time"]}


def _rework_section(payload, rows, decisions, events, archive_coverage):
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
    failed_receipts = 0
    for row in rows:
        summary = row["summary"]
        checks = ((summary or {}).get("metrics") or {}).get("checks") \
            if isinstance(summary, dict) else None
        if isinstance(checks, dict):
            value = checks.get("failed")
            if isinstance(value, int) and not isinstance(value, bool):
                failed_receipts += value
    transport = Counter()
    transport_rounds, transport_unknown = [], []
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        meta = row["taskMeta"]
        diagnostics = ((meta or {}).get("network") or {}).get("diagnostics") \
            if isinstance(meta, dict) else None
        if diagnostics is not True:
            transport_unknown.append(key)
            continue
        sidecar = read_network_sidecar(row["roundDir"] / "round.network.jsonl")
        if sidecar.get("status") in ("missing", "unreadable"):
            transport_unknown.append(key)
            continue
        transport_rounds.append(key)
        for name, value in (sidecar.get("classes") or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                transport[name] += value
    takeover_events = [{"eventId": event.get("id"), "fingerprint": event.get("fingerprint"),
                        "at": event.get("createdAt") or event.get("at")}
                       for event in events if isinstance(event, dict)
                       and event.get("kind") == TAKEOVER_EVENT_KIND]
    quality_failures = {record.get("eventId") for record in decisions
                        if record.get("decision") in ("rejected", "changes_requested")
                        and record.get("failureKind", "quality") != "external"}
    return {
        "source": "native_rounded_receipts_decisions_network",
        "reviewedQualityFailures": {
            "count": len(quality_failures),
            "coverage": dict(archive_coverage, note="distinct archived quality decisions")},
        "failedCheckAttempts": {"value": failed_receipts, "roundSummaries": failed_receipts,
                                 "source": "stored round summary counters"},
        "failedRounds": failed_rounds,
        "unknownRounds": unknown_rounds,
        "transportErrors": {"classes": {key: transport[key] for key in sorted(transport)},
                            "roundsWithReadableSidecar": transport_rounds,
                            "roundsUnknown": transport_unknown,
                            "coverage": _status(transport_rounds, [], transport_unknown)},
        "nativeTakeover": {"observed": bool(takeover_events),
                           "events": takeover_events,
                           "note": "archived takeover events; an explicit main takeover latch is "
                                   "reported separately in intervention coverage"}}


def _verification_section(common, payload, rows):
    root = state_root(Path(common)).resolve()
    unique, sources = {}, Counter()
    round_known, round_missing, round_unknown = [], [], []
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
                continue
            path = receipt.get("receipt")
            if not isinstance(path, str):
                continue
            try:
                resolved = Path(path).resolve()
            except OSError:
                continue
            entry = {"source": "round_summary", "id": receipt.get("id"),
                     "argv": receipt.get("argv") if isinstance(receipt.get("argv"), list) else None,
                     "argvComplete": all(isinstance(arg, str) for arg in receipt.get("argv") or []),
                     "head": receipt.get("head"), "exitCode": receipt.get("exit_code"),
                     "timedOut": bool(receipt.get("timed_out")),
                     "cancelled": bool(receipt.get("cancelled")),
                     "startedAt": receipt.get("started_at"), "endedAt": receipt.get("ended_at"),
                     "logVerified": receipt.get("log_verified"),
                     "testCounts": receipt.get("test_counts"),
                     "receiptExists": resolved.is_file()}
            if str(resolved) not in unique:
                unique[str(resolved)] = entry
                sources["round_summary"] += 1
    extra_unknown = []
    for entry in payload["extraChecks"]:
        resolved = (root / entry["path"]).resolve()
        key = str(resolved)
        problem = None
        if not inside(resolved, root) or not resolved.is_file():
            problem = "missing"
        else:
            try:
                digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except OSError:
                digest = None
            if digest != entry["sha256"]:
                problem = "metadata mismatch"
        record = {"source": "extra_check", "id": entry["id"], "argv": entry["argv"],
                  "argvComplete": entry["argvComplete"], "head": entry["head"],
                  "exitCode": entry["exitCode"], "timedOut": entry["timedOut"],
                  "cancelled": entry["cancelled"], "startedAt": entry["startedAt"],
                  "endedAt": entry["endedAt"], "logVerified": None,
                  "testCounts": entry["testCounts"], "receiptExists": problem is None,
                  "problem": problem}
        if problem is not None:
            extra_unknown.append({"path": entry["path"], "problem": problem})
            record["unknown"] = True
        if key in unique:
            sources["shared"] += 1
            continue
        unique[key] = record
        sources["extra_check"] += 1
    counts = Counter()
    elapsed, elapsed_missing, intervals = 0.0, 0, []
    clean_groups = {}
    for key, record in sorted(unique.items()):
        counts["attempts"] += 1
        if record.get("unknown"):
            counts["unknown"] += 1
            continue
        if record.get("timedOut") or record.get("cancelled"):
            counts["interrupted"] += 1
            continue
        code = record.get("exitCode")
        if code is None:
            counts["unknown"] += 1
            continue
        counts["completed"] += 1
        counts["passed" if code == 0 else "failed"] += 1
        started, ended = record.get("startedAt"), record.get("endedAt")
        if _finite(started) and _finite(ended) and ended >= started:
            elapsed += ended - started
            intervals.append((started, ended))
        else:
            elapsed_missing += 1
        if code == 0 and isinstance(record.get("head"), str) \
                and FULL_OID_RE.fullmatch(record["head"]) and record.get("argvComplete") \
                and isinstance(record.get("argv"), list):
            group_key = (record["head"], tuple(record["argv"]))
            clean_groups.setdefault(group_key, []).append(key)
    repeats = []
    for (head, argv), keys in sorted(clean_groups.items()):
        if len(keys) < 2:
            continue
        repeats.append({"head": head, "argv": list(argv), "count": len(keys),
                        "receipts": keys[:MAX_POTENTIAL_REPEAT_SOURCES],
                        "classification": "potential_repeat",
                        "note": "identical recorded clean head and argv; a potential repeat, "
                                "never evidence of waste by itself"})
    return {
        "source": "stored_round_receipts_and_main_reported_extra_checks",
        "attempts": counts["attempts"], "completed": counts["completed"],
        "passed": counts["passed"], "failed": counts["failed"],
        "interrupted": counts["interrupted"], "unknown": counts["unknown"],
        "uniqueReceipts": len(unique),
        "deduplicatedSharedReceipts": sources["shared"],
        "sources": {"roundSummaryReceipts": sources["round_summary"],
                    "extraCheckReceipts": sources["extra_check"]},
        "elapsedSeconds": {"value": elapsed if counts["completed"] else None,
                           "complete": counts["unknown"] == 0 and elapsed_missing == 0,
                           "samples": counts["completed"], "missingTime": elapsed_missing,
                           "label": "sum of completed receipt intervals"},
        "wallCoverageSeconds": {"value": union_seconds(intervals) if intervals else None,
                                "label": "union of recorded receipt intervals; checks are inside "
                                         "tool coverage"},
        "potentialRepeats": repeats,
        "coverage": _status(round_known, round_missing, round_unknown)
                    | {"extraChecksUnknown": extra_unknown,
                       "note": "recorded-check coverage only; uninstrumented checks remain outside, "
                               "and references are never a new execution"}}


def _usage_section(payload, rows):
    pi_known, pi_rounds = {key: 0.0 for key in SUMMARY_USAGE_KEYS}, {key: [] for key in SUMMARY_USAGE_KEYS}
    pi_incomplete = {key: [] for key in SUMMARY_USAGE_KEYS}
    pi_usage_unknown, summary_missing = [], []
    cost, cost_rounds, cost_unknown = 0.0, [], []
    models, expected = Counter(), set()
    for row in rows:
        key = f"{row['task']}/{row['round']}"
        summary = row["summary"]
        if summary is None or "metrics" not in summary:
            summary_missing.append(key)
            cost_unknown.append(key)
            for metric in SUMMARY_USAGE_KEYS:
                pi_incomplete[metric].append(key)
            continue
        metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
        usage = metrics.get("usage")
        for metric in SUMMARY_USAGE_KEYS:
            value = _leaf_value(usage, metric)
            if value is None:
                pi_incomplete[metric].append(key)
            else:
                pi_known[metric] += value
                pi_rounds[metric].append(key)
                if not _leaf_complete(usage, metric):
                    pi_incomplete[metric].append(key)
        value = summary.get("reported_cost_usd")
        if _finite(value) and value >= 0:
            cost += value
            cost_rounds.append(key)
        else:
            cost_unknown.append(key)
        if isinstance(summary.get("models"), dict):
            models.update(summary["models"])
        if isinstance(summary.get("expected_model"), str):
            expected.add(summary["expected_model"])
    if not rows:
        pi_report = {"applicable": False, "usage": None, "reasoningTokens": None,
                     "costUsd": None, "rounds": 0,
                     "note": "no Pi member rounds were reported for this outcome"}
        identity = {"recorded": {}, "expectedReported": [], "status": "not_applicable"}
    else:
        rounds_total = len(rows)
        pi_report = {
            "applicable": True,
            "usage": {metric: {"known": pi_known[metric] if pi_rounds[metric] else None,
                                "roundsKnown": pi_rounds[metric],
                                "roundsIncomplete": pi_incomplete[metric],
                                "complete": len(pi_rounds[metric]) == rounds_total
                                            and not pi_incomplete[metric]}
                       for metric in SUMMARY_USAGE_KEYS},
            "costUsd": {"known": cost if cost_rounds else None,
                        "complete": len(cost_rounds) == rounds_total,
                        "roundsKnown": cost_rounds, "roundsUnknown": cost_unknown,
                        "scope": "pi_assistant", "source": "provider_reported"},
            "roundsMissingSummaries": summary_missing,
            "note": "reasoning is already included in output and is never added again; an absent "
                    "field stays null and is never a fabricated zero"}
        if not models:
            identity = {"recorded": {}, "expectedReported": sorted(expected), "status": "unknown"}
        elif len(models) == 1 and expected == set(models):
            identity = {"recorded": dict(models), "expectedReported": sorted(expected),
                        "status": "matched"}
        elif len(models) > 1:
            identity = {"recorded": dict(models), "expectedReported": sorted(expected),
                        "status": "mixed",
                        "note": "mixed model identities are never attributed to one configured model"}
        else:
            identity = {"recorded": dict(models), "expectedReported": sorted(expected),
                        "status": "mismatched",
                        "note": "recorded model identity differs from the frozen expected model"}
    ext_sums = {key: 0.0 for key in USAGE_KEYS}
    ext_counts = {key: 0 for key in USAGE_KEYS}
    ext_cost, ext_cost_entries, quantities = 0.0, 0, []
    for entry in payload["work"]:
        supplies = False
        for key in USAGE_KEYS:
            value = (entry.get("usage") or {}).get(key)
            if value is not None:
                ext_sums[key] += value
                ext_counts[key] += 1
                supplies = True
        cost_value = entry.get("reportedCostUsd")
        if cost_value is not None:
            ext_cost += cost_value
            ext_cost_entries += 1
            supplies = True
        if supplies:
            quantities.append(entry)
    external_complete = bool(payload["workComplete"]) and all(
        entry.get("reportedCostUsd") is not None for entry in quantities)
    external = {"declaredComplete": bool(payload["workComplete"]),
                "workEntries": len(payload["work"]),
                "usage": {key: (ext_sums[key] if ext_counts[key] else None) for key in USAGE_KEYS},
                "usageEntries": {key: ext_counts[key] for key in USAGE_KEYS},
                "reportedCostUsd": ext_cost if ext_cost_entries else None,
                "costEntries": ext_cost_entries,
                "complete": external_complete,
                "source": "main_reported_work",
                "note": "externally reported work only; absence of a work record is not proof "
                        "that no external work happened"}
    combined = None
    reason = None
    if pi_report["applicable"] and not pi_report["costUsd"]["complete"]:
        reason = "Pi round cost coverage is incomplete"
    elif not payload["workComplete"]:
        reason = "external cost completeness was not declared (workComplete is false)"
    elif not external_complete:
        reason = "a declared external work entry supplies quantities without a reported cost"
    else:
        combined = (pi_report["costUsd"]["known"] or 0.0) + (external["reportedCostUsd"] or 0.0)
    return {
        "source": "stored_round_summaries_and_main_reported_work",
        "modelIdentity": identity,
        "piReported": pi_report,
        "externalReported": external,
        "combinedReportedCostUsd": {"value": combined, "reason": reason,
                                     "note": "combined only from complete Pi round cost coverage "
                                             "plus a declared-complete external report"},
        "billing": {"verified": False, "costUsd": None,
                    "note": "interface/SDK reported cost is not a provider bill; billing is "
                            "never verified here"},
        "notes": ["reasoning tokens are already inside output; they are reported separately and "
                  "never added to output a second time",
                  "unknown numeric fields stay null; a missing value is never zero"]}


def _intervention_section(payload, rows, takeover_latches, takeover_events, archive_coverage):
    kinds = Counter(entry["kind"] for entry in payload["work"])
    latches = list(takeover_latches)
    for event in takeover_events:
        latches.append({"task": None, "cause": "quality_limit",
                        "at": event.get("at"), "source": "archived_takeover_event"})
    observed = bool(latches)
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
        "nativeTakeover": {"observed": observed, "evidence": latches,
                           "coverage": dict(archive_coverage,
                                            note="board latch plus archived takeover events")},
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
    rows = _load_member_rows(common, members)
    decisions, events, archive_coverage = _read_archive(common, members)
    latches, latch_problem = _takeover_latches(common, members)
    if latch_problem:
        archive_coverage = dict(archive_coverage, status="unknown" if members else "not_applicable",
                                problem=latch_problem)
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
        "delivery": _delivery_section(payload, rows, decisions, archive_coverage),
        "timing": timing,
        "reworkIncidents": _rework_section(payload, rows, decisions, events, archive_coverage),
        "verification": _verification_section(common, payload, rows),
        "usageCost": _usage_section(payload, rows),
        "interventionCoverage": _intervention_section(payload, rows, latches, events,
                                                       archive_coverage),
        "acceptance": "not_verified",
        "note": "Main-reported closeout observation over recorded metadata; never a phase "
                "acceptance, ownership decision or permission to execute",
    }


def _member_source_flags(common, members) -> dict:
    present, missing = [], []
    for member in members:
        task = member.get("task")
        task_dir = task_dir_for(Path(common), task) if isinstance(task, str) else None
        for number in member.get("rounds") or []:
            key = f"{task}/{number}"
            if task_dir is not None and (task_dir / "task.json").is_file() \
                    and (task_dir / "rounds" / str(number) / "round.state.json").is_file():
                present.append(key)
            else:
                missing.append(key)
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
    root = state_root(Path(common)) / "outcomes"
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
