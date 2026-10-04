#!/usr/bin/env python3
"""Bounded Pi evidence summary. Process completion is never acceptance PASS.

No Codex CLI is invoked. Reasoning tokens are never added on top of provider totals.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

# The frozen helper snapshot is immutable evidence: never write bytecode caches
# into the task tools directory.
sys.dont_write_bytecode = True

from pi_size import sanitize_snapshot, sha256_file
from pi_execution import terminal_evidence

RISKY = [
    (r"\bgit\s+push\b", "git push"), (r"\bgit\s+stash\b", "git stash"),
    (r"\bgit\s+add\s+(-A|--all|\.)(\s|$)", "git add -A / ."),
    (r"--no-verify\b", "--no-verify"), (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+(checkout|switch)\s+(-b\s+)?(main|master|bake-[\w-]+)\b", "switch branch"),
    (r"\brm\s+-rf?\s+/(?!\S*build/)", "absolute rm"),
    (r"\bPLAN\.md\b", "mentions PLAN.md (inspect read versus write)"),
    (r"\bdocs/plan/", "frozen docs/plan"),
]
USAGE_KEYS = ("input", "cacheRead", "cacheWrite", "output", "totalTokens")
# Session entries outside assistant messages that can carry their own provider usage.
# ``usage`` is Pi's arbitrary model-attributed usage entry (for example cache_warm),
# ``compaction`` and ``branch_summary`` store the usage of the summary LLM call.
AUXILIARY_ENTRY_TYPES = ("compaction", "branch_summary", "usage")
AUXILIARY_LEAF_NAMES = {"input": "uncachedInput", "cacheRead": "cacheRead",
                        "cacheWrite": "cacheWrite", "output": "output",
                        "totalTokens": "totalTokens"}

# Session-derived timing is an estimate from persisted write timestamps, never
# an acceptance signal. The caller passes the explicit session source and the
# round window; nothing here guesses a directory or scans another task.
SESSION_ENTRY_SLACK_SECONDS = 1.0
MAX_SESSION_LINE_BYTES = 8 * 1024 * 1024
MAX_SESSION_HEADER_BYTES = 64 * 1024
SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
CACHE_RATIO_FORMULA = "cacheRead / (cacheRead + uncachedInput)"


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _epoch(value):
    """ISO-8601 session entry timestamp to unix seconds; None when unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _leaf(value, label, **extra):
    """One labelled metric: exact, estimated or unknown; null is never zero."""
    result = {"value": value, "label": label}
    result.update(extra)
    return result


def union_seconds(intervals):
    """Union length of [start, end] intervals; overlap is counted once."""
    ordered = sorted((float(start), float(end)) for start, end in intervals
                     if float(end) >= float(start))
    if not ordered:
        return 0.0
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start


def _session_header_id(path):
    """Header identity of one candidate session file; (id, problem)."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.readline(MAX_SESSION_HEADER_BYTES)
    except OSError:
        return None, "session_header_unreadable"
    if not raw:
        return None, "session_header_missing"
    if len(raw) >= MAX_SESSION_HEADER_BYTES and not raw.endswith(b"\n"):
        return None, "session_header_overlong"
    try:
        header = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "session_header_invalid"
    if not isinstance(header, dict) or header.get("type") != "session":
        return None, "session_header_missing"
    header_id = header.get("id")
    if not isinstance(header_id, str) or not header_id:
        return None, "session_header_missing"
    return header_id, None


def resolve_session_file(session_dir, session_id):
    """Exact session file for the caller's dir/id, verified by the header id.

    File-name suffixes can collide after forks or copies, so the persisted
    header identity decides. Exactly one verified candidate is usable; zero or
    several matches stay unknown instead of picking a lexicographic winner.
    """
    if not session_dir or not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return None, 0, "no_session_source"
    try:
        base = Path(session_dir)
        if not base.is_dir():
            return None, 0, "session_dir_missing"
        suffix = f"_{session_id}.jsonl"
        candidates = sorted(entry for entry in base.iterdir()
                            if entry.is_file() and entry.name.endswith(suffix))
    except OSError:
        return None, 0, "session_dir_unreadable"
    if not candidates:
        return None, 0, "session_file_missing"
    matching, problems = [], []
    for candidate in candidates:
        header_id, problem = _session_header_id(candidate)
        if header_id == session_id:
            matching.append(candidate)
        else:
            problems.append(problem or "session_header_mismatch")
    if len(matching) == 1:
        return matching[0], len(candidates), None
    if not matching:
        return None, len(candidates), problems[0] if problems else "session_header_mismatch"
    return None, len(candidates), "session_identity_ambiguous"


def _iter_session_lines(path, limit=MAX_SESSION_LINE_BYTES):
    """Bounded session lines: oversized single records stay explicit unknown."""
    with Path(path).open("rb") as stream:
        while True:
            raw = stream.readline(limit)
            if not raw:
                return
            if len(raw) >= limit and not raw.endswith(b"\n"):
                while raw and not raw.endswith(b"\n"):
                    raw = stream.readline(limit)
                yield "overlong", None
                continue
            yield "line", raw


def _valid_auxiliary_token(value) -> bool:
    """Token counts must be finite non-negative integers; whole floats stay valid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value) or value < 0:
        return False
    return float(value).is_integer()


def _valid_auxiliary_cost(value) -> bool:
    """A reported cost must be finite and non-negative."""
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def _new_auxiliary():
    """Mutable accumulator for one bounded session pass; finalized afterwards.

    ``records``/``byId`` are a scan-scoped identity map only: records are
    aggregated into known totals after the pass and never persist as state.
    """
    return {"status": "unknown", "label": "unknown", "reliable": False, "complete": False,
            "sessionFile": None, "window": None, "reason": None, "sourceReason": None,
            "signalsVerified": False,
            "entries": 0, "kinds": {kind: 0 for kind in AUXILIARY_ENTRY_TYPES},
            "duplicateEntries": 0, "identityMissingEntries": 0,
            "identityConflicts": 0, "conflictedEntries": 0,
            "usageMissingEntries": 0, "usageInvalidEntries": 0,
            "costMissingEntries": 0, "costInvalidEntries": 0,
            "successfulCompactionEntries": 0, "reportedCompactionsWithoutEntry": 0,
            "failureSignals": None, "records": [], "byId": {},
            "totals": Counter(), "samples": Counter(),
            "invalidFields": Counter(), "costTotal": 0.0, "costSamples": 0}


def _auxiliary_fingerprint(entry) -> str:
    """Canonical content of one entry; non-finite numbers use a stable JSON spelling."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def _collect_auxiliary(auxiliary, entry):
    """Record one strict-window entry; an id conflict is never resolved by order.

    A byte/canonically identical replay is a duplicate. The same explicit id
    with different content is an explicit conflict: both occurrences are kept
    unscored instead of silently preferring the first.
    """
    fingerprint = _auxiliary_fingerprint(entry)
    entry_id = entry.get("id")
    record = {"id": entry_id if isinstance(entry_id, str) and entry_id else None,
              "kind": entry.get("type"), "fingerprint": fingerprint,
              "usage": entry.get("usage"), "conflict": False}
    if record["id"] is not None:
        existing = auxiliary["byId"].get(record["id"])
        if existing is not None:
            if existing["fingerprint"] == fingerprint:
                auxiliary["duplicateEntries"] += 1
                return
            auxiliary["identityConflicts"] += 1
            existing["conflict"] = True
            return
        auxiliary["byId"][record["id"]] = record
    else:
        auxiliary["identityMissingEntries"] += 1
    auxiliary["records"].append(record)


def _aggregate_auxiliary(auxiliary):
    """Aggregate unique records; invalid fields never join the known sums."""
    invalid = Counter()
    for record in auxiliary["records"]:
        auxiliary["entries"] += 1
        auxiliary["kinds"][record["kind"]] += 1
        if record["conflict"]:
            auxiliary["conflictedEntries"] += 1
            continue
        usage = record["usage"]
        if not isinstance(usage, dict):
            auxiliary["usageMissingEntries"] += 1
            auxiliary["costMissingEntries"] += 1
            continue
        entry_invalid = False
        for key in USAGE_KEYS:
            value = usage.get(key)
            if _valid_auxiliary_token(value):
                auxiliary["totals"][key] += value
                auxiliary["samples"][key] += 1
            elif key in usage:
                invalid[key] += 1
                entry_invalid = True
        cost = usage.get("cost")
        cost_value = cost.get("total") if isinstance(cost, dict) else None
        if _valid_auxiliary_cost(cost_value):
            auxiliary["costTotal"] += cost_value
            auxiliary["costSamples"] += 1
        elif cost_value is not None:
            auxiliary["costInvalidEntries"] += 1
        else:
            auxiliary["costMissingEntries"] += 1
        if entry_invalid:
            auxiliary["usageInvalidEntries"] += 1
    auxiliary["invalidFields"] = {key: invalid.get(key, 0) for key in USAGE_KEYS}


def _finalize_auxiliary(auxiliary, parse_clean, compaction_signals=None, round_signals_complete=True):
    """Per-key known/partial/unknown auxiliary totals with a conservative zero rule.

    A reliable session with no auxiliary entries, a verified round-signal source
    and no failed or unfinished compaction signal is a real zero. Invalid or
    conflicting values, a failure/unfinished signal without a successful entry,
    a missing session source, or an unverified round transcript stay
    unknown/incomplete instead of fabricating a definite zero.
    """
    _aggregate_auxiliary(auxiliary)
    signals = compaction_signals or {}
    started = int(signals.get("compactionStarted", 0) or 0)
    succeeded = int(signals.get("compactionSucceeded", 0) or 0)
    failed = int(signals.get("compactionFailed", 0) or 0)
    aborted = int(signals.get("compactionAborted", 0) or 0)
    unfinished = max(0, started - succeeded - failed - aborted)
    auxiliary["failureSignals"] = {"compactionStarted": started, "compactionSucceeded": succeeded,
                                   "compactionFailed": failed, "compactionAborted": aborted,
                                   "compactionUnfinished": unfinished}
    auxiliary["successfulCompactionEntries"] = auxiliary["kinds"]["compaction"]
    auxiliary["reportedCompactionsWithoutEntry"] = max(0, succeeded - auxiliary["kinds"]["compaction"])
    auxiliary["reliable"] = bool(auxiliary["sessionFile"]) and bool(parse_clean)
    auxiliary["signalsVerified"] = bool(round_signals_complete)
    entries = auxiliary["entries"]
    failure_total = failed + aborted + unfinished
    conflicts = auxiliary["identityConflicts"]
    zero_confident = (auxiliary["reliable"] and auxiliary["signalsVerified"]
                      and entries == 0 and failure_total == 0
                      and auxiliary["reportedCompactionsWithoutEntry"] == 0)
    for key in USAGE_KEYS:
        samples = auxiliary["samples"].get(key, 0)
        invalid = auxiliary["invalidFields"].get(key, 0)
        if zero_confident:
            leaf = _leaf(0.0, "exact", samples=0, invalid=0, complete=True)
        elif not auxiliary["reliable"]:
            leaf = _leaf(None, "unknown", samples=samples, invalid=invalid, complete=False,
                         reason=auxiliary.get("sourceReason") or "session_parse_incomplete")
        elif samples == 0:
            if invalid:
                reason = "auxiliary_usage_invalid"
            elif failure_total:
                reason = "compaction_attempt_without_success_entry"
            elif auxiliary["reportedCompactionsWithoutEntry"]:
                reason = "compaction_summary_without_session_entry"
            elif conflicts:
                reason = "auxiliary_entry_identity_conflict"
            elif not auxiliary["signalsVerified"]:
                reason = "round_signal_source_unverified"
            else:
                reason = "auxiliary_usage_missing"
            leaf = _leaf(None, "unknown", samples=0, invalid=invalid, complete=False, reason=reason)
        else:
            complete = (samples == entries and invalid == 0 and conflicts == 0
                        and auxiliary["signalsVerified"] and failure_total == 0
                        and auxiliary["reportedCompactionsWithoutEntry"] == 0
                        and auxiliary["identityMissingEntries"] == 0)
            leaf = _leaf(auxiliary["totals"][key], "exact" if complete else "estimated",
                         samples=samples, invalid=invalid, complete=complete,
                         reason=None if complete else "auxiliary_usage_invalid_or_incomplete")
        auxiliary[AUXILIARY_LEAF_NAMES[key]] = leaf
    if zero_confident:
        cost_leaf = _leaf(0.0, "exact", samples=0, invalid=0, complete=True)
    elif not auxiliary["reliable"]:
        cost_leaf = _leaf(None, "unknown", samples=auxiliary["costSamples"],
                          invalid=auxiliary["costInvalidEntries"], complete=False,
                          reason=auxiliary.get("sourceReason") or "session_parse_incomplete")
    elif auxiliary["costSamples"] == 0:
        if auxiliary["costInvalidEntries"]:
            reason = "auxiliary_cost_invalid"
        elif failure_total:
            reason = "compaction_attempt_without_success_entry"
        elif auxiliary["reportedCompactionsWithoutEntry"]:
            reason = "compaction_summary_without_session_entry"
        elif conflicts:
            reason = "auxiliary_entry_identity_conflict"
        elif not auxiliary["signalsVerified"]:
            reason = "round_signal_source_unverified"
        else:
            reason = "auxiliary_cost_missing"
        cost_leaf = _leaf(None, "unknown", samples=0, invalid=auxiliary["costInvalidEntries"],
                          complete=False, reason=reason)
    else:
        cost_complete = (auxiliary["costSamples"] == entries
                         and not auxiliary["costInvalidEntries"] and conflicts == 0
                         and auxiliary["signalsVerified"] and failure_total == 0
                         and auxiliary["reportedCompactionsWithoutEntry"] == 0
                         and auxiliary["identityMissingEntries"] == 0)
        cost_leaf = _leaf(auxiliary["costTotal"], "exact" if cost_complete else "estimated",
                          samples=auxiliary["costSamples"],
                          invalid=auxiliary["costInvalidEntries"], complete=cost_complete,
                          reason=None if cost_complete else "auxiliary_cost_invalid_or_incomplete")
    auxiliary["reportedCostUsd"] = cost_leaf
    auxiliary["complete"] = bool(
        auxiliary["reliable"] and auxiliary["signalsVerified"] and conflicts == 0
        and failure_total == 0 and auxiliary["reportedCompactionsWithoutEntry"] == 0
        and auxiliary["identityMissingEntries"] == 0
        and auxiliary["usageInvalidEntries"] == 0 and auxiliary["costInvalidEntries"] == 0
        and (entries == 0 or (all(auxiliary[AUXILIARY_LEAF_NAMES[key]]["complete"] for key in USAGE_KEYS)
                              and cost_leaf["complete"])))
    if not auxiliary["reliable"]:
        reason = auxiliary.get("sourceReason") or "session_parse_incomplete"
    elif conflicts:
        reason = "auxiliary_entry_identity_conflict"
    elif failure_total and entries == 0:
        reason = "compaction_attempt_without_success_entry"
    elif failure_total:
        reason = "compaction_failure_or_incomplete_signals"
    elif auxiliary["reportedCompactionsWithoutEntry"]:
        reason = "compaction_summary_without_session_entry"
    elif auxiliary["identityMissingEntries"]:
        reason = "auxiliary_entry_identity_missing"
    elif auxiliary["usageInvalidEntries"] or auxiliary["costInvalidEntries"]:
        reason = "auxiliary_usage_invalid"
    elif not auxiliary["signalsVerified"]:
        reason = "round_signal_source_unverified"
    elif entries and not all(auxiliary[AUXILIARY_LEAF_NAMES[key]]["complete"] for key in USAGE_KEYS):
        reason = "auxiliary_usage_missing_or_partial"
    elif entries and not cost_leaf["complete"]:
        reason = "auxiliary_cost_missing_or_partial"
    else:
        reason = None
    auxiliary["reason"] = reason
    auxiliary["status"] = "complete" if auxiliary["complete"] else (
        "partial" if auxiliary["reliable"] else "unknown")
    auxiliary["label"] = auxiliary["status"]
    # Scan-scoped state is not evidence: leaves already carry values and samples;
    # ``invalidFields`` stays as bounded per-field evidence instead.
    auxiliary.pop("byId", None)
    auxiliary.pop("records", None)
    auxiliary.pop("totals", None)
    auxiliary.pop("samples", None)


def derive_session_metrics(session_dir, session_id, window, compaction_signals=None,
                           round_signals_complete=True):
    """One bounded pass over the explicit session source for timing and auxiliary usage.

    Model intervals use the persisted session entry's outer write time and the
    assistant message's internal start timestamp. Tool intervals use the
    assistant persistence boundary and the matching tool-result message time;
    parent/child or concurrent intervals are unioned, never added. The same
    pass collects strict-window ``compaction``/``branch_summary``/``usage``
    entries; identical replays are deduplicated by explicit entry id, conflicting
    repeats stay unresolved, and none of them fold into the assistant fields.
    Missing or unreliable sources return null with an explicit reason instead of
    zero; ``round_signals_complete`` carries the round transcript's health, so an
    unreadable or truncated transcript never proves that no compaction ran.
    """
    coverage = {"sessionFile": None, "sessionCandidates": 0, "entriesInWindow": 0,
                "malformedLines": 0, "overlongLines": 0, "duplicateEntries": 0,
                "invalidTimestamps": 0, "invalidEntryTimestamps": 0,
                "outOfWindowEntries": 0,
                "duplicateToolCalls": 0, "toolResultsWithoutCall": 0,
                "openToolIntervals": 0}
    timing = {"status": "unknown", "label": "unknown",
              "windowSeconds": _leaf(None, "unknown", source=None, complete=False),
              "modelResponseSeconds": _leaf(None, "unknown", samples=0, complete=False, reason=None),
              "toolSeconds": _leaf(None, "unknown", samples=0, openIntervals=0,
                                   orphanResults=0, complete=False, reason=None),
              "coveredSeconds": _leaf(None, "unknown", samples=0, complete=False),
              "unattributedSeconds": _leaf(None, "unknown", complete=False, reason=None),
              "coverage": coverage, "reason": None}
    auxiliary = _new_auxiliary()
    start = _finite(window[0]) if window else None
    end = _finite(window[1]) if window else None
    if start is None or end is None or end < start:
        timing["reason"] = "round_window_missing"
        auxiliary["sourceReason"] = "round_window_missing"
        _finalize_auxiliary(auxiliary, False, compaction_signals, round_signals_complete)
        return {"timing": timing, "auxiliary": auxiliary}
    wall = end - start
    timing["windowSeconds"] = _leaf(wall, "exact",
                                    source="round state startedAt/endedAt", complete=True)
    auxiliary["window"] = {"start": start, "end": end, "strict": True}
    session_file, candidates, problem = resolve_session_file(session_dir, session_id)
    coverage["sessionCandidates"] = candidates
    if session_file is None:
        timing["reason"] = problem
        auxiliary["sourceReason"] = problem
        _finalize_auxiliary(auxiliary, False, compaction_signals, round_signals_complete)
        return {"timing": timing, "auxiliary": auxiliary}
    coverage["sessionFile"] = str(session_file)
    auxiliary["sessionFile"] = str(session_file)
    model_intervals, tool_intervals, open_tools = [], [], []
    pending, used_calls, seen_entries = {}, set(), set()
    for kind, raw in _iter_session_lines(session_file):
        if kind == "overlong":
            coverage["overlongLines"] += 1
            continue
        try:
            entry = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            coverage["malformedLines"] += 1
            continue
        if not isinstance(entry, dict):
            coverage["malformedLines"] += 1
            continue
        if entry.get("type") == "session":
            continue
        outer = _epoch(entry.get("timestamp"))
        if outer is None:
            coverage["invalidTimestamps"] += 1
            coverage["invalidEntryTimestamps"] += 1
            continue
        # Auxiliary cost attribution is strict: the timing slack absorbs clock skew
        # for intervals but must never move spend across a round boundary.
        if start <= outer <= end and entry.get("type") in AUXILIARY_ENTRY_TYPES:
            _collect_auxiliary(auxiliary, entry)
        if outer < start - SESSION_ENTRY_SLACK_SECONDS or outer > end + SESSION_ENTRY_SLACK_SECONDS:
            coverage["outOfWindowEntries"] += 1
            continue
        entry_id = entry.get("id")
        if isinstance(entry_id, str):
            if entry_id in seen_entries:
                coverage["duplicateEntries"] += 1
                continue
            seen_entries.add(entry_id)
        coverage["entriesInWindow"] += 1
        if entry.get("type") != "message":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        role, inner = message.get("role"), _finite(message.get("timestamp"))
        if role == "assistant":
            if inner is None or inner <= 0:
                coverage["invalidTimestamps"] += 1
                continue
            model_start, model_end = inner / 1000.0, outer
            if model_end < model_start:
                coverage["invalidTimestamps"] += 1
                continue
            model_start, model_end = max(model_start, start), min(model_end, end)
            if model_end < model_start:
                coverage["invalidTimestamps"] += 1
                continue
            model_intervals.append((model_start, model_end))
            for part in message.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "toolCall":
                    continue
                call_id = part.get("id")
                if not isinstance(call_id, str) or not call_id:
                    continue
                if call_id in used_calls:
                    coverage["duplicateToolCalls"] += 1
                    continue
                used_calls.add(call_id)
                pending[call_id] = model_end
        elif role == "toolResult":
            call_id = message.get("toolCallId")
            if not isinstance(call_id, str) or inner is None or inner <= 0:
                coverage["invalidTimestamps"] += 1
                continue
            tool_start = pending.pop(call_id, None)
            if tool_start is None:
                coverage["toolResultsWithoutCall"] += 1
                continue
            tool_end = inner / 1000.0
            tool_start, tool_end = max(tool_start, start), min(tool_end, end)
            if tool_end < tool_start:
                coverage["invalidTimestamps"] += 1
                continue
            tool_intervals.append((tool_start, tool_end))
    open_tools = [max(value, start) for value in pending.values()]
    coverage["openToolIntervals"] = len(open_tools)
    parse_clean = not (coverage["malformedLines"] or coverage["overlongLines"]
                       or coverage["invalidTimestamps"])
    coverage["parseClean"] = parse_clean
    # Auxiliary entries only need the entry-level parse to be clean; inner
    # assistant/tool timestamp clamping is a timing concern, not usage evidence.
    auxiliary_parse_clean = not (coverage["malformedLines"] or coverage["overlongLines"]
                                 or coverage["invalidEntryTimestamps"])
    coverage["auxiliaryParseClean"] = auxiliary_parse_clean
    if not coverage["entriesInWindow"]:
        timing["reason"] = "no_entries_in_window"
        _finalize_auxiliary(auxiliary, auxiliary_parse_clean, compaction_signals,
                            round_signals_complete)
        return {"timing": timing, "auxiliary": auxiliary}
    coverage["modelComplete"] = parse_clean and bool(model_intervals)
    coverage["toolComplete"] = (parse_clean and not open_tools
                                and not coverage["toolResultsWithoutCall"])
    model_complete = coverage["modelComplete"]
    tool_complete = coverage["toolComplete"]
    model_union = union_seconds(model_intervals)
    tool_union = union_seconds(tool_intervals)
    # Open intervals are a union too: several concurrent open calls must not be
    # summed as if they were sequential.
    coverage["openToolSeconds"] = union_seconds((value, end) for value in open_tools)
    model_value = model_union if model_intervals else None
    model_reason = None if model_complete else (
        "session_parse_incomplete" if model_value is not None
        else "no_assistant_intervals_in_window" if parse_clean else "session_parse_incomplete")
    timing["modelResponseSeconds"] = _leaf(
        model_value, "estimated" if model_value is not None else "unknown",
        samples=len(model_intervals), complete=model_complete, reason=model_reason)
    tool_value = tool_union if (tool_intervals or tool_complete) else None
    if tool_value is None:
        tool_reason = ("session_parse_incomplete" if not parse_clean else
                       "open_tool_intervals_excluded" if open_tools else "tool_results_without_call")
    elif tool_complete:
        tool_reason = None
    elif not parse_clean:
        tool_reason = "session_parse_incomplete"
    elif open_tools:
        tool_reason = "open_tool_intervals_excluded"
    else:
        tool_reason = "tool_results_without_call"
    timing["toolSeconds"] = _leaf(
        tool_value, "estimated" if tool_value is not None else "unknown",
        samples=len(tool_intervals), openIntervals=len(open_tools),
        orphanResults=coverage["toolResultsWithoutCall"], complete=tool_complete,
        reason=tool_reason)
    if model_complete and tool_complete:
        timing["coveredSeconds"] = _leaf(union_seconds(model_intervals + tool_intervals),
                                         "estimated",
                                         samples=len(model_intervals) + len(tool_intervals),
                                         complete=True)
        timing["unattributedSeconds"] = _leaf(
            max(0.0, wall - timing["coveredSeconds"]["value"]), "estimated", complete=True,
            reason="round wall minus the union of measured model and tool intervals")
        timing["status"] = "ok"
    else:
        timing["coveredSeconds"] = _leaf(
            None, "unknown", samples=len(model_intervals) + len(tool_intervals),
            complete=False, reason="requires complete model and tool coverage")
        timing["unattributedSeconds"] = _leaf(
            None, "unknown", complete=False,
            reason="requires complete model and tool coverage")
        timing["status"] = "partial" if (model_value is not None or tool_value is not None) else "unknown"
    timing["label"] = "estimated" if timing["status"] == "ok" else timing["status"]
    _finalize_auxiliary(auxiliary, auxiliary_parse_clean, compaction_signals, round_signals_complete)
    return {"timing": timing, "auxiliary": auxiliary}


def derive_timing(session_dir, session_id, window):
    """Backward-compatible timing-only view of the one bounded session pass."""
    return derive_session_metrics(session_dir, session_id, window)["timing"]


def usage_metrics(totals, samples, unique_messages, duplicates, reasoning_total, reasoning_samples,
                  identity_mode="none", heuristic_duplicates=0):
    def metric(key, source=None, note=None):
        count = samples.get(key, 0) if source is None else source
        value = totals.get(key) if source is None else reasoning_total
        complete = bool(unique_messages) and count == unique_messages
        extra = {"samples": count, "complete": complete}
        if note:
            extra["note"] = note
        return _leaf(value if count else None, "exact" if count else "unknown", **extra)

    ratio_samples = min(samples.get("input", 0), samples.get("cacheRead", 0))
    denominator = (totals.get("input") or 0) + (totals.get("cacheRead") or 0)
    if ratio_samples and denominator > 0:
        complete = (bool(unique_messages) and samples.get("input") == unique_messages
                    and samples.get("cacheRead") == unique_messages)
        ratio = _leaf(totals["cacheRead"] / denominator, "exact" if complete else "estimated",
                      samples=ratio_samples, complete=complete, formula=CACHE_RATIO_FORMULA)
    else:
        ratio = _leaf(None, "unknown", samples=ratio_samples, complete=False,
                      formula=CACHE_RATIO_FORMULA)
    return {"uncachedInput": metric("input"), "cacheRead": metric("cacheRead"),
            "cacheWrite": metric("cacheWrite"), "output": metric("output"),
            "reasoning": metric("reasoning", reasoning_samples,
                                "already included in output; never added again"),
            "totalTokens": metric("totalTokens"), "cacheRatio": ratio,
            "messages": unique_messages, "duplicateMessages": duplicates,
            "identity": identity_mode, "identityReliable": identity_mode == "response_id",
            "heuristicDuplicates": heuristic_duplicates}


def total_metrics(usage, usage_complete, assistant_cost, cost_samples, unique_messages, auxiliary):
    """Assistant plus auxiliary totals, complete only when both sides are established.

    Assistant fields keep their original meaning and are never merged into the
    auxiliary report; this block is the explicit overall view. A sum is only
    given when both sides are finite and known, so a missing or non-finite side
    stays null and no NaN/Infinity reaches the JSON.
    """
    def finite(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0)

    combined = {}
    for key in USAGE_KEYS:
        assistant_value = usage.get(key)
        auxiliary_value = (auxiliary.get(AUXILIARY_LEAF_NAMES[key]) or {}).get("value")
        combined[key] = (assistant_value + auxiliary_value
                         if finite(assistant_value) and finite(auxiliary_value) else None)
    assistant_cost_known = bool(unique_messages) and cost_samples == unique_messages
    auxiliary_complete = bool(auxiliary.get("complete"))
    auxiliary_cost = (auxiliary.get("reportedCostUsd") or {}).get("value")
    cost_known = (assistant_cost_known and auxiliary_complete and finite(assistant_cost)
                  and finite(auxiliary_cost))
    reasons = []
    if not usage_complete:
        reasons.append("assistant_usage_incomplete")
    if not auxiliary_complete:
        reasons.append("auxiliary_usage_incomplete")
    if not assistant_cost_known:
        reasons.append("assistant_cost_unknown")
    return {"assistantComplete": bool(usage_complete), "auxiliaryComplete": auxiliary_complete,
            "scope": "pi_execution", "costSource": "provider_reported",
            "billingCostUsd": None, "billingComplete": False,
            "billingReason": "no_verified_billing_or_price_source",
            "complete": bool(usage_complete and auxiliary_complete),
            "usage": combined,
            "reportedCostUsd": assistant_cost + auxiliary_cost if cost_known else None,
            "costComplete": cost_known,
            "reason": "; ".join(reasons) if reasons else None,
            "note": "Pi assistant and auxiliary usage only; costComplete means reported fields "
                    "are present, not verified billing. Reported zero does not prove free inference. "
                    "Codex design/review/takeover and external work are outside this scope; "
                    "compaction/branch_summary/usage entries are added once here, never twice"}


def context_metrics(samples, unique_messages):
    complete = bool(unique_messages) and len(samples) == unique_messages
    if not samples:
        return {"first": _leaf(None, "unknown", samples=0, complete=False),
                "last": _leaf(None, "unknown", samples=0, complete=False),
                "peak": _leaf(None, "unknown", samples=0, complete=False),
                "samples": 0, "complete": False}
    return {"first": _leaf(samples[0], "exact", samples=len(samples), complete=complete),
            "last": _leaf(samples[-1], "exact", samples=len(samples), complete=complete),
            "peak": _leaf(max(samples), "exact", samples=len(samples), complete=complete),
            "samples": len(samples), "complete": complete,
            "unit": "tokens: input + cacheRead + cacheWrite"}


def check_metrics(receipt_list):
    counts = Counter()
    elapsed, elapsed_samples, missing_time = 0.0, 0, 0
    for check in receipt_list:
        if check.get("timed_out") or check.get("cancelled"):
            counts["interrupted"] += 1
            continue
        code = check.get("exit_code")
        if code is None:
            counts["unknown"] += 1
            continue
        counts["completed"] += 1
        counts["passed" if code == 0 else "failed"] += 1
        start, end = _finite(check.get("started_at")), _finite(check.get("ended_at"))
        if start is not None and end is not None and end >= start:
            elapsed += end - start
            elapsed_samples += 1
        else:
            missing_time += 1
    complete = (missing_time == 0 and counts["unknown"] == 0)
    return {"attempts": len(receipt_list), "completed": counts["completed"],
            "passed": counts["passed"], "failed": counts["failed"],
            "interrupted": counts["interrupted"], "unknown": counts["unknown"],
            "unverifiedLogs": sum(1 for check in receipt_list if not check.get("log_verified")),
            "elapsedSeconds": _leaf(round(elapsed, 6), "exact" if complete else "estimated",
                                    samples=elapsed_samples, missingTime=missing_time,
                                    complete=complete,
                                    note="zero is a real zero when no completed check needs time; "
                                         "known partial sums stay partial while missing or unknown "
                                         "attempts remain, and interrupted attempts never fabricate "
                                         "completed time")}


def inside(path: Path, roots: list[Path]) -> bool:
    path = path.resolve()
    return any(path == root or root in path.parents for root in roots)


def read_meta(path: Path) -> dict:
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            # model/thinking share the first line; paths may contain spaces.
            if line.startswith("task="):
                result.update(part.split("=", 1) for part in line.split() if "=" in part)
            elif "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
    return result


def receipts(directory: Path) -> list[dict]:
    result = []
    if not directory.exists():
        return result
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            log = path.parent / data["log"]
            # Receipt verification is streaming: a large check log is never
            # read into memory whole at terminal-summary or result time.
            valid = (inside(log, [path.parent.resolve()]) and log.is_file()
                     and sha256_file(log) == data["log_sha256"])
            result.append({key: data.get(key) for key in (
                "id", "argv", "head", "dirty", "exit_code", "timed_out", "cancelled",
                "started_at", "ended_at", "test_counts", "log")}
                | {"receipt": str(path), "log_verified": valid,
                   "resource_limit": sanitize_snapshot(data.get("resource_limit")
                                                       or data.get("resourceLimit"))})
        except (ValueError, KeyError, OSError, TypeError):
            result.append({"receipt": str(path), "log_verified": False, "exit_code": None})
    return result


def _round_lines(log: Path):
    if log.exists():
        with log.open() as stream:
            yield from stream


def summarize(log: Path, worktree: Path, run_dir: Path | None = None,
              expected_model: str | None = None, checks_dir: Path | None = None,
              session_dir: Path | None = None, session_id: str | None = None,
              window: tuple | None = None) -> dict:
    run_dir = (run_dir or log.parent).resolve()
    worktree = worktree.resolve()
    meta = read_meta(log.with_suffix(".meta"))
    expected_model = expected_model or meta.get("model")
    models, stop_reasons, counts, totals = Counter(), Counter(), Counter(), Counter()
    usage_samples = Counter()
    flags, errors, commands = [], [], []
    started = {}
    malformed = turns = assistant_messages = duplicate_usage = 0
    unique_usage_messages = heuristic_duplicates = no_identity_messages = 0
    identity_modes: set = set()
    reasoning_total, reasoning_samples = 0.0, 0
    context_samples: list[float] = []
    seen_usage: set = set()
    final, cost, cost_samples = "", 0.0, 0
    compaction_signals = {"compactionStarted": 0, "compactionSucceeded": 0,
                          "compactionFailed": 0, "compactionAborted": 0}
    for line_no, raw in enumerate(_round_lines(log), 1):
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("not an event")
        except (ValueError, TypeError):
            malformed += 1
            continue
        kind = event.get("type")
        if kind == "turn_end":
            turns += 1
        elif kind == "compaction_start":
            compaction_signals["compactionStarted"] += 1
        elif kind == "compaction_end":
            # JSON-mode transcripts expose Pi's own compaction outcome. A result
            # without an abort is a success; anything else is failed or cancelled.
            if isinstance(event.get("result"), dict) and not event.get("aborted"):
                compaction_signals["compactionSucceeded"] += 1
            elif event.get("aborted"):
                compaction_signals["compactionAborted"] += 1
            else:
                compaction_signals["compactionFailed"] += 1
        elif kind == "tool_execution_start":
            tool, args = event.get("toolName", "?"), event.get("args") or {}
            counts[tool] += 1
            text = str(args.get("command") or args.get("path") or args)
            for pattern, label in RISKY:
                if re.search(pattern, text):
                    flags.append(f"line {line_no}: {label}: {text[:180]}")
            if tool == "bash":
                command = {"command": text, "line": line_no, "exit_code": None, "tool_error": None}
                commands.append(command)
                started[event.get("toolCallId")] = command
        elif kind == "tool_execution_end":
            command = started.get(event.get("toolCallId"))
            if command is not None:
                command["tool_error"] = event.get("isError")
                details = (event.get("result") or {}).get("details") or {}
                code = details.get("exitCode")
                if isinstance(code, int) and not isinstance(code, bool):
                    command["exit_code"] = code
                # isError=False and a green-looking text are not a numeric exit code.
        elif kind == "message_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            assistant_messages += 1
            model = f'{message.get("provider", "?")}/{message.get("model", "?")}'
            models[model] += 1
            stop_reasons[str(message.get("stopReason", "unknown"))] += 1
            if message.get("errorMessage"):
                errors.append(str(message["errorMessage"])[:300])
            usage = message.get("usage") or {}
            # Deduplicate only explicit identities. Equal timestamps, usage or
            # even bodies do not prove two provider responses are the same.
            response_id = message.get("responseId")
            message_id = message.get("id")
            identity, mode = None, "none"
            if isinstance(response_id, str) and response_id:
                identity = ("response_id", message.get("provider"), message.get("model"), response_id)
                mode = "response_id"
            elif isinstance(message_id, str) and message_id:
                identity = ("message_id", message.get("provider"), message.get("model"), message_id)
                mode = "message_id"
            if identity is None:
                no_identity_messages += 1
            else:
                identity_modes.add(mode)
            duplicate = identity is not None and identity in seen_usage
            if duplicate:
                duplicate_usage += 1
            else:
                if identity is not None:
                    seen_usage.add(identity)
                unique_usage_messages += 1
                for key in USAGE_KEYS:
                    value = usage.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        totals[key] += value
                        usage_samples[key] += 1
                reasoning = usage.get("reasoning")
                if isinstance(reasoning, (int, float)) and not isinstance(reasoning, bool):
                    reasoning_total += reasoning
                    reasoning_samples += 1
                context_parts = [usage.get(key) for key in ("input", "cacheRead", "cacheWrite")]
                if all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       for value in context_parts):
                    context_samples.append(float(sum(context_parts)))
                value = (usage.get("cost") or {}).get("total")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    cost += value
                    cost_samples += 1
            texts = [part["text"] for part in message.get("content", [])
                     if part.get("type") == "text" and part.get("text")]
            if texts:
                final = "\n".join(texts)
    model_check = "unknown"
    if models and expected_model:
        model_check = "matched" if set(models) == {expected_model} else "mismatch"
    usage = {key: totals[key] if usage_samples[key] else None for key in USAGE_KEYS}
    full_usage = bool(unique_usage_messages) and all(usage_samples[k] == unique_usage_messages
                                                for k in USAGE_KEYS[:-1])
    check_dir = checks_dir or (run_dir / (log.stem.replace("round-", "checks-") if log.stem.startswith("round-") else "checks"))
    check_receipts = receipts(check_dir)
    terminal = terminal_evidence(log)
    final = terminal.get("finalText") or ""
    session_metrics = derive_session_metrics(session_dir, session_id, window, compaction_signals,
                                             round_signals_complete=log.exists() and malformed == 0)
    auxiliary = session_metrics["auxiliary"]
    metrics = {"schema": 1,
               "usage": usage_metrics(totals, usage_samples, unique_usage_messages,
                                      duplicate_usage, reasoning_total, reasoning_samples,
                                      "response_id" if (identity_modes == {"response_id"}
                                                        and no_identity_messages == 0
                                                        and assistant_messages)
                                      else "unavailable" if identity_modes or no_identity_messages
                                      else "none",
                                      heuristic_duplicates),
               "context": context_metrics(context_samples, unique_usage_messages),
               "checks": check_metrics(check_receipts),
               "timing": session_metrics["timing"],
               "auxiliary": auxiliary,
               "total": total_metrics(usage, full_usage, cost, cost_samples,
                                      unique_usage_messages, auxiliary),
               "labels": "usage/context/check counters come from recorded final messages and "
                          "receipts; timing is estimated from session write timestamps; auxiliary "
                          "usage comes from strict-window compaction/branch_summary/usage session "
                          "entries deduplicated by id; unknown stays null and is never a fabricated zero"}
    return {"schema_version": 1, "execution_evidence": terminal, "source": str(log.resolve()), "source_bytes": log.stat().st_size if log.exists() else 0,
            "turns": turns, "assistant_messages": assistant_messages, "models": dict(models),
            "expected_model": expected_model, "model_check": model_check,
            "usage": usage, "usage_complete": full_usage,
            "reported_cost_usd": cost if cost_samples else None, "cost_samples": cost_samples,
            "auxiliary_usage": {"status": auxiliary["status"], "complete": auxiliary["complete"],
                                "reliable": auxiliary["reliable"], "entries": auxiliary["entries"],
                                "kinds": dict(auxiliary["kinds"]),
                                "usage": {key: (auxiliary.get(AUXILIARY_LEAF_NAMES[key]) or {}).get("value")
                                          for key in USAGE_KEYS},
                                "reported_cost_usd": (auxiliary.get("reportedCostUsd") or {}).get("value"),
                                "reason": auxiliary.get("reason"),
                                "sessionFile": auxiliary.get("sessionFile")},
            "stop_reasons": dict(stop_reasons), "tool_counts": dict(counts),
            "commands": commands, "guardrail_flags": list(dict.fromkeys(flags)),
            "errors": errors, "malformed_lines": malformed, "final_text": final,
            "metrics": metrics,
            "check_receipts": check_receipts, "checks_dir": str(check_dir.resolve()),
            "process_exit": meta.get("exit"),
            "acceptance": "not_verified"}


CHECK_FLAGS = ("failed", "timed_out", "unverified_log", "unknown_exit", "skipped", "zero_run", "unknown_counts", "resource_breach")


def check_flag_map(check: dict) -> dict:
    code = check.get("exit_code")
    counts = check.get("test_counts") or {}
    resource = check.get("resource_limit") or {}
    return {"failed": (code is not None and code != 0) or bool(counts.get("fail")),
            "timed_out": bool(check.get("timed_out")),
            "unverified_log": not check["log_verified"],
            "unknown_exit": code is None,
            "skipped": bool(counts.get("skip")),
            "zero_run": bool(counts) and counts.get("run") == 0,
            "unknown_counts": not counts,
            "resource_breach": bool(resource.get("breached"))}


def check_overview(data: dict, limit: int = 8) -> dict:
    """Aggregate every receipt (never dropping failures) and show at most `limit`."""
    checks = data.get("check_receipts", [])
    totals = Counter()
    for check in checks:
        totals.update({key: int(value) for key, value in check_flag_map(check).items()})
    selected = sorted(checks, key=lambda check: tuple(-int(check_flag_map(check)[key])
                                                      for key in CHECK_FLAGS))[:max(0, limit)]
    receipts = [{"id": check.get("id"), "receipt": check.get("receipt"),
                 "exit_code": check.get("exit_code"), "timed_out": check.get("timed_out"),
                 "test_counts": check.get("test_counts"), "log_verified": check.get("log_verified"),
                 "resource_limit": check.get("resource_limit"),
                 "flags": [key for key in CHECK_FLAGS if check_flag_map(check)[key]]} for check in selected]
    return {"total": len(checks), "counts": {key: totals[key] for key in CHECK_FLAGS},
            "receipts": receipts, "listed": len(receipts),
            "absence": "missing evidence: no check receipts were recorded" if not checks else None,
            "note": "all attempts are aggregated; retries do not erase failures"}


def bounded(data: dict, receipt_limit: int = 8) -> dict:
    """Small, safe-to-return structured evidence. Not acceptance."""
    return {"schema_version": 1, "source": data["source"], "source_bytes": data["source_bytes"],
            "checks_dir": data.get("checks_dir"), "turns": data["turns"],
            "assistant_messages": data["assistant_messages"], "models": data["models"],
            "expected_model": data["expected_model"], "model_check": data["model_check"],
            "usage": data["usage"], "usage_complete": data["usage_complete"],
            "reported_cost_usd": data["reported_cost_usd"], "cost_samples": data["cost_samples"],
            "auxiliary_usage": data.get("auxiliary_usage"),
            "total_usage_complete": bool((data.get("metrics") or {}).get("total", {})
                                          .get("complete")),
            "stop_reasons": data["stop_reasons"], "tool_counts": data["tool_counts"],
            "guardrail_flags": data["guardrail_flags"][:20],
            "guardrail_flag_count": len(data["guardrail_flags"]),
            "errors": data["errors"][:20], "error_count": len(data["errors"]),
            "malformed_lines": data["malformed_lines"],
            "final_excerpt": data["final_text"][:1200],
            "process_exit": data["process_exit"], "checks": check_overview(data, receipt_limit),
            "metrics": data.get("metrics"),
            "acceptance": "not_verified"}


def compact(data: dict) -> str:
    lines = [f'Pi evidence: turns={data["turns"]} messages={data["assistant_messages"]} process_exit={data["process_exit"]}',
             f'model_check={data["model_check"]} models={data["models"]}',
             f'usage={data["usage"]} complete={data["usage_complete"]} reported_cost_usd={data["reported_cost_usd"]}',
             f'tools={data["tool_counts"]} malformed_lines={data["malformed_lines"]}',
             'acceptance=not_verified (requires PLAN checks and independent review)']
    metrics = data.get("metrics") or {}

    def leaf(section, key):
        item = (metrics.get(section) or {}).get(key) or {}
        value = item.get("value")
        return f"{value if value is not None else 'unknown'}({item.get('label', 'unknown')})"

    usage_metrics, check_metrics_data, timing = (metrics.get("usage") or {},
                                                 metrics.get("checks") or {},
                                                 metrics.get("timing") or {})
    lines.append(
        "metrics usage: " + " ".join(f"{key}={leaf('usage', key)}" for key in (
            "uncachedInput", "cacheRead", "cacheWrite", "output", "reasoning", "totalTokens", "cacheRatio")))
    lines.append("metrics context: " + " ".join(
        f"{key}={leaf('context', key)}" for key in ("first", "last", "peak")))
    lines.append(
        f"metrics checks: attempts={check_metrics_data.get('attempts')} "
        f"completed={check_metrics_data.get('completed')} failed={check_metrics_data.get('failed')} "
        f"interrupted={check_metrics_data.get('interrupted')} unknown={check_metrics_data.get('unknown')} "
        f"elapsed={leaf('checks', 'elapsedSeconds')}")
    lines.append(
        f"metrics timing: window={leaf('timing', 'windowSeconds')} "
        f"model={leaf('timing', 'modelResponseSeconds')} tool={leaf('timing', 'toolSeconds')} "
        f"unattributed={leaf('timing', 'unattributedSeconds')} status={timing.get('status')} "
        f"reason={timing.get('reason')}")
    auxiliary = metrics.get("auxiliary") or {}
    total = metrics.get("total") or {}
    lines.append(
        "metrics auxiliary: " + " ".join(f"{key}={leaf('auxiliary', key)}" for key in (
            "uncachedInput", "cacheRead", "cacheWrite", "output", "totalTokens", "reportedCostUsd"))
        + f" entries={auxiliary.get('entries')} kinds={auxiliary.get('kinds')} "
          f"complete={auxiliary.get('complete')} reason={auxiliary.get('reason')}")
    lines.append(
        f"metrics total: complete={total.get('complete')} "
        f"assistantComplete={total.get('assistantComplete')} "
        f"auxiliaryComplete={total.get('auxiliaryComplete')} "
        f"reportedCostUsd={total.get('reportedCostUsd')} reason={total.get('reason')}")
    for label, items in (("errors", data["errors"]), ("guardrail_flags", data["guardrail_flags"])):
        lines.append(f"{label}: count={len(items)}")
        lines.extend("  " + str(item)[:240] for item in items[:5])
    commands = data["commands"]
    lines.append(f'commands: total={len(commands)} unknown_exit={sum(c["exit_code"] is None for c in commands)} '
                 f'tool_errors={sum(c["tool_error"] is True for c in commands)}')
    checks = data["check_receipts"]
    totals = Counter()
    for check in checks:
        totals.update({key: int(value) for key, value in check_flag_map(check).items()})
    lines.append(f"receipt_attempts: total={len(checks)} " + " ".join(f"{key}={totals[key]}" for key in (
        "failed", "timed_out", "unverified_log", "unknown_exit", "skipped", "zero_run", "unknown_counts",
        "resource_breach")))
    lines.append("All attempts counted; retries do not erase failures. Absence is missing evidence.")
    priority = CHECK_FLAGS
    selected = sorted(checks, key=lambda check: tuple(-int(check_flag_map(check)[key]) for key in priority))[:8]
    for check in selected:
        lines.append(f'  {check.get("id", "?")}: exit={check.get("exit_code")} log_verified={check["log_verified"]} counts={check.get("test_counts")}')
    lines.extend(["final excerpt (untrusted report):", data["final_text"][:1200],
                  f'full evidence: {data["source"]}; use --json or --commands only for a specific investigation'])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--checks-dir", type=Path)
    parser.add_argument("--expected-model")
    parser.add_argument("--session-dir", type=Path,
                        help="explicit session directory owned by the caller's task")
    parser.add_argument("--session-id", help="explicit session id inside --session-dir")
    parser.add_argument("--round-started-at", type=float, help="round window start (unix seconds)")
    parser.add_argument("--round-ended-at", type=float, help="round window end (unix seconds)")
    parser.add_argument("--json", action="store_true", help="Full structured evidence, preferably redirect to disk")
    parser.add_argument("--commands", action="store_true", help="Explicitly expand command evidence")
    args = parser.parse_args()
    window = (args.round_started_at, args.round_ended_at) \
        if args.round_started_at is not None and args.round_ended_at is not None else None
    data = summarize(args.jsonl, args.worktree, args.run_dir, args.expected_model, args.checks_dir,
                     session_dir=args.session_dir, session_id=args.session_id, window=window)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    elif args.commands:
        print(json.dumps(data["commands"], ensure_ascii=False, separators=(",", ":")))
    else:
        print(compact(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
