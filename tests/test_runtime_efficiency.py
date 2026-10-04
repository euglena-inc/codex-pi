"""Behavioral tests for streaming check-log analysis and derived round metrics.

Expected facts are computed independently here: the hash is ``hashlib`` over the
full bytes, the tail follows the documented last-20-lines/2000-byte contract in
a local reimplementation, and timing unions come from explicit interval lists.
The runtime functions under test are never used as the oracle.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from runtime_helpers import RUNTIME

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402
import pi_check  # noqa: E402
import pi_summary  # noqa: E402
import pi_task  # noqa: E402

CHECK = RUNTIME / "pi_check.py"
ROOT = Path(__file__).resolve().parent.parent


def naive_tail(raw: bytes, max_lines: int = 20, max_bytes: int = 2000) -> str:
    """Independent reimplementation of the documented tail contract."""
    chunk = raw[-max_bytes:]
    start = 0
    while start < len(chunk) and 0x80 <= chunk[start] <= 0xBF and start < 4:
        start += 1
    lines = chunk[start:].decode("utf-8", errors="replace").splitlines()[-max_lines:]
    text = "\n".join(lines)
    encoded = text.encode("utf-8")
    while len(encoded) > max_bytes:
        text = text[1:]
        encoded = text.encode("utf-8")
    return text


def analyze_bytes(raw: bytes, **kwargs) -> dict:
    with tempfile.TemporaryDirectory(prefix="codex-pi-stream-") as tmp:
        path = Path(tmp) / "check.log"
        path.write_bytes(raw)
        return pi_check.analyze_log(path, **kwargs)


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def session_entry(entry_id: str, outer: float, message: dict, parent=None) -> dict:
    return {"type": "message", "id": entry_id, "parentId": parent,
            "timestamp": iso(outer), "message": message}


def assistant(inner_ms: int, calls=(), usage=None) -> dict:
    return {"role": "assistant", "provider": "p", "model": "m", "timestamp": inner_ms,
            "stopReason": "toolUse" if calls else "stop", "usage": usage or {},
            "content": [{"type": "toolCall", "id": call_id, "name": "bash"} for call_id in calls]}


def tool_result(inner_ms: int, call_id: str) -> dict:
    return {"role": "toolResult", "toolCallId": call_id, "toolName": "bash",
            "timestamp": inner_ms, "content": [], "isError": False}


_DEFAULT = object()


class StreamingAnalyzerTest(unittest.TestCase):
    def test_hash_counts_and_tail_match_independent_facts(self):
        raw = ("=== RUN TestA\n--- PASS: TestA\n"
               "é中🙂 payload\n"
               "--- SKIP: TestB\n").encode("utf-8")
        result = analyze_bytes(raw)
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["bytes"], len(raw))
        self.assertEqual(result["test_counts"], {"run": 1, "pass": 1, "fail": 0, "skip": 1,
                                                 "format": "go_verbose_top_level"})
        self.assertEqual(result["tail"], naive_tail(raw))

    def test_chunk_boundaries_never_change_the_result(self):
        raw = ("first line é\n=== RUN TestOne\n--- PASS: TestOne\n"
               "🙂 multibyte straddle é中\n"
               "Ran 5 tests in 0.123s\n\nOK (skipped=2)\n"
               "--- FAIL: TestTwo\n").encode("utf-8")
        reference = analyze_bytes(raw)
        for chunk_size in (1, 2, 3, 4, 5, 7, 16, 64, 4096, pi_check.STREAM_CHUNK_BYTES,
                           pi_check.STREAM_CHUNK_BYTES + 3):
            with self.subTest(chunk_size=chunk_size):
                self.assertEqual(analyze_bytes(raw, chunk_size=chunk_size), reference)
        self.assertEqual(reference["test_counts"],
                         {"run": 1, "pass": 1, "fail": 1, "skip": 0,
                          "format": "go_verbose_top_level"})

    def test_go_priority_and_final_unittest_candidate(self):
        unittest_only = b"noise\nRan 2 tests in 0.010s\n\nOK (skipped=1)\n"
        self.assertEqual(analyze_bytes(unittest_only)["test_counts"],
                         {"run": 2, "pass": 1, "fail": 0, "skip": 1,
                          "format": "python_unittest_summary"})
        mixed = unittest_only + b"=== RUN TestGo\n--- PASS: TestGo\n"
        self.assertEqual(analyze_bytes(mixed)["test_counts"],
                         {"run": 1, "pass": 1, "fail": 0, "skip": 0,
                          "format": "go_verbose_top_level"})
        last_ran = b"Ran 2 tests in 0.010s\nOK\n\nRan 7 tests in 0.020s\n\nOK\n"
        self.assertEqual(analyze_bytes(last_ran)["test_counts"],
                         {"run": 7, "pass": 7, "fail": 0, "skip": 0,
                          "format": "python_unittest_summary"})
        dangling = b"Ran 7 tests in 0.020s\n\n\n"
        self.assertIsNone(analyze_bytes(dangling)["test_counts"])
        contradictory = b"Ran 2 tests in 0.010s\nOK (skipped=oops)\n"
        self.assertIsNone(analyze_bytes(contradictory)["test_counts"])

    def test_overlong_line_stays_unknown_and_never_reanchors(self):
        raw = (b"Ran 2 tests in 0.010s\nOK\n"
               + b"z" * (pi_check.MAX_LINE_CHARS + 10) + b"--- PASS: Hidden\n"
               + b"--- PASS: Visible\n")
        result = analyze_bytes(raw)
        self.assertTrue(result["overlong"])
        self.assertIsNone(result["test_counts"],
                          "a dropped over-long prefix must not re-anchor later markers")
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        final_overlong = analyze_bytes(b"--- PASS: Visible\n" + b"q" * (pi_check.MAX_LINE_CHARS + 1))
        self.assertIsNone(final_overlong["test_counts"])

    def test_missing_empty_and_invalid_utf8_logs(self):
        empty_hash = hashlib.sha256(b"").hexdigest()
        with tempfile.TemporaryDirectory(prefix="codex-pi-stream-") as tmp:
            missing = pi_check.analyze_log(Path(tmp) / "absent.log")
        self.assertEqual(missing["sha256"], empty_hash)
        self.assertEqual(missing["bytes"], 0)
        self.assertIsNone(missing["test_counts"])
        self.assertEqual(missing["tail"], "")
        self.assertEqual(analyze_bytes(b"")["sha256"], empty_hash)
        raw = b"\xff\xfe bad\nRan 3 tests in 0.010s\n\nOK\n"
        result = analyze_bytes(raw)
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["test_counts"]["run"], 3)
        self.assertEqual(result["tail"], naive_tail(raw))
        self.assertIn("bad", result["tail"])

    def test_tail_reuses_last_bytes_and_respects_caps(self):
        raw = (b"".join(f"line {index:03d} é\n".encode("utf-8") for index in range(60))
               + b"x" * 150 + b"\n"
               + "é".encode("utf-8") * 900 + b"\n")
        result = analyze_bytes(raw)
        self.assertLessEqual(len(result["tail"].encode("utf-8")), 2000)
        self.assertLessEqual(len(result["tail"].splitlines()), 20)
        self.assertEqual(result["tail"], naive_tail(raw))

    def test_line_boundary_edge_cases_match_documented_semantics(self):
        cases = [
            (b"=== RUN\n", {"run": 1, "pass": 0, "fail": 0, "skip": 0,
                            "format": "go_verbose_top_level"}),
            (b"=== RUN", None),
            (b"--- PASS:\n", {"run": 0, "pass": 1, "fail": 0, "skip": 0,
                             "format": "go_verbose_top_level"}),
            (b"a\r--- PASS: x\n", None),
            (b"\v=== RUN Test\n", None),
            (b"Ran 2 tests in 0.010s\vOK\n",
             {"run": 2, "pass": 2, "fail": 0, "skip": 0,
              "format": "python_unittest_summary"}),
            (b"Ran 2 tests in 0.010s\rOK\n",
             {"run": 2, "pass": 2, "fail": 0, "skip": 0,
              "format": "python_unittest_summary"}),
            (b"--- SKIP: x", {"run": 0, "pass": 0, "fail": 0, "skip": 1,
                             "format": "go_verbose_top_level"}),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(analyze_bytes(raw)["test_counts"], expected)

    def test_compat_helpers_share_the_streaming_algorithm(self):
        text = "Ran 2 tests in 0.010s\n\nOK (skipped=1)\n"
        self.assertEqual(pi_check.unittest_counts(text),
                         {"run": 2, "pass": 1, "fail": 0, "skip": 1,
                          "format": "python_unittest_summary"})
        self.assertIsNone(pi_check.unittest_counts("x" * (pi_check.MAX_LINE_CHARS + 1)))
        raw = b"last line\n" + "é".encode("utf-8") * 700
        self.assertEqual(pi_check.log_tail(raw), naive_tail(raw))

    def test_pi_check_receipt_binds_streaming_hash_and_true_tail(self):
        with tempfile.TemporaryDirectory(prefix="codex-pi-stream-") as tmp:
            work = Path(tmp)
            checks = work / "checks"
            checks.mkdir()
            code = ("import sys\n"
                    "for i in range(40): print('line %03d é中🙂' % i)\n"
                    "sys.exit(3)")
            proc = subprocess.run([sys.executable, str(CHECK), "--output-dir", str(checks),
                                   "--id", "stream", "--", sys.executable, "-c", code],
                                  cwd=str(work), capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 3, proc.stderr)
            payload = json.loads(proc.stdout)
            receipt = json.loads(Path(payload["receipt"]).read_text(encoding="utf-8"))
            log_path = checks / receipt["log"]
            raw = log_path.read_bytes()
            self.assertEqual(receipt["log_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(payload["log_tail"], naive_tail(raw))
            self.assertIsNone(receipt["test_counts"])

    def test_unreadable_existing_log_fails_instead_of_emptying(self):
        self.assertNotEqual(os.geteuid(), 0, "permission negatives need a non-root uid")
        with tempfile.TemporaryDirectory(prefix="codex-pi-stream-") as tmp:
            path = Path(tmp) / "check.log"
            path.write_bytes(b"=== RUN TestA\n")
            original = path.read_bytes()
            path.chmod(0)
            try:
                with self.assertRaises(OSError):
                    pi_check.analyze_log(path)
            finally:
                path.chmod(0o600)
            self.assertEqual(path.read_bytes(), original, "the original log stays untouched")

    def test_mid_read_failure_is_not_an_empty_log(self):
        class FailingStream:
            def __init__(self, data: bytes):
                self.data, self.calls = data, 0

            def read(self, size: int = -1) -> bytes:
                self.calls += 1
                if self.calls == 1:
                    return self.data
                raise OSError("simulated mid-read failure")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(Path, "open", return_value=FailingStream(b"partial\n")):
            with self.assertRaises(OSError):
                pi_check.analyze_log(Path("/ignored/not-used.log"))

    def test_pi_check_permission_failure_writes_no_receipt(self):
        with tempfile.TemporaryDirectory(prefix="codex-pi-stream-") as tmp:
            work = Path(tmp)
            checks = work / "checks"
            checks.mkdir()
            proc = subprocess.run(
                [sys.executable, str(CHECK), "--output-dir", str(checks), "--id", "noperm",
                 "--", "sh", "-c", 'chmod 000 "$1"/*.log', "sh", str(checks)],
                cwd=str(work), capture_output=True, text=True, timeout=60)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.strip(), "", "no normal-looking receipt JSON on stdout")
            self.assertIn("Permission", proc.stderr)
            self.assertEqual(list(checks.glob("*.json")), [])
            self.assertEqual(list(checks.glob("*.running")), [])
            logs = list(checks.glob("*.log"))
            self.assertEqual(len(logs), 1, "the original log file is retained")
            logs[0].chmod(0o600)
            self.assertEqual(logs[0].read_bytes(), b"")


class TimingCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-timing-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.round_dir = self.tmp / "rounds" / "1"
        self.round_dir.mkdir(parents=True)
        self.session_dir = self.tmp / "session"
        self.session_dir.mkdir()
        self.window = (100.0, 130.0)

    def write_session(self, entries):
        lines = [{"type": "session", "version": 3, "id": "sid",
                  "timestamp": "1970-01-01T00:00:00.000Z", "cwd": "/tmp"}] + entries
        (self.session_dir / "2026-01-01T00-00-00-000Z_sid.jsonl").write_text(
            "\n".join(json.dumps(entry) for entry in lines) + "\n", encoding="utf-8")

    def summarize(self, events=(), window=_DEFAULT, session_dir=None, session_id="sid", **kwargs):
        log = self.round_dir / "round.jsonl"
        log.write_text("\n".join(json.dumps(event) for event in events)
                       + ("\n" if events else ""), encoding="utf-8")
        return pi_summary.summarize(
            log, self.tmp, self.round_dir, "p/m",
            checks_dir=self.round_dir / "round.checks",
            session_dir=self.session_dir if session_dir is None else session_dir,
            session_id=session_id, window=self.window if window is _DEFAULT else window, **kwargs)


class TimingMetricsTest(TimingCase):
    def test_tool_and_model_intervals_are_unioned_and_window_filtered(self):
        entries = [
            session_entry("before", 90.0, assistant(88000)),
            session_entry("a1", 105.0, assistant(101000, calls=("c1",))),
            session_entry("t1", 106.1, tool_result(106000, "c1"), parent="a1"),
            session_entry("a2", 112.0, assistant(109000)),
            session_entry("a3", 120.0, assistant(116000, calls=("c2", "c3"))),
            session_entry("t2", 121.1, tool_result(121000, "c2"), parent="a3"),
            session_entry("t3", 121.6, tool_result(121500, "c3"), parent="a3"),
            session_entry("a1", 105.0, assistant(101000, calls=("c1",))),  # duplicate entry id
            session_entry("after", 200.0, assistant(150000)),
        ]
        self.write_session(entries)
        data = self.summarize()
        timing = data["metrics"]["timing"]
        self.assertEqual(timing["status"], "ok")
        self.assertEqual(timing["label"], "estimated")
        self.assertEqual(timing["windowSeconds"], {"value": 30.0, "label": "exact",
                                                    "source": "round state startedAt/endedAt",
                                                    "complete": True})
        self.assertEqual(timing["modelResponseSeconds"]["value"], 11.0)
        self.assertEqual(timing["modelResponseSeconds"]["samples"], 3)
        self.assertTrue(timing["modelResponseSeconds"]["complete"])
        self.assertEqual(timing["toolSeconds"]["value"], 2.5,
                         "concurrent tool intervals must be unioned, not summed")
        self.assertEqual(timing["toolSeconds"]["samples"], 3)
        self.assertTrue(timing["toolSeconds"]["complete"])
        self.assertEqual(timing["coveredSeconds"]["value"], 13.5)
        self.assertEqual(timing["unattributedSeconds"]["value"], 16.5)
        coverage = timing["coverage"]
        self.assertEqual(coverage["entriesInWindow"], 6)
        self.assertEqual(coverage["duplicateEntries"], 1)
        self.assertEqual(coverage["outOfWindowEntries"], 2)
        self.assertEqual(coverage["openToolIntervals"], 0)

    def test_no_tool_calls_in_a_clean_session_is_a_verified_zero(self):
        self.write_session([session_entry("a1", 105.0, assistant(101000))])
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["status"], "ok")
        self.assertEqual(timing["toolSeconds"]["value"], 0.0)
        self.assertEqual(timing["toolSeconds"]["label"], "estimated")
        self.assertTrue(timing["toolSeconds"]["complete"])
        self.assertEqual(timing["coverage"]["parseClean"], True)

    def test_orphan_tool_result_blocks_completeness(self):
        self.write_session([
            session_entry("a1", 105.0, assistant(101000)),
            session_entry("t1", 106.0, tool_result(105500, "missing"), parent="a1"),
        ])
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["status"], "partial")
        self.assertIsNone(timing["toolSeconds"]["value"],
                          "no measured tool interval must not become a known zero")
        self.assertFalse(timing["toolSeconds"]["complete"],
                         "an unmatched tool result means a tool was never measured")
        self.assertEqual(timing["toolSeconds"]["orphanResults"], 1)
        self.assertEqual(timing["coverage"]["toolComplete"], False)
        self.assertIsNone(timing["coveredSeconds"]["value"])
        self.assertIsNone(timing["unattributedSeconds"]["value"])

    def test_open_tool_intervals_use_union_not_sum(self):
        self.write_session([session_entry("a1", 105.0, assistant(101000, calls=("c1", "c2")))])
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["toolSeconds"]["openIntervals"], 2)
        self.assertEqual(timing["coverage"]["openToolSeconds"], 25.0,
                         "two concurrent open calls union to one [105, 130] interval")
        self.assertFalse(timing["toolSeconds"]["complete"])

    def test_session_header_identity_not_filename_order(self):
        header = json.dumps({"type": "session", "version": 3, "id": "sid",
                             "timestamp": "1970-01-01T00:00:00.000Z", "cwd": "/tmp"})
        other_header = json.dumps({"type": "session", "version": 3, "id": "other",
                                   "timestamp": "1970-01-01T00:00:00.000Z", "cwd": "/tmp"})
        entry = json.dumps(session_entry("a1", 105.0, assistant(101000)))
        (self.session_dir / "2026-01-01T00-00-00-000Z_sid.jsonl").write_text(
            header + "\n" + entry + "\n", encoding="utf-8")
        (self.session_dir / "2026-02-01T00-00-00-000Z_sid.jsonl").write_text(
            other_header + "\n" + entry + "\n", encoding="utf-8")
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["status"], "ok", "only the header-verified file is authoritative")
        self.assertEqual(timing["modelResponseSeconds"]["value"], 4.0)
        self.assertEqual(timing["coverage"]["sessionCandidates"], 2)
        # Two files both claiming the id are ambiguous, never a filename guess.
        (self.session_dir / "2026-02-01T00-00-00-000Z_sid.jsonl").write_text(
            header + "\n" + entry + "\n", encoding="utf-8")
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["reason"], "session_identity_ambiguous")
        self.assertIsNone(timing["modelResponseSeconds"]["value"])

    def test_multi_round_window_does_not_mix(self):
        self.write_session([
            session_entry("old-a", 50.0, assistant(40000, calls=("c1",))),
            session_entry("old-t", 51.0, tool_result(40500, "c1"), parent="old-a"),
            session_entry("new-a", 105.0, assistant(101000, calls=("c1",))),
            session_entry("new-t", 106.5, tool_result(106000, "c1"), parent="new-a"),
        ])
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["modelResponseSeconds"]["value"], 4.0)
        self.assertEqual(timing["toolSeconds"]["value"], 1.0)
        self.assertTrue(timing["toolSeconds"]["complete"])
        self.assertEqual(timing["coverage"]["outOfWindowEntries"], 2)

    def test_other_session_dir_is_not_read(self):
        other = self.tmp / "other-session"
        other.mkdir()
        header = json.dumps({"type": "session", "version": 3, "id": "sid",
                             "timestamp": "1970-01-01T00:00:00.000Z", "cwd": "/tmp"})
        entry = json.dumps(session_entry("a1", 105.0, assistant(101000)))
        (other / "2026-01-01T00-00-00-000Z_sid.jsonl").write_text(
            header + "\n" + entry + "\n", encoding="utf-8")
        self.write_session([])
        timing = self.summarize(window=(100.0, 130.0))["metrics"]["timing"]
        self.assertEqual(timing["reason"], "no_entries_in_window")
        self.assertIsNone(timing["modelResponseSeconds"]["value"])

    def test_partial_tail_open_interval_and_bad_timestamps_are_explicit(self):
        entries = [
            session_entry("a1", 105.0, assistant(101000, calls=("c1", "c2"))),
            session_entry("t1", 106.1, tool_result(106000, "c1"), parent="a1"),
            session_entry("bad", 107.0, {"role": "assistant", "timestamp": "later"}),
            {"type": "message", "id": "broken", "parentId": None,
             "timestamp": "not-a-time", "message": assistant(108000)},
            session_entry("t2", 120.0, tool_result(119000, "orphan"), parent="a1"),
        ]
        self.write_session(entries)
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["status"], "partial")
        self.assertEqual(timing["modelResponseSeconds"]["value"], 4.0)
        self.assertFalse(timing["modelResponseSeconds"]["complete"],
                         "a bad session line must not look like complete model coverage")
        self.assertEqual(timing["toolSeconds"]["value"], 1.0)
        self.assertFalse(timing["toolSeconds"]["complete"])
        self.assertEqual(timing["toolSeconds"]["openIntervals"], 1)
        self.assertIsNotNone(timing["toolSeconds"]["reason"])
        self.assertEqual(timing["coverage"]["openToolIntervals"], 1)
        self.assertEqual(timing["coverage"]["toolResultsWithoutCall"], 1)
        self.assertGreaterEqual(timing["coverage"]["invalidTimestamps"], 2)

    def test_missing_session_and_window_return_null_not_zero(self):
        missing_source = self.summarize(session_dir=False)
        timing = missing_source["metrics"]["timing"]
        self.assertEqual(timing["reason"], "no_session_source")
        for key in ("modelResponseSeconds", "toolSeconds", "coveredSeconds", "unattributedSeconds"):
            self.assertIsNone(timing[key]["value"], key)
            self.assertEqual(timing[key]["label"], "unknown", key)
        no_window = self.summarize(window=None)
        self.assertEqual(no_window["metrics"]["timing"]["reason"], "round_window_missing")
        self.assertIsNone(no_window["metrics"]["timing"]["modelResponseSeconds"]["value"])
        empty_dir = self.tmp / "empty-session"
        empty_dir.mkdir()
        missing_file = self.summarize(session_dir=empty_dir)
        self.assertEqual(missing_file["metrics"]["timing"]["reason"], "session_file_missing")
        self.assertIsNone(missing_file["metrics"]["timing"]["toolSeconds"]["value"])

    def test_no_entries_in_window_is_unknown(self):
        self.write_session([session_entry("old", 50.0, assistant(40000))])
        timing = self.summarize()["metrics"]["timing"]
        self.assertEqual(timing["reason"], "no_entries_in_window")
        self.assertIsNone(timing["modelResponseSeconds"]["value"])


class UsageMetricsTest(TimingCase):
    def test_unique_final_usage_reasoning_subitem_and_cache_ratio(self):
        first = {"role": "assistant", "provider": "p", "model": "m", "timestamp": 1000,
                 "stopReason": "toolUse", "responseId": "first",
                 "usage": {"input": 10, "cacheRead": 90, "cacheWrite": 5, "output": 20,
                           "reasoning": 7, "totalTokens": 125, "cost": {"total": 0.1}},
                 "content": []}
        second = {"role": "assistant", "provider": "p", "model": "m", "timestamp": 2000,
                  "stopReason": "stop", "responseId": "second",
                  "usage": {"input": 3, "cacheRead": 7, "cacheWrite": 0, "output": 5,
                            "reasoning": 2, "totalTokens": 15, "cost": {"total": 0.2}},
                  "content": [{"type": "text", "text": "done"}]}
        events = [
            {"type": "agent_end", "messages": [first, second]},
            {"type": "turn_end", "message": first, "toolResults": []},
            {"type": "message_end", "message": first},
            {"type": "message_end", "message": second},
            {"type": "message_end", "message": first},  # duplicate delivery
        ]
        data = self.summarize(events)
        usage = data["metrics"]["usage"]
        self.assertEqual(usage["uncachedInput"]["value"], 13)
        self.assertEqual(usage["cacheRead"]["value"], 97)
        self.assertEqual(usage["cacheWrite"]["value"], 5)
        self.assertEqual(usage["output"]["value"], 25,
                         "reasoning is a sub-item of output and is never added again")
        self.assertEqual(usage["reasoning"]["value"], 9.0)
        self.assertEqual(usage["totalTokens"]["value"], 140)
        self.assertAlmostEqual(usage["cacheRatio"]["value"], 97 / 110)
        self.assertEqual(usage["cacheRatio"]["label"], "exact")
        self.assertEqual(usage["messages"], 2)
        self.assertEqual(usage["duplicateMessages"], 1)
        self.assertTrue(data["usage_complete"],
                        "explicit duplicate delivery does not remove usage coverage")
        context = data["metrics"]["context"]
        self.assertEqual(context["first"]["value"], 105.0)
        self.assertEqual(context["last"]["value"], 10.0)
        self.assertEqual(context["peak"]["value"], 105.0)
        self.assertEqual(context["samples"], 2)

    def test_unique_usage_identity_and_unified_unique_count(self):
        first = {"role": "assistant", "provider": "p", "model": "m", "timestamp": 1000,
                 "stopReason": "stop", "responseId": "first",
                 "usage": {"input": 1, "cacheRead": 0, "output": 1, "totalTokens": 2},
                 "content": []}
        second = {"role": "assistant", "provider": "p", "model": "m", "timestamp": 2000,
                  "stopReason": "stop",
                  "usage": {"input": 2, "cacheRead": 0, "output": 2, "totalTokens": 4},
                  "content": []}
        no_stamp = {key: value for key, value in second.items() if key != "timestamp"}
        events = [{"type": "message_end", "message": message}
                  for message in (first, first, second, no_stamp)]
        usage = self.summarize(events)["metrics"]["usage"]
        self.assertEqual(usage["messages"], 3,
                         "a message with no identity is still counted exactly once")
        self.assertEqual(usage["duplicateMessages"], 1)
        self.assertEqual(usage["heuristicDuplicates"], 0)
        self.assertEqual(usage["identity"], "unavailable")
        self.assertFalse(usage["identityReliable"])
        self.assertEqual(usage["uncachedInput"]["value"], 5)
        self.assertTrue(usage["uncachedInput"]["complete"])

    def test_response_id_identity_is_reliable(self):
        message = {"role": "assistant", "provider": "p", "model": "m", "timestamp": 1000,
                   "responseId": "resp-1", "stopReason": "stop",
                   "usage": {"input": 1, "cacheRead": 0, "output": 1, "totalTokens": 2},
                   "content": []}
        shifted = dict(message, timestamp=9999)
        usage = self.summarize([{"type": "message_end", "message": entry}
                                for entry in (message, shifted)])["metrics"]["usage"]
        self.assertEqual(usage["messages"], 1)
        self.assertEqual(usage["duplicateMessages"], 1)
        self.assertEqual(usage["identity"], "response_id")
        self.assertTrue(usage["identityReliable"])
        self.assertEqual(usage["heuristicDuplicates"], 0)

    def test_same_timestamp_never_merges_different_usage(self):
        def assistant_with(input_tokens):
            return {"role": "assistant", "provider": "p", "model": "m", "timestamp": 1000,
                    "stopReason": "stop",
                    "usage": {"input": input_tokens, "cacheRead": 0, "output": 0,
                              "totalTokens": input_tokens},
                    "content": []}
        distinct = self.summarize([{"type": "message_end", "message": assistant_with(1)},
                                   {"type": "message_end", "message": assistant_with(2)}])
        usage = distinct["metrics"]["usage"]
        self.assertEqual(usage["messages"], 2)
        self.assertEqual(usage["duplicateMessages"], 0)
        # Even indistinguishable records are not merged without response identity.
        merged = self.summarize([{"type": "message_end", "message": assistant_with(1)},
                                 {"type": "message_end", "message": assistant_with(1)}])
        usage = merged["metrics"]["usage"]
        self.assertEqual(usage["messages"], 2)
        self.assertEqual(usage["uncachedInput"]["value"], 2)
        self.assertEqual(usage["heuristicDuplicates"], 0)
        self.assertFalse(usage["identityReliable"])

    def test_same_timestamp_and_usage_with_different_text_are_both_counted(self):
        messages = [{"role": "assistant", "provider": "p", "model": "m", "timestamp": 1000,
                     "stopReason": "stop", "usage": {"input": 10, "cacheRead": 0,
                     "cacheWrite": 0, "output": 1, "totalTokens": 11},
                     "content": [{"type": "text", "text": text}]}
                    for text in ("first answer", "different answer")]
        data = self.summarize([{"type": "message_end", "message": m} for m in messages])
        self.assertEqual(data["usage"]["input"], 20)
        self.assertEqual(data["metrics"]["usage"]["messages"], 2)
        self.assertEqual(data["metrics"]["usage"]["duplicateMessages"], 0)

    def test_reasoning_and_context_unknown_when_usage_missing(self):
        events = [{"type": "message_end", "message": {
            "role": "assistant", "provider": "p", "model": "m", "timestamp": 1000,
            "stopReason": "stop", "usage": {}, "content": []}}]
        usage = self.summarize(events)["metrics"]["usage"]
        self.assertIsNone(usage["reasoning"]["value"])
        self.assertEqual(usage["reasoning"]["label"], "unknown")
        context = self.summarize(events)["metrics"]["context"]
        self.assertIsNone(context["peak"]["value"])
        self.assertEqual(context["peak"]["label"], "unknown")


class CheckMetricsTest(TimingCase):
    def write_receipt(self, name, **overrides):
        checks = self.round_dir / "round.checks"
        checks.mkdir(exist_ok=True)
        data = {"id": name, "argv": ["x"], "head": None, "dirty": False, "exit_code": 0,
                "timed_out": False, "cancelled": False, "started_at": 100.0, "ended_at": 102.5,
                "test_counts": {"run": 1, "pass": 1, "fail": 0, "skip": 0},
                "log": name + ".log", "log_sha256": hashlib.sha256(b"ok\n").hexdigest()}
        data.update(overrides)
        (checks / (name + ".log")).write_bytes(b"ok\n")
        (checks / (name + ".json")).write_text(json.dumps(data), encoding="utf-8")

    def test_interrupted_and_unknown_attempts_never_fabricate_elapsed(self):
        self.write_receipt("clean")
        self.write_receipt("timeout", timed_out=True, started_at=200.0, ended_at=260.0)
        checks = self.round_dir / "round.checks"
        (checks / "broken.json").write_text("{not json", encoding="utf-8")
        metrics = self.summarize()["metrics"]["checks"]
        self.assertEqual(metrics["attempts"], 3)
        self.assertEqual(metrics["completed"], 1)
        self.assertEqual(metrics["passed"], 1)
        self.assertEqual(metrics["failed"], 0)
        self.assertEqual(metrics["interrupted"], 1)
        self.assertEqual(metrics["unknown"], 1)
        self.assertEqual(metrics["elapsedSeconds"]["value"], 2.5)
        self.assertEqual(metrics["elapsedSeconds"]["samples"], 1)

    def test_failed_but_completed_check_counts_elapsed(self):
        self.write_receipt("failed", exit_code=1)
        metrics = self.summarize()["metrics"]["checks"]
        self.assertEqual(metrics["completed"], 1)
        self.assertEqual(metrics["failed"], 1)
        self.assertEqual(metrics["elapsedSeconds"]["value"], 2.5)
        self.assertTrue(metrics["elapsedSeconds"]["complete"])

    def test_zero_checks_is_a_complete_zero(self):
        metrics = self.summarize()["metrics"]["checks"]
        self.assertEqual(metrics["attempts"], 0)
        self.assertEqual(metrics["elapsedSeconds"]["value"], 0.0)
        self.assertTrue(metrics["elapsedSeconds"]["complete"])
        self.assertEqual(metrics["elapsedSeconds"]["missingTime"], 0)

    def test_missing_receipt_time_is_known_partial_not_complete(self):
        self.write_receipt("clean")
        self.write_receipt("notime", started_at=None, ended_at=None)
        metrics = self.summarize()["metrics"]["checks"]
        self.assertEqual(metrics["completed"], 2)
        self.assertEqual(metrics["elapsedSeconds"]["value"], 2.5)
        self.assertFalse(metrics["elapsedSeconds"]["complete"])
        self.assertEqual(metrics["elapsedSeconds"]["missingTime"], 1)
        self.assertEqual(metrics["elapsedSeconds"]["label"], "estimated")

    def test_all_interrupted_is_a_complete_zero(self):
        self.write_receipt("timeout", timed_out=True, started_at=200.0, ended_at=260.0)
        metrics = self.summarize()["metrics"]["checks"]
        self.assertEqual(metrics["attempts"], 1)
        self.assertEqual(metrics["interrupted"], 1)
        self.assertEqual(metrics["elapsedSeconds"]["value"], 0.0)
        self.assertTrue(metrics["elapsedSeconds"]["complete"])

    def test_unknown_exit_is_incomplete(self):
        self.write_receipt("unknownexit", exit_code=None)
        metrics = self.summarize()["metrics"]["checks"]
        self.assertEqual(metrics["unknown"], 1)
        self.assertEqual(metrics["elapsedSeconds"]["value"], 0.0)
        self.assertFalse(metrics["elapsedSeconds"]["complete"])


class BoardMetricsTest(unittest.TestCase):
    def test_reported_zero_and_plugin_bytes_do_not_prove_workflow_cost(self):
        for number in (1, 2):
            summary = self.new_summary(10, 3.0, 1.0, 56.0, 2)
            summary["reported_cost_usd"] = 0.0
            self.write_summary(number, summary)
        (self.task_dir / "codex-io.jsonl").write_text(
            json.dumps({"bytes": 42, "command": "result", "kind": "output"}) + "\n")
        record = pi_board.task_metrics(self.board_file, {"planRef": "PLAN.md"}, "T")
        self.assertEqual(record["costUsd"]["known"], 0.0)
        self.assertTrue(record["costUsd"]["complete"])
        self.assertFalse(record["costUsd"]["billingComplete"])
        self.assertIsNone(record["costUsd"]["billingCostUsd"])
        self.assertFalse(record["workflowCost"]["complete"])
        self.assertIsNone(record["workflowCost"]["reportedCostUsd"])
        self.assertEqual(record["workflowCost"]["evidenceRefs"]["plan"], "PLAN.md")
        self.assertIn("codex_takeover", record["measurementScope"]["excludes"])
        self.assertEqual(record["codexBytes"]["total"], 42)
        self.assertEqual(record["codexBytes"]["unit"], "utf8_bytes")

    def test_invalid_reported_cost_never_enters_known_total(self):
        for invalid in (float("nan"), float("inf"), -1, True):
            with self.subTest(invalid=invalid):
                self.write_summary(1, {"usage": {}, "usage_complete": True,
                                       "reported_cost_usd": invalid})
                self.write_summary(2, {"usage": {}, "usage_complete": True,
                                       "reported_cost_usd": 0.25})
                cost = pi_board.task_metrics(self.board_file, None, "T")["costUsd"]
                self.assertEqual(cost["known"], 0.25)
                self.assertFalse(cost["complete"])
                self.assertEqual(cost["roundsUnknown"], [1])

    def test_summary_cost_completeness_is_not_billing_completeness(self):
        usage = dict.fromkeys(pi_summary.USAGE_KEYS, 0)
        auxiliary = {"complete": True, "reportedCostUsd": {"value": 0}}
        for name in pi_summary.AUXILIARY_LEAF_NAMES.values():
            auxiliary[name] = {"value": 0}
        result = pi_summary.total_metrics(usage, True, 0, 1, 1, auxiliary)
        self.assertTrue(result["costComplete"])
        self.assertEqual(result["reportedCostUsd"], 0)
        self.assertEqual(result["costSource"], "provider_reported")
        self.assertFalse(result["billingComplete"])
        self.assertEqual(result["scope"], "pi_execution")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-board-metrics-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.board_file = self.tmp / "board.json"
        self.task_dir = self.tmp / "tasks" / "T"
        (self.task_dir / "rounds" / "1").mkdir(parents=True)
        (self.task_dir / "rounds" / "2").mkdir(parents=True)

    def write_summary(self, number, summary):
        (self.task_dir / "rounds" / str(number) / "round.summary.json").write_text(
            json.dumps(summary), encoding="utf-8")

    def new_summary(self, uncached, model, tool, unattributed, checks):
        return {"usage": {"input": uncached, "output": 1, "totalTokens": uncached + 1},
                "usage_complete": True, "reported_cost_usd": 0.1,
                "metrics": {"usage": {
                    "uncachedInput": {"value": uncached, "label": "exact", "samples": 1,
                                      "complete": True},
                    "cacheRead": {"value": 5, "label": "exact", "samples": 1, "complete": True},
                    "output": {"value": 1, "label": "exact", "samples": 1, "complete": True},
                    "reasoning": {"value": None, "label": "unknown", "samples": 0,
                                  "complete": False},
                    "totalTokens": {"value": uncached + 1, "label": "exact", "samples": 1,
                                    "complete": True}},
                    "context": {"first": {"value": 10, "label": "exact", "complete": True},
                                "last": {"value": 12, "label": "exact", "complete": True},
                                "peak": {"value": 12, "label": "exact", "complete": True}},
                    "checks": {"attempts": checks, "completed": checks, "passed": checks,
                               "failed": 0, "interrupted": 0, "unknown": 0,
                               "elapsedSeconds": {"value": 2.0, "label": "exact", "samples": checks,
                                                  "complete": True}},
                    "timing": {"windowSeconds": {"value": 60.0, "label": "exact", "complete": True},
                               "modelResponseSeconds": {"value": model, "label": "estimated",
                                                        "complete": True},
                               "toolSeconds": {"value": tool, "label": "estimated",
                                               "complete": True},
                               "unattributedSeconds": {"value": unattributed, "label": "estimated",
                                                       "complete": True}}}}

    def test_derived_metrics_aggregate_and_old_summaries_stay_unknown(self):
        self.write_summary(1, self.new_summary(10, 3.0, 1.0, 56.0, 2))
        self.write_summary(2, {"usage": {"input": 1}, "usage_complete": True,
                               "reported_cost_usd": 0.1})
        with mock.patch.object(pi_summary, "derive_timing",
                               side_effect=AssertionError("board refresh must not scan sessions")), \
                mock.patch.object(pi_task, "summarize",
                                  side_effect=AssertionError("board refresh must not rebuild summaries")):
            record = pi_board.task_metrics(self.board_file, None, "T")
        derived = record["derivedMetrics"]
        self.assertEqual(derived["usage"]["uncachedInput"]["known"], 10)
        self.assertFalse(derived["usage"]["uncachedInput"]["complete"])
        self.assertEqual(derived["usage"]["uncachedInput"]["roundsKnown"], [1])
        self.assertEqual(derived["usage"]["uncachedInput"]["roundsUnknown"], [2])
        self.assertEqual(derived["usage"]["uncachedInput"]["roundsIncomplete"], [])
        self.assertEqual(derived["contextPeak"]["known"], 12)
        self.assertFalse(derived["contextPeak"]["complete"])
        self.assertEqual(derived["contextPeak"]["roundsUnknown"], [2])
        self.assertEqual(derived["timing"]["modelResponseSeconds"]["known"], 3.0)
        self.assertEqual(derived["timing"]["modelResponseSeconds"]["roundsKnown"], [1])
        self.assertEqual(derived["timing"]["modelResponseSeconds"]["roundsUnknown"], [2])
        self.assertEqual(derived["checks"]["attempts"], 2)
        self.assertEqual(derived["checks"]["elapsedSeconds"]["known"], 2.0)
        self.assertEqual(derived["checks"]["elapsedSeconds"]["roundsUnknown"], [2])
        self.assertFalse(derived["checks"]["elapsedSeconds"]["complete"])
        self.assertIn(2, derived["roundsWithoutMetrics"])
        # The stored evidence is read-only: no summary was rewritten.
        before = (self.task_dir / "rounds" / "1" / "round.summary.json").read_bytes()
        pi_board.task_metrics(self.board_file, None, "T")
        self.assertEqual((self.task_dir / "rounds" / "1" / "round.summary.json").read_bytes(), before)


    def test_incomplete_round_blocks_aggregate_completeness(self):
        summary = self.new_summary(10, 3.0, 1.0, 56.0, 2)
        summary["metrics"]["checks"]["elapsedSeconds"]["complete"] = False
        summary["metrics"]["timing"]["modelResponseSeconds"]["complete"] = False
        self.write_summary(1, summary)
        with mock.patch.object(pi_summary, "derive_timing",
                               side_effect=AssertionError("board refresh must not scan sessions")):
            derived = pi_board.task_metrics(self.board_file, None, "T")["derivedMetrics"]
        checks = derived["checks"]["elapsedSeconds"]
        self.assertEqual(checks["known"], 2.0, "the known part stays visible")
        self.assertEqual(checks["roundsIncomplete"], [1])
        self.assertEqual(checks["roundsKnown"], [])
        self.assertEqual(checks["roundsUnknown"], [2])
        self.assertFalse(checks["complete"])
        model = derived["timing"]["modelResponseSeconds"]
        self.assertEqual(model["known"], 3.0)
        self.assertEqual(model["roundsIncomplete"], [1])
        self.assertFalse(model["complete"])


class BenchmarkHelperTest(unittest.TestCase):
    def test_benchmark_measures_helper_then_summary_end_to_end(self):
        with tempfile.TemporaryDirectory(prefix="codex-pi-benche2e-") as tmp:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "benchmark_runtime.py"),
                 "--baseline-ref", "HEAD", "--sizes-mib", "1", "--evidence-dir", tmp],
                capture_output=True, text=True, timeout=300, cwd=str(ROOT))
            self.assertIn(proc.returncode, (0, 1), proc.stderr)
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(data["factsOk"], data)
            for size, sides in data["summaryVerified"].items():
                self.assertTrue(sides["old"], f"old summary did not verify the log at {size}")
                self.assertTrue(sides["new"], f"new summary did not verify the log at {size}")
            self.assertTrue(Path(data["evidence"]).is_file())

    def test_synthetic_generator_facts_are_independent(self):
        spec = importlib.util.spec_from_file_location(
            "benchmark_runtime", ROOT / "scripts" / "benchmark_runtime.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="codex-pi-benchgen-") as tmp:
            path = Path(tmp) / "log.txt"
            facts = module.generate_log(path, 256 * 1024)
            raw = path.read_bytes()
            self.assertEqual(len(raw), 256 * 1024)
            self.assertEqual(facts["sha256"], hashlib.sha256(raw).hexdigest())
            lines = raw.decode("utf-8").splitlines()
            self.assertEqual(sum(line.startswith("=== RUN ") for line in lines), facts["counts"]["run"])
            self.assertEqual(sum(line.startswith("--- PASS:") for line in lines), facts["counts"]["pass"])
            self.assertEqual(lines[-1], facts["tailLastLine"])
            self.assertEqual(facts["counts"]["format"], "go_verbose_top_level")


class VersionTest(unittest.TestCase):
    def test_version_matches_release(self):
        self.assertEqual((RUNTIME / "VERSION").read_text().strip(), "0.8.7")
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], "0.8.7")


if __name__ == "__main__":
    unittest.main()
