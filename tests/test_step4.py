"""Step 4: size-only codex-io accounting, `pi_board.py metrics`, release version."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import ROOT, RUNTIME, Repo, base_env, cleanup_repos, default_config, run_cli
from test_board import board_only, captured_runner, write_board

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402

BOARD = RUNTIME / "pi_board.py"
THREAD = "11111111-2222-3333-4444-555555555555"


def run_board(*args, env):
    return subprocess.run([sys.executable, str(BOARD), *[str(a) for a in args]],
                          capture_output=True, text=True, env=env, timeout=60)


def io_lines(task_dir: Path) -> list:
    path = task_dir / "codex-io.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class CodexIoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-io-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = Repo(self.tmp, name="repo", config=default_config())
        self.env = base_env(PI_DOUBLE_MODE="ok")
        self.env["CODEX_PI_HANDOFF_ROOT"] = str(self.tmp / "handoffs")
        self.repo.start("t1", self.repo.worktree("wt"), env=self.env)
        self.repo.wait_terminal("t1", env=self.env)
        self.task_dir = self.repo.task_dir("t1")

    def tearDown(self):
        cleanup_repos()

    def test_status_and_result_record_sizes_only(self):
        before = len(io_lines(self.task_dir))
        status = run_cli("status", "--repo", self.repo.root, "--task", "t1", env=self.env)
        result = run_cli("result", "--repo", self.repo.root, "--task", "t1", env=self.env)
        lines = io_lines(self.task_dir)[before:]
        self.assertEqual([line["command"] for line in lines], ["status", "result"])
        self.assertEqual(lines[0]["bytes"], len(status.stdout.encode("utf-8")))
        self.assertEqual(lines[1]["bytes"], len(result.stdout.encode("utf-8")))
        for line in lines:
            self.assertEqual(sorted(line), ["at", "bytes", "command"], "size only, no content")
        raw = (self.task_dir / "codex-io.jsonl").read_text()
        self.assertNotIn("completed", raw)
        self.assertNotIn("acceptance", raw)

    def test_other_commands_are_not_recorded(self):
        before = len(io_lines(self.task_dir))
        run_cli("project", "--repo", self.repo.root, env=self.env)
        run_cli("phase-status", "--repo", self.repo.root, "--task", "t1", env=self.env)
        self.assertEqual(len(io_lines(self.task_dir)), before)

    def test_write_failure_never_changes_output_or_exit_code(self):
        good = run_cli("status", "--repo", self.repo.root, "--task", "t1", env=self.env)
        path = self.task_dir / "codex-io.jsonl"
        path.unlink(missing_ok=True)
        path.mkdir()  # the append target is unwritable
        bad = run_cli("status", "--repo", self.repo.root, "--task", "t1", env=self.env)
        self.assertEqual(bad.returncode, 0)
        self.assertEqual(json.loads(bad.stdout)["task"], json.loads(good.stdout)["task"])
        self.assertEqual(bad.stderr, "")

    def test_show_splits_bytes_across_covered_tasks(self):
        run_board("register", "--repo", self.repo.root, "--task", "t1", "--thread", THREAD,
                  "--title", "T", "--goal", "G", env=self.env)
        before = len(io_lines(self.task_dir))
        shown = run_board("show", "--repo", self.repo.root, "--task", "t1", env=self.env)
        line = io_lines(self.task_dir)[before:][-1]
        self.assertEqual(line["command"], "show")
        self.assertEqual(line["bytes"], len(shown.stdout.encode("utf-8")))
        self.assertEqual(line["shared"], 1)
        # Two tasks: the shares add up to the printed size exactly.
        second = self.repo.worktree("wt2")
        self.repo.start("t2", second, env=self.env)
        self.repo.wait_terminal("t2", env=self.env)
        run_board("register", "--repo", self.repo.root, "--task", "t2", "--thread", THREAD,
                  "--title", "T2", "--goal", "G", env=self.env)
        counts = {t: len(io_lines(self.repo.task_dir(t))) for t in ("t1", "t2")}
        shown = run_board("show", "--repo", self.repo.root, "--thread", THREAD, env=self.env)
        added = [io_lines(self.repo.task_dir(t))[counts[t]:][-1] for t in ("t1", "t2")]
        self.assertEqual(sum(line["bytes"] for line in added), len(shown.stdout.encode("utf-8")))
        self.assertTrue(all(line["shared"] == 2 for line in added))

    def test_queued_card_records_packet_bytes(self):
        board = board_only(self.repo, task_id="t1", thread=THREAD,
                           transport=pi_board.TRANSPORT_CLI_QUEUE)
        pi_board.add_event(board["cards"]["t1"], "review_required", 3, "fp", "s",
                           {"round": 3, "head": "a" * 40}, {}, "q", 1)
        path = write_board(self.repo, board)
        calls = []
        before = len(io_lines(self.task_dir))
        os.environ["CODEX_PI_HANDOFF_ROOT"] = str(self.tmp / "handoffs")
        self.addCleanup(os.environ.pop, "CODEX_PI_HANDOFF_ROOT", None)
        import unittest.mock as mock
        with mock.patch.object(pi_board, "_resolve_codex_bin", return_value="/bin/true"):
            result = pi_board.dispatch_task(path, "t1", cli_runner=captured_runner(calls))
        self.assertTrue(result["dispatched"])
        message = calls[0]["argv"][-1]
        line = io_lines(self.task_dir)[before:][-1]
        self.assertEqual(line["kind"], "packet")
        self.assertEqual(line["bytes"], len(message.encode("utf-8")))
        self.assertLessEqual(line["bytes"], 1200)


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-metrics-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = Repo(self.tmp, name="repo", config=default_config())
        self.env = base_env()

    def tearDown(self):
        cleanup_repos()

    def make_task(self, name, summaries):
        task_dir = self.repo.task_dir(name)
        task_dir.mkdir(parents=True, exist_ok=True)
        for number, summary in summaries.items():
            round_dir = task_dir / "rounds" / str(number)
            round_dir.mkdir(parents=True)
            if summary is not None:
                (round_dir / "round.summary.json").write_text(json.dumps(summary))
        (task_dir / "task.json").write_text("{}")
        return task_dir

    @staticmethod
    def summary(total, cost, complete=True):
        return {"usage": {"input": total, "output": 1, "totalTokens": total + 1},
                "usage_complete": complete, "reported_cost_usd": cost}

    def metrics(self, *extra):
        proc = run_board("metrics", "--repo", self.repo.root, *extra, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [json.loads(line) for line in proc.stdout.splitlines()]

    def test_complete_totals_and_unknown_propagation(self):
        self.make_task("full", {1: self.summary(10, 0.25), 2: self.summary(20, 0.5)})
        row, = self.metrics("--task", "full")
        self.assertEqual(row["rounds"], 2)
        self.assertEqual(row["usage"]["known"], {"input": 30, "output": 2, "totalTokens": 32})
        self.assertTrue(row["usage"]["complete"])
        self.assertEqual(row["costUsd"], {"known": 0.75, "complete": True, "roundsUnknown": []})
        # A missing round summary: the known part is shown, never as complete.
        self.make_task("gap", {1: self.summary(10, 0.25), 2: None})
        row, = self.metrics("--task", "gap")
        self.assertEqual(row["usage"]["known"]["input"], 10)
        self.assertFalse(row["usage"]["complete"])
        self.assertEqual(row["usage"]["roundsMissing"], [2])
        self.assertFalse(row["costUsd"]["complete"])
        self.assertEqual(row["costUsd"]["roundsUnknown"], [2])
        # Reported usage incomplete / cost unknown in one round.
        self.make_task("partial", {1: self.summary(10, None, complete=False)})
        row, = self.metrics("--task", "partial")
        self.assertFalse(row["usage"]["complete"])
        self.assertEqual(row["usage"]["roundsIncomplete"], [1])
        self.assertFalse(row["costUsd"]["complete"])
        # No rounds at all is not a complete zero.
        self.make_task("empty", {})
        row, = self.metrics("--task", "empty")
        self.assertFalse(row["usage"]["complete"])

    def test_decisions_by_kind_failure_kind_and_takeover(self):
        self.make_task("rev", {1: self.summary(1, 0.1)})
        board = board_only(self.repo, task_id="rev", thread=THREAD,
                           transport=pi_board.TRANSPORT_CLI_QUEUE)
        card = board["cards"]["rev"]
        card["reviewPolicyPin"] = {"qualityFailureLimit": 2}
        specs = [("review_required", 1, "changes_requested", "quality"),
                 ("review_required", 2, "changes_requested", "external"),
                 ("phase_blocked", 3, "rejected", "quality"),
                 ("review_required", 4, "accepted", None)]
        for index, (kind, number, decision, failure) in enumerate(specs):
            event = pi_board.add_event(card, kind, number, f"fp{index}", "s",
                                       {"round": number, "head": "a" * 40}, {}, "q", index)
            event.update(handled=True, handledAt=10 + index, decision=decision)
            if failure:
                event["failureKind"] = failure
        write_board(self.repo, board)
        row, = self.metrics("--task", "rev")
        self.assertEqual(row["decisions"], {
            "review_required": {"changes_requested": 2, "accepted": 1},
            "phase_blocked": {"rejected": 1}})
        self.assertEqual(row["failureKinds"], {"quality": 2, "external": 1})
        self.assertFalse(row["takeover"], "accepted resets the count before the limit")
        card["codex"]["takeover"] = {"required": True, "limit": 2}
        write_board(self.repo, board)
        row, = self.metrics("--task", "rev")
        self.assertTrue(row["takeover"])
        # An unregistered task keeps unknowns instead of invented zeros.
        self.make_task("loose", {1: self.summary(1, 0.1)})
        row, = self.metrics("--task", "loose")
        self.assertFalse(row["registered"])
        self.assertIsNone(row["takeover"])
        self.assertIsNone(row["decisions"])

    def test_codex_bytes_and_one_line_per_task(self):
        for name in ("a", "b"):
            task_dir = self.make_task(name, {1: self.summary(1, 0.1)})
        task_dir = self.repo.task_dir("a")
        (task_dir / "codex-io.jsonl").write_text("\n".join([
            json.dumps({"at": 1, "command": "result", "bytes": 100}),
            json.dumps({"at": 2, "command": "status", "bytes": 40}),
            json.dumps({"at": 3, "command": "show", "bytes": 60}),
            json.dumps({"at": 4, "command": "queue", "kind": "packet", "bytes": 900}),
            json.dumps({"at": 5, "command": "result", "bytes": 10}),
            "not json", json.dumps({"at": 6, "command": "x", "bytes": -1})]) + "\n")
        rows = self.metrics()
        self.assertEqual([row["task"] for row in rows], ["a", "b"])
        io = rows[0]["codexBytes"]
        self.assertEqual(io["packet"], 900)
        self.assertEqual(io["commands"], {"result": 110, "status": 40, "show": 60})
        self.assertEqual(io["total"], 1110)
        self.assertEqual(io["badLines"], 2)
        self.assertTrue(io["tracked"])
        self.assertFalse(rows[1]["codexBytes"]["tracked"])
        self.assertEqual(rows[1]["codexBytes"]["total"], 0)


class VersionTest(unittest.TestCase):
    def test_release_version_is_consistent(self):
        version = (RUNTIME / "VERSION").read_text().strip()
        self.assertEqual(version, "0.6.0")
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"].split("+")[0], version)
        self.assertIn(version, (ROOT / "README.md").read_text())
        self.assertIn(version, (ROOT / "runtime" / "IMPLEMENTATION.md").read_text())


if __name__ == "__main__":
    unittest.main()
