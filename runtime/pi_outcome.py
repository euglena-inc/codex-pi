#!/usr/bin/env python3
"""Source-backed, main-reported outcome observations for the metrics CLI (0.8.5).

Storage is a bounded sequence of immutable JSON revisions under the private
git-common ``codex-pi/outcomes/<ID>/`` directory. A closeout is a *reported
observation*: it never proves acceptance, never owns execution and never grants
permission. Recording and projection only read existing bounded metadata
(selected member task/round state, stored round summaries, archived
decisions/events, standard check receipts and network sidecars). This module
never invokes a model, never executes a task, never mutates
board/execution/budget/quality state and never scans a raw session or raw check
log for statistics.

Repair rules this module implements as one coherent contract:

* The explicitly selected member ``(task, round)`` set is the only attribution
  boundary. Archive decisions/events are read through cursor pages per task,
  then immediately retained with their task identity and filtered by the
  selected rounds. Unselected or unattributable acceptances, failures and
  takeovers never enter the outcome.
* All three consumers (record/query/listing) share one trusted-root rule:
  every component from the resolved git-common directory down to a member
  metadata file or an extra check is validated for symlink and escape before
  anything is read or written, and every read is bounded by actual bytes.
* Stored round summaries are consumed with their existing assistant,
  auxiliary and total/completeness facts; a partial or older summary never
  becomes a complete total, and auxiliary usage/cost is added exactly once.
* Check receipts are normalized once (production summaries usually store a
  bare filename, resolved against the owning round's checks directory), then
  deduplicated, measured and grouped. Missing, contradictory or unverified
  copies degrade completeness instead of being silently dropped.
* Time and coverage are never guessed: an absent or inconsistent boundary
  stays unknown with a reason, interval unions are never added to wall time,
  and recordedAt never replaces the actual acceptance time.

Dependency direction: this is a lower-level helper. It imports storage/core
modules only and must never import an entry module (``pi_task``/``pi_board``/
``pi_handoff``).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from pi_core import (
    TASK_RE,
    atomic,
    git,
    inside,
    lock_fd,
)
from pi_store import FULL_OID_RE, _read_bounded_json

SCHEMA_VERSION = 1
OUTCOME_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
WORK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
CHECK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
REVISION_RE = re.compile(r"revision-(\d{6})\.json\Z")

STATUSES = ("accepted", "partial", "failed", "cancelled", "unknown")
WORK_KINDS = ("design", "review", "debug", "supplemental_checks", "takeover", "other")
USAGE_KEYS = ("input", "cacheRead", "cacheWrite", "output", "totalTokens")
ASSISTANT_METRIC_KEYS = ("uncachedInput", "cacheRead", "cacheWrite", "output", "totalTokens")
AUXILIARY_METRIC_KEYS = ("uncachedInput", "cacheRead", "cacheWrite", "output", "totalTokens",
                         "reportedCostUsd")
SUMMARY_TIMING_KEYS = ("windowSeconds", "modelResponseSeconds", "toolSeconds",
                       "unattributedSeconds")
DELIVERY_EVENT_KINDS = ("review_required", "phase_blocked")
DELIVERY_DECISIONS = ("accepted", "rejected", "changes_requested")
TAKEOVER_EVENT_KIND = "codex_takeover_required"

# Bounded input: nothing here is allowed to grow with transcript size.
MAX_RECORD_BYTES = 1_000_000
MAX_REVISION_BYTES = 2_000_000
MAX_REVISIONS = 64
MAX_HISTORY_BYTES = 32_000_000
MAX_MEMBER_SCAN_BYTES = 64_000_000
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
MAX_PROBLEM_DETAILS = 20

WORK_FIELDS = frozenset(("id", "kind", "actor", "model", "startedAt", "finishedAt",
                         "usage", "reportedCostUsd", "evidenceRef"))
PAYLOAD_FIELDS = frozenset(("members", "status", "candidate", "evidenceRefs", "startedAt",
                            "finishedAt", "work", "workComplete", "extraChecks"))
RECEIPT_FIELDS = ("id", "exitCode", "timedOut", "cancelled", "startedAt", "endedAt", "head",
                  "dirty", "argv", "argvComplete", "testCounts")


# ---------------------------------------------------------------------------
# small validators
# ---------------------------------------------------------------------------

def _finite(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _time_value(value, name):
    """A finite non-negative Unix timestamp; ``None`` stays an explicit absence."""
    if value is None:
        return None
    if not _finite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Unix timestamp")
    return value


def _token_count(value, name):
    if not _finite(value) \
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


def _same_value(left, right) -> bool:
    return left == right


def payload_digest(payload) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rendered_envelope_size(envelope) -> int:
    """The exact bytes ``atomic`` writes, measured before any publish."""
    return len((json.dumps(envelope, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


# ---------------------------------------------------------------------------
# trusted roots and safe bounded reads
# ---------------------------------------------------------------------------

def _codex_root(common) -> Path:
    """Resolved private ``<git-common>/codex-pi`` root; no symlink may escape."""
    base = Path(common).resolve()
    if not base.is_dir():
        raise ValueError("git-common directory does not exist")
    state = base / "codex-pi"
    if state.is_symlink():
        raise ValueError("the codex-pi state directory must not be a symlink")
    resolved = state.resolve()
    if resolved != state or not inside(resolved, base):
        raise ValueError("the codex-pi state directory escapes the git-common directory")
    return resolved


def _outcomes_root(common) -> Path:
    state = _codex_root(common)
    outcomes = state / "outcomes"
    if outcomes.is_symlink():
        raise ValueError("the outcomes directory must not be a symlink")
    resolved = outcomes.resolve()
    if not inside(resolved, state):
        raise ValueError("the outcomes directory escapes the private git-common state root")
    return resolved


def outcome_path(common, outcome_id) -> Path:
    """Lexical bounded outcome directory inside the verified outcomes root."""
    root = _outcomes_root(common)
    target = root / validate_outcome_id(outcome_id)
    return _require_under(target, root, "outcome directory")


def _require_under(path, root, label) -> Path:
    """Write-path resolution: reject symlinks and escapes from the trusted root."""
    resolved, problem = _inspect_under(path, root)
    if problem:
        raise ValueError(f"{label} {problem}")
    return resolved


def _inspect_under(path, root):
    """Read-path resolution: never follow a symlink or escape; return a problem."""
    candidate, root = Path(path), Path(root)
    try:
        relative = candidate.relative_to(root)
        if ".." in relative.parts:
            return None, "escapes the private git-common state root"
        current = root
        for part in ("", *relative.parts):
            current = current / part
            if current.is_symlink():
                return None, "is a symlink or has a symlink parent"
        resolved = candidate.resolve()
    except ValueError:
        return None, "escapes the private git-common state root"
    except (OSError, RuntimeError):
        return None, "is unresolvable"
    if not inside(resolved, root):
        return None, "escapes the private git-common state root"
    return resolved, None


def _read_bounded_bytes(path, limit: int):
    """Actual byte ceiling before parse: never read a whole oversized file."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > limit:
        return None, "oversized"
    return raw, None


def load_record_payload(path) -> dict:
    """Read one bounded JSON record file; refusal happens before full parse."""
    raw, problem = _read_bounded_bytes(path, MAX_RECORD_BYTES)
    if problem is not None:
        raise ValueError(f"record file is {problem}: {path}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("record file is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("outcome record must be a JSON object")
    return payload


def _member_task_identity(common, task):
    """Write-time member task identity: same repo, real metadata, no symlink."""
    state = _codex_root(common)
    tasks_root = state / "tasks"
    if tasks_root.is_symlink():
        raise ValueError("the tasks directory must not be a symlink")
    task_dir = _require_under(tasks_root / task, state, f"member task {task!r} directory")
    if not task_dir.is_dir():
        raise ValueError(f"member task {task!r} is missing")
    task_json = _require_under(task_dir / "task.json", state, f"member task {task!r} metadata")
    data, problem = _read_bounded_json(task_json, MAX_TASK_BYTES)
    if problem is not None or not isinstance(data, dict):
        raise ValueError(f"member task {task!r} metadata is {problem or 'invalid'}")
    declared = data.get("task")
    if declared is not None and declared != task:
        raise ValueError(f"member task {task!r} metadata names a different task")
    declared_common = data.get("commonDir")
    if declared_common is not None:
        try:
            declared_path = Path(declared_common).resolve()
        except (OSError, TypeError, ValueError, RuntimeError):
            declared_path = None
        if declared_path != Path(common).resolve():
            raise ValueError(f"member task {task!r} belongs to a different git-common directory")
    created = data.get("createdAt")
    created = created if (_finite(created) and created >= 0) else None
    return task_dir, created


def _member_round_identity(common, task_dir, number):
    state = _codex_root(common)
    round_dir = _require_under(task_dir / "rounds" / str(number), state,
                               f"member round {task_dir.name}/{number}")
    if not round_dir.is_dir():
        raise ValueError(f"member round {task_dir.name}/{number} is missing")
    state_path = _require_under(round_dir / "round.state.json", state,
                                f"member round {task_dir.name}/{number} state")
    if not state_path.is_file():
        raise ValueError(f"member round {task_dir.name}/{number} state is missing")
    summary_path = round_dir / "round.summary.json"
    if summary_path.is_symlink():
        raise ValueError(f"member round {task_dir.name}/{number} summary must not be a symlink")
    return round_dir


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
    earliest_created = None
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
        task_dir, created = _member_task_identity(common, task)
        for number in numbers:
            _member_round_identity(common, task_dir, number)
        if created is not None:
            earliest_created = created if earliest_created is None else min(earliest_created,
                                                                             created)
        result.append({"task": task, "rounds": sorted(numbers)})
    result.sort(key=lambda member: member["task"])
    return result, earliest_created


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
    if not isinstance(data.get("log_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", data["log_sha256"]):
        raise ValueError(f"extra check {rel!r} has no pinned log digest")
    timed_out = data.get("timed_out")
    cancelled = data.get("cancelled")
    if not isinstance(timed_out, bool) or not isinstance(cancelled, bool):
        raise ValueError(f"extra check {rel!r} has invalid interruption flags")
    dirty = data.get("dirty")
    if dirty is not None and not isinstance(dirty, bool):
        raise ValueError(f"extra check {rel!r} has an invalid dirty flag")
    counts = data.get("test_counts")
    if counts is not None and (not isinstance(counts, dict) or any(
            not isinstance(v, int) or isinstance(v, bool) or v < 0
            for k, v in counts.items() if k != "format")):
        raise ValueError(f"extra check {rel!r} has invalid test counts")
    test_counts = {key: value for key, value in counts.items()
                   if isinstance(value, int) and not isinstance(value, bool)} \
        if isinstance(counts, dict) else None
    argv_out = [arg[:MAX_ARGV_CHARS] for arg in argv[:MAX_ARGV_ITEMS]]
    argv_complete = len(argv) <= MAX_ARGV_ITEMS and all(len(arg) <= MAX_ARGV_CHARS for arg in argv)
    head = data.get("head")
    return {"path": rel, "sha256": digest, "id": receipt_id, "exitCode": exit_code,
            "startedAt": started, "endedAt": ended, "timedOut": timed_out,
            "cancelled": cancelled,
            "head": head if isinstance(head, str) and FULL_OID_RE.fullmatch(head) else None,
            "dirty": dirty, "argv": argv_out, "argvComplete": argv_complete,
            "testCounts": test_counts}


def _normalize_extra_checks(raw, common):
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("extraChecks must be a list of receipt paths")
    if len(raw) > MAX_EXTRA_CHECKS:
        raise ValueError(f"extraChecks exceeds {MAX_EXTRA_CHECKS} entries")
    root = _codex_root(common)
    entries = {}
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("each extraChecks entry must be a non-empty relative path")
        if len(value) > MAX_PATH_CHARS:
            raise ValueError(f"extraChecks path exceeds {MAX_PATH_CHARS} characters")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe extraChecks path {value!r}")
        resolved = _require_under(root / relative, root, f"extraChecks entry {value!r}")
        if not resolved.is_file():
            raise ValueError(f"extraChecks receipt does not exist: {value!r}")
        raw_bytes, problem = _read_bounded_bytes(resolved, MAX_RECEIPT_BYTES)
        if problem is not None:
            raise ValueError(f"extraChecks receipt is {problem}: {value!r}")
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
    members, earliest_created = _normalize_members(payload.get("members"), common)
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
    if started is None and finished is not None and earliest_created is not None \
            and finished < earliest_created:
        raise ValueError("finishedAt precedes the earliest selected member task creation time")
    normalized = {"members": members, "status": status, "candidate": candidate,
                  "evidenceRefs": _normalize_evidence_refs(payload.get("evidenceRefs"), status),
                  "startedAt": started, "finishedAt": finished,
                  "work": _normalize_work(payload.get("work")),
                  "workComplete": _bool_value(payload.get("workComplete"), "workComplete") is True,
                  "extraChecks": _normalize_extra_checks(payload.get("extraChecks"), common)}
    return normalized, earliest_created


# ---------------------------------------------------------------------------
# storage: validated immutable numbered revisions
# ---------------------------------------------------------------------------

def _validate_stored_payload(payload):
    """Structural validation of a stored revision, independent of live sources."""
    if not isinstance(payload, dict) or set(payload) - PAYLOAD_FIELDS:
        raise ValueError("payload has an invalid shape")
    members = payload.get("members")
    if not isinstance(members, list) or len(members) > MAX_MEMBERS:
        raise ValueError("payload members are invalid")
    total_rounds = 0
    seen_rounds = set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {"task", "rounds"}:
            raise ValueError("payload member is invalid")
        if not isinstance(member.get("task"), str) or not TASK_RE.fullmatch(member["task"]):
            raise ValueError("payload member task is invalid")
        rounds = member.get("rounds")
        if not isinstance(rounds, list) or not rounds:
            raise ValueError("payload member rounds are invalid")
        for number in rounds:
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise ValueError("payload member round is invalid")
            pair = (member["task"], number)
            if pair in seen_rounds:
                raise ValueError("payload has duplicate member rounds")
            seen_rounds.add(pair)
        total_rounds += len(rounds)
    if total_rounds > MAX_MEMBER_ROUNDS:
        raise ValueError("payload member rounds exceed the bounded total")
    if payload.get("status") not in STATUSES:
        raise ValueError("payload status is invalid")
    candidate = payload.get("candidate")
    if candidate is not None and (not isinstance(candidate, str)
                                  or not FULL_OID_RE.fullmatch(candidate)):
        raise ValueError("payload candidate is invalid")
    refs = payload.get("evidenceRefs")
    if not isinstance(refs, list) or len(refs) > MAX_EVIDENCE_REFS \
            or any(not isinstance(ref, str) or not ref or len(ref) > MAX_REF_CHARS for ref in refs):
        raise ValueError("payload evidenceRefs are invalid")
    if payload.get("status") == "accepted" and (not refs or candidate is None):
        raise ValueError("accepted payload needs a candidate and evidenceRefs")
    for key in ("startedAt", "finishedAt"):
        value = payload.get(key)
        if value is not None and (not _finite(value) or value < 0):
            raise ValueError(f"payload {key} is invalid")
    if payload.get("startedAt") is not None and payload.get("finishedAt") is not None \
            and payload["startedAt"] > payload["finishedAt"]:
        raise ValueError("payload timestamps are inconsistent")
    work = payload.get("work")
    if not isinstance(work, list) or len(work) > MAX_WORK:
        raise ValueError("payload work is invalid")
    for entry in work:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) \
                or entry.get("kind") not in WORK_KINDS \
                or set(entry) - {"id", "kind", "actor", "model", "startedAt", "finishedAt",
                                 "usage", "reportedCostUsd", "evidenceRef"}:
            raise ValueError("payload work entry is invalid")
        usage = entry.get("usage")
        if not isinstance(usage, dict) or set(usage) - set(USAGE_KEYS):
            raise ValueError("payload work usage is invalid")
        for key, value in usage.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or value < 0):
                raise ValueError("payload work usage value is invalid")
        cost = entry.get("reportedCostUsd")
        if cost is not None and (not _finite(cost) or cost < 0):
            raise ValueError("payload work cost is invalid")
    if _normalize_work(work) != work:
        raise ValueError("payload work is not normalized")
    if not isinstance(payload.get("workComplete"), bool):
        raise ValueError("payload workComplete is invalid")
    extra = payload.get("extraChecks")
    if not isinstance(extra, list) or len(extra) > MAX_EXTRA_CHECKS:
        raise ValueError("payload extraChecks are invalid")
    for entry in extra:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) \
                or not isinstance(entry.get("sha256"), str) \
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("payload extraChecks entry is invalid")
        if not isinstance(entry.get("id"), str) or not CHECK_ID_RE.fullmatch(entry["id"]):
            raise ValueError("payload extraChecks id is invalid")
        if entry.get("exitCode") is not None and (isinstance(entry["exitCode"], bool)
                                                  or not isinstance(entry["exitCode"], int)):
            raise ValueError("payload extraChecks exit code is invalid")
        for key in ("startedAt", "endedAt"):
            value = entry.get(key)
            if not _finite(value) or value < 0:
                raise ValueError("payload extraChecks timestamps are invalid")
        if entry.get("endedAt") < entry.get("startedAt"):
            raise ValueError("payload extraChecks timestamps are inconsistent")
        if not isinstance(entry.get("timedOut"), bool) or not isinstance(entry.get("cancelled"), bool):
            raise ValueError("payload extraChecks flags are invalid")
        if entry.get("dirty") is not None and not isinstance(entry["dirty"], bool):
            raise ValueError("payload extraChecks dirty flag is invalid")
        argv = entry.get("argv")
        if not isinstance(argv, list) or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("payload extraChecks argv is invalid")
        if not isinstance(entry.get("argvComplete"), bool):
            raise ValueError("payload extraChecks argvComplete is invalid")
        head = entry.get("head")
        if head is not None and (not isinstance(head, str) or not FULL_OID_RE.fullmatch(head)):
            raise ValueError("payload extraChecks head is invalid")
        counts = entry.get("testCounts")
        if counts is not None and (not isinstance(counts, dict)
                                   or any(isinstance(v, bool) or not isinstance(v, int) or v < 0
                                          for v in counts.values())):
            raise ValueError("payload extraChecks testCounts are invalid")


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
    _validate_stored_payload(payload)
    return {"schemaVersion": SCHEMA_VERSION, "revision": revision, "recordedAt": recorded,
            "payloadDigest": digest, "payload": payload}


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


def _load_history(directory: Path) -> list:
    """All valid revisions in order; missing/gapped/corrupt history raises."""
    found = _revision_files(directory)
    if not found:
        return []
    numbers = sorted(found)
    if numbers != list(range(1, numbers[-1] + 1)):
        raise ValueError("history has a gap or an ambiguous latest revision")
    if len(numbers) > MAX_REVISIONS:
        raise ValueError(f"history exceeds the bounded revision count ({MAX_REVISIONS})")
    total_bytes, history = 0, []
    for number in numbers:
        try:
            total_bytes += found[number].stat().st_size
        except OSError as exc:
            raise ValueError(f"revision {number} is unreadable") from exc
        if total_bytes > MAX_HISTORY_BYTES:
            raise ValueError("history exceeds the bounded cumulative size")
        data, problem = _read_bounded_json(found[number], MAX_REVISION_BYTES)
        if problem is not None:
            raise ValueError(f"revision {number} is {problem}")
        try:
            history.append(_validate_envelope(data, number))
        except ValueError as exc:
            raise ValueError(f"revision {number}: {exc}") from None
    return history


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
    conflicting update fails without changing any file. A normalized envelope
    larger than the reader's bounded revision limit is refused before publish,
    so no successful write can be unreadable by this module.
    """
    root, common = Path(root), Path(common)
    outcome_id = validate_outcome_id(outcome_id)
    if expected_revision is not None:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
                or expected_revision < 0:
            raise ValueError("expected revision must be a non-negative integer")
    normalized, _earliest = _normalize_payload(payload, common, root)
    digest = payload_digest(normalized)
    timestamp = time.time() if now is None else float(now)
    if not _finite(timestamp) or timestamp < 0:
        raise ValueError("recordedAt must be a finite non-negative Unix timestamp")
    directory = outcome_path(common, outcome_id)
    lock_path = _require_under(directory / "write.lock", _codex_root(common), "outcome lock")
    fd = lock_fd(lock_path, blocking=True, timeout=30)
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
        if number > MAX_REVISIONS:
            raise ValueError(f"history exceeds the bounded revision count ({MAX_REVISIONS})")
        envelope = {"schemaVersion": SCHEMA_VERSION, "revision": number, "recordedAt": timestamp,
                    "payloadDigest": digest, "payload": normalized}
        size = _rendered_envelope_size(envelope)
        if size > MAX_REVISION_BYTES:
            raise ValueError(f"normalized outcome revision is {size} bytes and exceeds the "
                             f"{MAX_REVISION_BYTES}-byte bounded revision limit; reduce work, "
                             "argv or extraChecks before recording")
        history_bytes = sum(path.stat().st_size for path in _revision_files(directory).values())
        if history_bytes + size > MAX_HISTORY_BYTES:
            raise ValueError("history exceeds the bounded cumulative size")
        if directory.exists() and directory.is_symlink():
            raise ValueError("outcome directory must not be a symlink")
        atomic(directory / f"revision-{number:06d}.json", envelope)
        return {"ok": True, "outcome": outcome_id, "revision": number, "idempotent": False,
                "recordedAt": timestamp, "payloadDigest": digest}
    finally:
        os.close(fd)
