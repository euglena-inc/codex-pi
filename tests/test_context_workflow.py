"""Behavioral tests for auxiliary session usage and idle native-compaction evidence.

Expected facts are computed independently in this file from literal synthetic
values; the runtime functions under test are never used as the oracle. Raw
sessions and receipts stay in temporary directories.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from runtime_helpers import RUNTIME

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402
import pi_summary  # noqa: E402
import pi_task  # noqa: E402


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def session_entry(entry_id, outer: float, entry_type: str = "message", **fields) -> dict:
    entry = {"type": entry_type, "id": entry_id, "parentId": None,
             "timestamp": iso(outer)}
    entry.update(fields)
    return entry


def assistant(inner_ms: int, usage=None, calls=()) -> dict:
    return {"role": "assistant", "provider": "p", "model": "m", "timestamp": inner_ms,
            "stopReason": "toolUse" if calls else "stop", "usage": usage or {},
            "content": [{"type": "toolCall", "id": call_id, "name": "bash"} for call_id in calls]}


def usage(input=0, cacheRead=0, cacheWrite=0, output=0, totalTokens=None, cost=0.0) -> dict:
    total = input + cacheRead + cacheWrite + output if totalTokens is None else totalTokens
    return {"input": input, "cacheRead": cacheRead, "cacheWrite": cacheWrite, "output": output,
            "totalTokens": total,
            "cost": {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0,
                     "total": cost}}


def assistant_event(inner_ms: int, usage_dict: dict) -> dict:
    return {"type": "message_end", "message": assistant(inner_ms, usage=usage_dict)}


class ContextWorkflowCase(unittest.TestCase):
    window = (100.0, 130.0)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-context-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.round_dir = self.tmp / "rounds" / "1"
        self.round_dir.mkdir(parents=True)
        self.session_dir = self.tmp / "session"
        self.session_dir.mkdir()

    def write_session(self, entries, session_id="sid"):
        lines = [{"type": "session", "version": 3, "id": session_id,
                  "timestamp": "1970-01-01T00:00:00.000Z", "cwd": "/tmp"}] + entries
        path = self.session_dir / f"2026-01-01T00-00-00-000Z_{session_id}.jsonl"
        path.write_text("\n".join(json.dumps(entry) for entry in lines) + "\n", encoding="utf-8")
        return path

    def summarize(self, events=(), window=None, session_dir=None, session_id="sid"):
        log = self.round_dir / "round.jsonl"
        log.write_text("\n".join(json.dumps(event) for event in events)
                       + ("\n" if events else ""), encoding="utf-8")
        return pi_summary.summarize(
            log, self.tmp, self.round_dir, "p/m",
            checks_dir=self.round_dir / "round.checks",
            session_dir=self.session_dir if session_dir is None else session_dir,
            session_id=session_id, window=self.window if window is None else window)


class AuxiliaryUsageTest(ContextWorkflowCase):
    def test_auxiliary_entries_are_summed_once_by_explicit_entry_id(self):
        # Independent expected sums: 10+3+1 input, 5+2+1 output, 1+0+0 cacheRead,
        # 2+0+0 cacheWrite, 18+5+2 totalTokens, 0.5+0.1+0.01 cost. The repeated
        # c1 entry is a byte-identical replay and must not add its values twice.
        entries = [
            session_entry("c1", 105.0, "compaction", summary="s1", firstKeptEntryId="x",
                          tokensBefore=100, usage=usage(10, 1, 2, 5, 18, 0.5)),
            session_entry("b1", 110.0, "branch_summary", fromId="x", summary="s2",
                          usage=usage(3, 0, 0, 2, 5, 0.1)),
            session_entry("u1", 115.0, "usage", kind="cache_warm", provider="p", model="m",
                          usage=usage(1, 0, 0, 1, 2, 0.01)),
            session_entry("outside", 90.0, "usage", kind="cache_warm", provider="p", model="m",
                          usage=usage(777, 0, 0, 0, 777, 7.7)),
            session_entry("a1", 104.0, message=assistant(101000, usage=usage(2, 0, 0, 1, 3, 0.02))),
        ]
        replay = dict(entries[0])
        self.write_session(entries + [replay])
        data = self.summarize([assistant_event(101000, usage(2, 0, 0, 1, 3, 0.02))])
        auxiliary = data["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["entries"], 3)
        self.assertEqual(auxiliary["kinds"], {"compaction": 1, "branch_summary": 1, "usage": 1})
        self.assertEqual(auxiliary["duplicateEntries"], 1)
        self.assertEqual(auxiliary["identityConflicts"], 0)
        self.assertEqual(auxiliary["uncachedInput"]["value"], 14)
        self.assertEqual(auxiliary["cacheRead"]["value"], 1)
        self.assertEqual(auxiliary["cacheWrite"]["value"], 2)
        self.assertEqual(auxiliary["output"]["value"], 8)
        self.assertEqual(auxiliary["totalTokens"]["value"], 25)
        self.assertEqual(auxiliary["reportedCostUsd"]["value"], 0.61)
        self.assertTrue(auxiliary["complete"])
        self.assertEqual(auxiliary["status"], "complete")
        self.assertIsNone(auxiliary["reason"])
        # Assistant legacy fields remain assistant-only.
        self.assertEqual(data["usage"]["input"], 2)
        self.assertEqual(data["reported_cost_usd"], 0.02)
        # The explicit total adds auxiliary once.
        self.assertEqual(data["metrics"]["total"]["usage"]["input"], 16)
        self.assertEqual(data["metrics"]["total"]["reportedCostUsd"], 0.63)
        self.assertTrue(data["metrics"]["total"]["complete"])
        self.assertEqual(data["auxiliary_usage"]["usage"]["input"], 14)
        self.assertEqual(data["auxiliary_usage"]["reported_cost_usd"], 0.61)

    def test_same_id_conflicting_usage_is_never_resolved_by_order(self):
        conflict_low = session_entry("c1", 105.0, "compaction", summary="low",
                                     firstKeptEntryId="x", tokensBefore=1,
                                     usage=usage(1, 0, 0, 1, 2, 0.01))
        conflict_high = session_entry("c1", 106.0, "compaction", summary="high",
                                      firstKeptEntryId="x", tokensBefore=9,
                                      usage=usage(10, 0, 0, 5, 15, 0.5))
        valid = session_entry("u2", 110.0, "usage", kind="cache_warm", provider="p", model="m",
                              usage=usage(7, 0, 0, 4, 11, 0.07))
        observed = []
        for session_id, pair in (("conflict-forward", [conflict_low, conflict_high]),
                                 ("conflict-reverse", [conflict_high, conflict_low])):
            self.write_session(pair + [valid], session_id=session_id)
            auxiliary = self.summarize(session_id=session_id)["metrics"]["auxiliary"]
            observed.append({
                "input": auxiliary["uncachedInput"], "cost": auxiliary["reportedCostUsd"],
                "entries": auxiliary["entries"], "conflicts": auxiliary["identityConflicts"],
                "conflicted": auxiliary["conflictedEntries"],
                "duplicate": auxiliary["duplicateEntries"], "complete": auxiliary["complete"],
                "reason": auxiliary["reason"]})
        first, second = observed
        self.assertEqual(first, second, "reversing the conflict order must not pick a winner")
        self.assertEqual(first["conflicts"], 1)
        self.assertEqual(first["conflicted"], 1)
        self.assertEqual(first["duplicate"], 0)
        self.assertEqual(first["entries"], 2)
        self.assertFalse(first["complete"])
        self.assertEqual(first["reason"], "auxiliary_entry_identity_conflict")
        self.assertEqual(first["input"]["value"], 7, "only the valid separate entry is counted")
        self.assertEqual(first["input"]["samples"], 1)
        self.assertFalse(first["input"]["complete"])
        self.assertEqual(first["cost"]["value"], 0.07)
        self.assertEqual(first["cost"]["samples"], 1)
        self.assertFalse(first["cost"]["complete"])

    def test_invalid_auxiliary_values_are_excluded_and_incomplete(self):
        bad = {"input": float("nan"), "cacheRead": float("inf"), "cacheWrite": 1.5,
               "output": -5, "totalTokens": 3, "cost": {"total": -1.0}}
        entries = [
            session_entry("bad", 105.0, "compaction", summary="s", firstKeptEntryId="x",
                          tokensBefore=1, usage=bad),
            session_entry("good", 110.0, "usage", kind="cache_warm", provider="p", model="m",
                          usage=usage(2, 0, 0, 3, 5, 0.25)),
        ]
        self.write_session(entries)
        data = self.summarize()
        auxiliary = data["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["entries"], 2)
        self.assertEqual(auxiliary["identityConflicts"], 0)
        self.assertEqual(auxiliary["usageInvalidEntries"], 1)
        self.assertEqual(auxiliary["costInvalidEntries"], 1)
        self.assertEqual(auxiliary["invalidFields"], {"input": 1, "cacheRead": 1, "cacheWrite": 1,
                                                      "output": 1, "totalTokens": 0})
        self.assertEqual(auxiliary["uncachedInput"]["value"], 2, "only the valid entry is counted")
        self.assertEqual(auxiliary["uncachedInput"]["samples"], 1)
        self.assertFalse(auxiliary["uncachedInput"]["complete"])
        self.assertEqual(auxiliary["output"]["value"], 3)
        self.assertEqual(auxiliary["totalTokens"]["value"], 8)
        self.assertEqual(auxiliary["reportedCostUsd"]["value"], 0.25)
        self.assertEqual(auxiliary["reportedCostUsd"]["samples"], 1)
        self.assertEqual(auxiliary["reportedCostUsd"]["invalid"], 1)
        self.assertFalse(auxiliary["complete"])
        self.assertEqual(auxiliary["status"], "partial")
        self.assertEqual(auxiliary["reason"], "auxiliary_usage_invalid")
        self.assertIsNone(data["metrics"]["total"]["reportedCostUsd"])
        json.dumps(data, allow_nan=False)  # JSON must never carry NaN/Infinity

    def test_malformed_round_line_marks_the_signal_source_unverified(self):
        self.write_session([session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01)))])
        log = self.round_dir / "round.jsonl"
        log.write_text('{"type": "turn_end"}\n{"broken": \n', encoding="utf-8")
        data = pi_summary.summarize(log, self.tmp, self.round_dir, "p/m",
                                    checks_dir=self.round_dir / "round.checks",
                                    session_dir=self.session_dir, session_id="sid",
                                    window=self.window)
        auxiliary = data["metrics"]["auxiliary"]
        self.assertFalse(auxiliary["signalsVerified"])
        self.assertFalse(auxiliary["complete"])
        self.assertIsNone(auxiliary["uncachedInput"]["value"])
        self.assertEqual(auxiliary["reason"], "round_signal_source_unverified")

    def test_unverified_round_signal_keeps_known_sum_but_not_complete(self):
        self.write_session([
            session_entry("c1", 105.0, "compaction", summary="s", firstKeptEntryId="x",
                          tokensBefore=1, usage=usage(10, 0, 0, 5, 15, 0.5)),
            session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))),
        ])
        log = self.round_dir / "round.jsonl"
        log.write_text("not json at all\n", encoding="utf-8")
        data = pi_summary.summarize(log, self.tmp, self.round_dir, "p/m",
                                    checks_dir=self.round_dir / "round.checks",
                                    session_dir=self.session_dir, session_id="sid",
                                    window=self.window)
        auxiliary = data["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["uncachedInput"]["value"], 10, "known sum stays visible")
        self.assertFalse(auxiliary["uncachedInput"]["complete"])
        self.assertFalse(auxiliary["complete"])
        self.assertEqual(auxiliary["reason"], "round_signal_source_unverified")

    def test_missing_round_log_is_not_a_definite_zero(self):
        self.write_session([session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01)))])
        data = pi_summary.summarize(self.round_dir / "absent.jsonl", self.tmp, self.round_dir, "p/m",
                                    checks_dir=self.round_dir / "round.checks",
                                    session_dir=self.session_dir, session_id="sid",
                                    window=self.window)
        auxiliary = data["metrics"]["auxiliary"]
        self.assertFalse(auxiliary["signalsVerified"])
        self.assertFalse(auxiliary["complete"])
        self.assertIsNone(auxiliary["uncachedInput"]["value"])

    def test_strict_window_ignores_timing_slack_for_cost_attribution(self):
        # 130.5 is inside the timing +-1s slack but outside the strict round window;
        # 100.0 is the inclusive start boundary and must stay attributed.
        entries = [
            session_entry("in-start", 100.0, "usage", kind="cache_warm", provider="p", model="m",
                          usage=usage(1, 0, 0, 0, 1, 0.01)),
            session_entry("late", 130.5, "usage", kind="cache_warm", provider="p", model="m",
                          usage=usage(20, 0, 0, 0, 20, 0.2)),
            session_entry("early", 99.5, "usage", kind="cache_warm", provider="p", model="m",
                          usage=usage(30, 0, 0, 0, 30, 0.3)),
            session_entry("a1", 104.0, message=assistant(101000, usage=usage(5, 0, 0, 5, 10, 0.1))),
        ]
        self.write_session(entries)
        auxiliary = self.summarize([assistant_event(101000, usage(5, 0, 0, 5, 10, 0.1))])["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["entries"], 1)
        self.assertEqual(auxiliary["uncachedInput"]["value"], 1)
        self.assertEqual(auxiliary["reportedCostUsd"]["value"], 0.01)
        self.assertTrue(auxiliary["complete"])

    def test_entry_without_usage_is_never_a_zero(self):
        entries = [
            session_entry("c-missing", 105.0, "compaction", summary="s", firstKeptEntryId="x",
                          tokensBefore=10),
            session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))),
        ]
        self.write_session(entries)
        auxiliary = self.summarize([assistant_event(101000, usage(1, 0, 0, 1, 2, 0.01))])["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["entries"], 1)
        self.assertEqual(auxiliary["usageMissingEntries"], 1)
        self.assertIsNone(auxiliary["uncachedInput"]["value"])
        self.assertFalse(auxiliary["uncachedInput"]["complete"])
        self.assertFalse(auxiliary["complete"])
        self.assertEqual(auxiliary["status"], "partial")
        self.assertEqual(auxiliary["reason"], "auxiliary_usage_missing_or_partial")
        self.assertIsNone(self.summarize()["metrics"]["total"]["usage"]["input"])

    def test_partial_entry_usage_keeps_known_sum_but_not_complete(self):
        entries = [
            session_entry("c-partial", 105.0, "compaction", summary="s", firstKeptEntryId="x",
                          tokensBefore=10, usage={"input": 7, "output": 3}),
            session_entry("u-full", 110.0, "usage", kind="cache_warm", provider="p", model="m",
                          usage=usage(1, 0, 0, 1, 2, 0.01)),
            session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))),
        ]
        self.write_session(entries)
        auxiliary = self.summarize([assistant_event(101000, usage(1, 0, 0, 1, 2, 0.01))])["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["uncachedInput"]["value"], 8, "known part stays visible")
        self.assertEqual(auxiliary["uncachedInput"]["samples"], 2)
        self.assertTrue(auxiliary["uncachedInput"]["complete"],
                        "every entry reported input, so the per-key sum is exact")
        self.assertEqual(auxiliary["cacheRead"]["value"], 0,
                         "a reported zero is known; the partial entry just makes it incomplete")
        self.assertEqual(auxiliary["cacheRead"]["samples"], 1)
        self.assertFalse(auxiliary["cacheRead"]["complete"])
        self.assertEqual(auxiliary["totalTokens"]["samples"], 1)
        self.assertFalse(auxiliary["complete"])
        self.assertFalse(self.summarize([assistant_event(101000, usage(1, 0, 0, 1, 2, 0.01))])
                         ["metrics"]["total"]["complete"])

    def test_clean_session_without_auxiliary_entries_is_a_known_zero(self):
        self.write_session([session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01)))])
        auxiliary = self.summarize([assistant_event(101000, usage(1, 0, 0, 1, 2, 0.01))])["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["entries"], 0)
        self.assertTrue(auxiliary["reliable"])
        self.assertTrue(auxiliary["complete"])
        self.assertEqual(auxiliary["uncachedInput"], {"value": 0.0, "label": "exact",
                                                      "samples": 0, "invalid": 0,
                                                      "complete": True})
        self.assertEqual(auxiliary["reportedCostUsd"]["value"], 0.0)
        self.assertEqual(self.summarize([assistant_event(101000, usage(1, 0, 0, 1, 2, 0.01))])
                         ["metrics"]["total"]["reportedCostUsd"], 0.01)

    def test_missing_session_source_is_unknown_not_zero(self):
        self.write_session([session_entry("c1", 105.0, "compaction", summary="s",
                                          firstKeptEntryId="x", tokensBefore=1,
                                          usage=usage(10, 0, 0, 5, 15, 0.5))])
        data = self.summarize(session_dir=self.tmp / "absent-session")
        auxiliary = data["metrics"]["auxiliary"]
        self.assertFalse(auxiliary["reliable"])
        self.assertEqual(auxiliary["status"], "unknown")
        self.assertIsNone(auxiliary["uncachedInput"]["value"])
        self.assertIsNone(auxiliary["reportedCostUsd"]["value"])
        self.assertFalse(auxiliary["complete"])
        self.assertIsNone(data["auxiliary_usage"]["reported_cost_usd"])
        self.assertFalse(data["metrics"]["total"]["complete"])

    def test_failed_or_unfinished_compaction_signal_blocks_a_definite_zero(self):
        events = [
            {"type": "compaction_start", "reason": "manual"},
            {"type": "compaction_end", "reason": "manual", "result": None, "aborted": False,
             "errorMessage": "Compaction failed: synthetic", "willRetry": False},
            {"type": "message_end", "message": assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))},
        ]
        self.write_session([session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01)))])
        auxiliary = self.summarize(events)["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["failureSignals"]["compactionFailed"], 1)
        self.assertEqual(auxiliary["entries"], 0)
        self.assertIsNone(auxiliary["uncachedInput"]["value"])
        self.assertIsNone(auxiliary["reportedCostUsd"]["value"])
        self.assertFalse(auxiliary["complete"])
        self.assertEqual(auxiliary["reason"], "compaction_attempt_without_success_entry")
        self.assertFalse(self.summarize(events)["metrics"]["total"]["complete"])

    def test_unfinished_compaction_start_alone_is_not_zero(self):
        events = [{"type": "compaction_start", "reason": "threshold"}]
        self.write_session([session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01)))])
        auxiliary = self.summarize(events)["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["failureSignals"]["compactionUnfinished"], 1)
        self.assertIsNone(auxiliary["uncachedInput"]["value"])
        self.assertFalse(auxiliary["complete"])

    def test_failure_signal_with_successful_entry_keeps_known_sum_incomplete(self):
        events = [
            {"type": "compaction_start", "reason": "manual"},
            {"type": "compaction_end", "reason": "manual", "result": None, "aborted": True,
             "willRetry": False},
            {"type": "message_end", "message": assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))},
        ]
        self.write_session([
            session_entry("c1", 105.0, "compaction", summary="s", firstKeptEntryId="x",
                          tokensBefore=1, usage=usage(10, 0, 0, 5, 15, 0.5)),
            session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))),
        ])
        auxiliary = self.summarize(events)["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["uncachedInput"]["value"], 10, "known sum stays visible")
        self.assertFalse(auxiliary["uncachedInput"]["complete"])
        self.assertFalse(auxiliary["complete"])
        self.assertEqual(auxiliary["reason"], "compaction_failure_or_incomplete_signals")

    def test_success_event_without_session_entry_is_incomplete_not_zero(self):
        events = [
            {"type": "compaction_start", "reason": "manual"},
            {"type": "compaction_end", "reason": "manual", "aborted": False, "willRetry": False,
             "result": {"summary": "s", "usage": usage(10, 0, 0, 5, 15, 0.5)}},
            {"type": "message_end", "message": assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))},
        ]
        self.write_session([session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01)))])
        auxiliary = self.summarize(events)["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["reportedCompactionsWithoutEntry"], 1)
        self.assertIsNone(auxiliary["uncachedInput"]["value"])
        self.assertFalse(auxiliary["complete"])
        self.assertEqual(auxiliary["reason"], "compaction_summary_without_session_entry")

    def test_total_is_complete_only_when_both_sides_are(self):
        self.write_session([
            session_entry("c1", 105.0, "compaction", summary="s", firstKeptEntryId="x",
                          tokensBefore=1, usage=usage(10, 0, 0, 5, 15, 0.5)),
            session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01))),
        ])
        total = self.summarize([assistant_event(101000, usage(1, 0, 0, 1, 2, 0.01))])["metrics"]["total"]
        self.assertTrue(total["assistantComplete"])
        self.assertTrue(total["auxiliaryComplete"])
        self.assertTrue(total["complete"])
        self.assertEqual(total["usage"]["output"], 6)
        self.assertEqual(total["reportedCostUsd"], 0.51)

    def test_duplicate_session_identity_is_ambiguous_and_unknown(self):
        first = self.write_session([session_entry("c1", 105.0, "compaction", summary="s",
                                                  firstKeptEntryId="x", tokensBefore=1,
                                                  usage=usage(10, 0, 0, 5, 15, 0.5))])
        second = self.session_dir / "2026-02-01T00-00-00-000Z_sid.jsonl"
        second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
        auxiliary = self.summarize()["metrics"]["auxiliary"]
        self.assertEqual(auxiliary["status"], "unknown")
        self.assertEqual(auxiliary["reason"], "session_identity_ambiguous")
        self.assertIsNone(auxiliary["uncachedInput"]["value"])

    def test_bounded_output_exposes_auxiliary_and_total_completeness(self):
        self.write_session([session_entry("a1", 104.0, message=assistant(101000, usage=usage(1, 0, 0, 1, 2, 0.01)))])
        bounded = pi_summary.bounded(self.summarize([assistant_event(101000, usage(1, 0, 0, 1, 2, 0.01))]))
        self.assertTrue(bounded["auxiliary_usage"]["complete"])
        self.assertTrue(bounded["total_usage_complete"])


def auxiliary_leaf(value, complete=True):
    return {"value": value, "label": "exact" if complete else "estimated",
            "samples": 1, "complete": complete}


def auxiliary_section(complete=True, input=10, cost=0.5, entries=1):
    return {"status": "complete" if complete else "partial", "complete": complete,
            "reliable": True, "entries": entries,
            "kinds": {"compaction": entries, "branch_summary": 0, "usage": 0},
            "duplicateEntries": 0, "identityMissingEntries": 0, "usageMissingEntries": 0,
            "costMissingEntries": 0, "successfulCompactionEntries": entries,
            "reportedCompactionsWithoutEntry": 0,
            "failureSignals": {"compactionStarted": entries, "compactionSucceeded": entries,
                               "compactionFailed": 0, "compactionAborted": 0,
                               "compactionUnfinished": 0},
            "uncachedInput": auxiliary_leaf(input, complete), "cacheRead": auxiliary_leaf(0, complete),
            "cacheWrite": auxiliary_leaf(0, complete), "output": auxiliary_leaf(5, complete),
            "totalTokens": auxiliary_leaf(15, complete),
            "reportedCostUsd": auxiliary_leaf(cost, complete), "reason": None}


def stored_summary(auxiliary=None) -> dict:
    metrics = {"usage": {"uncachedInput": auxiliary_leaf(1)},
               "context": {}, "checks": {}, "timing": {}}
    if auxiliary is not None:
        metrics["auxiliary"] = auxiliary
    return {"usage": {"input": 1, "cacheRead": 0, "cacheWrite": 0, "output": 1,
                      "totalTokens": 2}, "usage_complete": True,
            "reported_cost_usd": 0.01, "metrics": metrics}


class BoardAuxiliaryAggregationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-board-aux-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.board_file = self.tmp / "board.json"
        self.task_dir = self.tmp / "tasks" / "T"

    def write_summary(self, number: int, data: dict):
        directory = self.task_dir / "rounds" / str(number)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "round.summary.json").write_text(json.dumps(data), encoding="utf-8")

    def test_auxiliary_aggregation_reads_stored_summaries_only(self):
        self.write_summary(1, stored_summary(auxiliary_section(complete=True, input=10, cost=0.5)))
        self.write_summary(2, stored_summary())  # old summary without auxiliary metrics
        with mock.patch.object(pi_summary, "derive_session_metrics",
                               side_effect=AssertionError("board refresh must not scan sessions")), \
                mock.patch.object(pi_task, "summarize",
                                  side_effect=AssertionError("board refresh must not rebuild summaries")):
            record = pi_board.task_metrics(self.board_file, None, "T")
        derived = record["derivedMetrics"]
        self.assertEqual(record["auxiliaryUsage"]["known"]["uncachedInput"], 10)
        self.assertFalse(record["auxiliaryUsage"]["complete"])
        self.assertEqual(record["auxiliaryUsage"]["roundsComplete"], [1])
        self.assertEqual(record["auxiliaryUsage"]["roundsUnknown"], [2])
        aggregated = derived["auxiliary"]["uncachedInput"]
        self.assertEqual(aggregated["known"], 10)
        self.assertEqual(aggregated["roundsKnown"], [1])
        self.assertEqual(aggregated["roundsUnknown"], [2])
        self.assertFalse(aggregated["complete"])
        self.assertEqual(derived["auxiliary"]["reportedCostUsd"]["known"], 0.5)
        self.assertFalse(derived["totalComplete"])

    def test_two_complete_rounds_make_the_total_complete(self):
        self.write_summary(1, stored_summary(auxiliary_section(complete=True, input=10, cost=0.5)))
        self.write_summary(2, stored_summary(auxiliary_section(complete=True, input=4, cost=0.25)))
        record = pi_board.task_metrics(self.board_file, None, "T")
        derived = record["derivedMetrics"]
        self.assertTrue(record["auxiliaryUsage"]["complete"])
        self.assertEqual(record["auxiliaryUsage"]["known"]["uncachedInput"], 14)
        self.assertEqual(record["auxiliaryUsage"]["known"]["reportedCostUsd"], 0.75)
        self.assertEqual(derived["auxiliary"]["uncachedInput"]["known"], 14)
        self.assertTrue(derived["auxiliary"]["uncachedInput"]["complete"])
        self.assertTrue(derived["auxiliary"]["reportedCostUsd"]["complete"])
        self.assertTrue(derived["totalComplete"])

    def test_incomplete_auxiliary_round_keeps_known_sum_and_blocks_total(self):
        self.write_summary(1, stored_summary(auxiliary_section(complete=False, input=10, cost=0.5)))
        derived = pi_board.task_metrics(self.board_file, None, "T")["derivedMetrics"]
        self.assertEqual(derived["auxiliary"]["uncachedInput"]["known"], 10)
        self.assertEqual(derived["auxiliary"]["uncachedInput"]["roundsIncomplete"], [1])
        self.assertFalse(derived["auxiliary"]["uncachedInput"]["complete"])
        self.assertFalse(derived["totalComplete"])

    def test_failure_signal_counts_are_aggregated(self):
        section = auxiliary_section(complete=False, input=10, cost=0.5)
        section["failureSignals"] = {"compactionStarted": 1, "compactionSucceeded": 0,
                                     "compactionFailed": 1, "compactionAborted": 1,
                                     "compactionUnfinished": 1}
        self.write_summary(1, stored_summary(section))
        record = pi_board.task_metrics(self.board_file, None, "T")
        counts = record["auxiliaryUsage"]["counts"]
        self.assertEqual(counts["signal_compactionFailed"], 1)
        self.assertEqual(counts["signal_compactionAborted"], 1)
        self.assertEqual(counts["signal_compactionUnfinished"], 1)
        self.assertEqual(counts["kind_compaction"], 1)

    def test_non_finite_stored_leaf_never_enters_the_known_sum(self):
        section = auxiliary_section(complete=True, input=10, cost=0.5)
        section["uncachedInput"] = {"value": float("nan"), "label": "exact",
                                    "samples": 1, "complete": True}
        section["invalidFields"] = {"input": 2}
        section["identityConflicts"] = 1
        self.write_summary(1, stored_summary(section))
        record = pi_board.task_metrics(self.board_file, None, "T")
        derived = record["derivedMetrics"]
        self.assertIsNone(derived["auxiliary"]["uncachedInput"]["known"])
        self.assertEqual(derived["auxiliary"]["uncachedInput"]["roundsUnknown"], [1])
        self.assertFalse(derived["auxiliary"]["uncachedInput"]["complete"])
        self.assertEqual(derived["auxiliary"]["output"]["known"], 5)
        self.assertEqual(record["auxiliaryUsage"]["counts"]["invalid_input"], 2)
        self.assertEqual(record["auxiliaryUsage"]["counts"]["identityConflicts"], 1)
        self.assertFalse(record["auxiliaryUsage"]["complete"])
        self.assertFalse(derived["totalComplete"])

    def test_unverified_signal_count_is_visible(self):
        section = auxiliary_section(complete=False, input=10, cost=0.5)
        section["signalsVerified"] = False
        self.write_summary(1, stored_summary(section))
        counts = pi_board.task_metrics(self.board_file, None, "T")["auxiliaryUsage"]["counts"]
        self.assertEqual(counts["signalsUnverified"], 1)


class VersionStillReads(unittest.TestCase):
    def test_context_workflow_keeps_release_version(self):
        self.assertEqual((RUNTIME / "VERSION").read_text().strip(), "0.8.8")


if __name__ == "__main__":
    unittest.main()
