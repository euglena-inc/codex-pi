"""Provider errors that Pi reports with process exit 0 are not deliveries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import RUNTIME, Repo, base_env, cleanup_repos, default_config
sys.path.insert(0, str(RUNTIME))
import pi_board
from pi_execution import terminal_evidence
from pi_takeover import review_policy


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-pi-execution-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.root = Path(self.tmp.name)

    def test_exit_zero_connection_error_preserves_exit_and_never_reviews_quality(self):
        repo = Repo(self.root, config=default_config())
        wt = repo.worktree("worker")
        env = base_env(PI_DOUBLE_MODE="connection-error")
        repo.start("fault", wt, env=env)
        result = repo.wait_terminal("fault", env=env)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["final"], "")
        pi_board.register_task(repo.root, "fault", transport="offline")
        from pi_store import read_board
        board, problem = read_board(repo.state_dir / "board.json")
        self.assertIsNone(problem)
        card = board["cards"]["fault"]
        events = card["events"]
        self.assertEqual([e["kind"] for e in events], ["execution_failed"])
        decided = pi_board.decide(repo.root, "fault", events[0]["id"], "changes_requested")
        self.assertEqual(decided["reviewPolicy"]["failedDeliveries"], 0)
        self.assertEqual(review_policy(card)["failedDeliveries"], 0)

    def test_last_message_overrides_earlier_error_but_tool_error_does_not(self):
        path = self.root / "stream.jsonl"
        def message(stop, error=None):
            return {"type":"message_end", "message":{"role":"assistant",
                    "stopReason":stop, "errorMessage":error,
                    "content":[{"type":"text", "text":"last"}]}}
        path.write_text("\n".join(json.dumps(x) for x in [message("error","Connection error."),
            {"type":"tool_execution_end","isError":True}, message("stop")]) + "\n")
        self.assertEqual(terminal_evidence(path)["status"], "completed")
        path.write_text(path.read_text() + json.dumps(message("aborted")) + "\n")
        self.assertEqual(terminal_evidence(path)["status"], "failed")

    def test_missing_truncated_and_oversized_final_messages_fail_closed(self):
        path = self.root / "stream.jsonl"
        for text in ["", '{"type":', json.dumps({"type":"message_end", "message":{
            "role":"assistant","stopReason":"stop","content":[{"type":"text",
            "text":"x" * 1_100_000}]}}) + "\n"]:
            path.write_text(text)
            self.assertEqual(terminal_evidence(path)["status"], "unknown")

    def test_later_activity_and_stream_fault_cannot_reuse_an_earlier_answer(self):
        path=self.root/'stream.jsonl'
        prefix=json.dumps({'type':'message_end','message':{'role':'assistant','stopReason':'stop',
                          'content':[{'type':'text','text':'earlier answer'}]}})+'\n'
        path.write_text(prefix+json.dumps({'type':'turn_start'})+'\n')
        self.assertEqual(terminal_evidence(path)['status'],'unknown')
        path.write_text(prefix+json.dumps({'type':'error','message':'Connection closed'})+'\n')
        self.assertEqual(terminal_evidence(path)['status'],'failed')

    def test_registration_reports_projection_failure_instead_of_silent_success(self):
        from unittest.mock import patch
        from pi_store import BoardOverflow,read_monitors
        repo=Repo(self.root,config=default_config());wt=repo.worktree('worker')
        env=base_env(PI_DOUBLE_MODE='ok');repo.start('fault',wt,env=env);repo.wait_terminal('fault',env=env)
        with patch('pi_board.refresh_with_status',side_effect=BoardOverflow('controlled overflow')):
            result=pi_board.register_task(repo.root,'fault',transport='offline')
        self.assertFalse(result['ok']);self.assertTrue(result['registered'])
        self.assertIn('controlled overflow',result['refreshError'])
        self.assertIn('projection failed',result['warning'])
        monitors,problem=read_monitors(repo.state_dir/'board.json')
        self.assertIsNone(problem);self.assertFalse(monitors['tasks']['fault']['healthy'])
