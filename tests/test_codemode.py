"""Native codemode runtime observations: tool selection and nested event streams.

These tests are offline and synthetic. The real Pi/QuickJS integration is covered
by scripts/validate_codemode.py; nothing here substitutes for it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import RUNTIME

sys.path.insert(0, str(RUNTIME))
import pi_core  # noqa: E402
from pi_summary import summarize  # noqa: E402


def write_log(directory: Path, events: list) -> Path:
    log = directory / "round.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return log


def assistant(text: str, *, usage: dict | None = None) -> dict:
    return {"type": "message_end", "message": {
        "role": "assistant", "provider": "deepseek", "model": "deepseek-flash",
        "stopReason": "stop",
        "usage": usage or {"input": 10, "output": 5, "totalTokens": 15},
        "content": [{"type": "text", "text": text}]}}


NESTED_USAGE = {"input": 9, "output": 9, "totalTokens": 18}


class ToolSelectionTest(unittest.TestCase):
    def test_codemode_is_added_to_both_selections_without_widening_read_only(self):
        read_only = set(pi_core.READ_ONLY_TOOLS.split(","))
        writable = set(pi_core.WRITABLE_TOOLS.split(","))
        self.assertIn("codemode", read_only)
        self.assertIn("codemode", writable)
        self.assertIn("check", read_only)
        self.assertEqual(read_only - {"codemode"}, {"read", "grep", "find", "ls",
                                                    "check", "progress", "readiness"})
        self.assertEqual(writable - {"codemode"}, {"read", "write", "edit", "bash",
                                                   "check", "progress", "readiness"})
        self.assertFalse(read_only & {"write", "edit", "bash"})


class NestedEventStreamTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-codemode-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.worktree = self.tmp / "wt"
        self.worktree.mkdir()

    def test_nested_calls_are_counted_once_with_exit_observations(self):
        log = write_log(self.tmp, [
            {"type": "turn_start"},
            {"type": "tool_execution_start", "toolName": "codemode", "toolCallId": "call-1",
             "args": {"code": "return 1;"}},
            {"type": "tool_execution_start", "toolName": "read", "toolCallId": "call-1/1",
             "parentToolCallId": "call-1", "args": {"path": "README.md"}},
            {"type": "tool_execution_end", "toolCallId": "call-1/1", "toolName": "read",
             "parentToolCallId": "call-1", "isError": False,
             "result": {"content": [{"type": "text", "text": "read ok"}], "details": {}}},
            {"type": "tool_execution_start", "toolName": "bash", "toolCallId": "call-1/2",
             "parentToolCallId": "call-1", "args": {"command": "echo hi"}},
            {"type": "tool_execution_end", "toolCallId": "call-1/2", "toolName": "bash",
             "parentToolCallId": "call-1", "isError": False,
             "result": {"content": [{"type": "text", "text": "hi"}],
                        "details": {"exitCode": 0}}},
            {"type": "tool_execution_end", "toolCallId": "call-1", "toolName": "codemode",
             "isError": False, "result": {"content": [{"type": "text", "text": "Script completed"}],
                                          "details": {"calls": 2}, "usage": NESTED_USAGE}},
            assistant("done"),
        ])
        data = summarize(log, self.worktree, run_dir=self.tmp)
        self.assertEqual(data["tool_counts"], {"codemode": 1, "read": 1, "bash": 1})
        self.assertEqual(data["execution_evidence"]["status"], "completed")
        self.assertEqual(len(data["commands"]), 1)
        self.assertEqual(data["commands"][0]["command"], "echo hi")
        self.assertEqual(data["commands"][0]["exit_code"], 0)
        self.assertIs(data["commands"][0]["tool_error"], False)
        # Nested tool usage is not added on top of the assistant usage.
        self.assertEqual(data["usage"], {"input": 10, "cacheRead": None, "cacheWrite": None,
                                         "output": 5, "totalTokens": 15})
        self.assertFalse(data["usage_complete"])
        self.assertEqual(data["malformed_lines"], 0)

    def test_nested_failure_and_cancellation_keep_distinct_observations(self):
        log = write_log(self.tmp, [
            {"type": "turn_start"},
            {"type": "tool_execution_start", "toolName": "codemode", "toolCallId": "call-1",
             "args": {"code": "wait"}},
            {"type": "tool_execution_start", "toolName": "bash", "toolCallId": "call-1/1",
             "parentToolCallId": "call-1", "args": {"command": "false"}},
            {"type": "tool_execution_end", "toolCallId": "call-1/1", "toolName": "bash",
             "parentToolCallId": "call-1", "isError": True,
             "result": {"content": [{"type": "text", "text": "exit 1"}], "details": {}}},
            {"type": "tool_execution_start", "toolName": "bash", "toolCallId": "call-1/2",
             "parentToolCallId": "call-1", "args": {"command": "sleep 30"}},
            {"type": "tool_execution_end", "toolCallId": "call-1", "toolName": "codemode",
             "isError": True, "result": {"content": [{"type": "text", "text": "Script failed"}]}},
            assistant("observed failure"),
        ])
        data = summarize(log, self.worktree, run_dir=self.tmp)
        self.assertEqual(data["tool_counts"], {"codemode": 1, "bash": 2})
        self.assertEqual(data["execution_evidence"]["status"], "completed")
        failed, cancelled = data["commands"]
        self.assertIs(failed["tool_error"], True)
        self.assertIsNone(failed["exit_code"])
        self.assertIsNone(cancelled["tool_error"])
        self.assertIsNone(cancelled["exit_code"])


if __name__ == "__main__":
    unittest.main()
