"""Hooks after the Stop-hook delivery path was removed (0.6.0).

The remaining hooks only pause cli-queue routes on Interrupt and print bounded
recovery evidence on SessionStart/UserPromptSubmit. Removed CLI entries return
argument errors.
No Codex binary and no model are used.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime_helpers import (CLI, ROOT, RUNTIME, Repo, base_env, cleanup_repos, default_config,
                             make_pi_trap, run_cli)

sys.path.insert(0, str(RUNTIME))
import pi_board  # noqa: E402

HANDOFF = RUNTIME / "pi_handoff.py"
BOARD = RUNTIME / "pi_board.py"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
THREAD = "11111111-2222-3333-4444-555555555555"


def h_env(tmp: Path, **extra) -> dict:
    env = base_env(**extra)
    env["CODEX_PI_HANDOFF_ROOT"] = str(Path(tmp) / "handoffs")
    env.pop("CODEX_THREAD_ID", None)
    return env


def run_hook(name: str, session: str, env: dict, cwd=None) -> dict:
    payload = json.dumps({"hook_event_name": name, "session_id": session, "turn_id": "t1",
                          "cwd": "/tmp/unrelated-cwd"})
    proc = subprocess.run([sys.executable, str(HANDOFF), "hook"], input=payload,
                          capture_output=True, text=True, env=env, timeout=60,
                          cwd=str(cwd) if cwd else None)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class HooksAfterStopRemovalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-hooks-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        cleanup_repos()

    def make(self):
        repo = Repo(self.tmp, name="repo", config=default_config())
        return repo, repo.worktree("wt")

    def write_binding(self, repo, task: str, state: str, key: str = "k" * 64) -> Path:
        directory = self.tmp / "handoffs" / "bindings"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{key}.json"
        path.write_text(json.dumps({
            "schemaVersion": 1, "eventKey": key, "sessionId": THREAD, "repo": str(repo.root),
            "task": task, "round": 1, "state": state, "waitSeconds": 0, "armedAt": 1,
            "frozenModel": "deepseek/deepseek-flash", "generation": 0,
            "lastError": "legacy"}), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    def test_hooks_config_keeps_only_interrupt_and_recovery_events(self):
        config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertEqual(sorted(config["hooks"]), ["Interrupt", "SessionStart", "UserPromptSubmit"])
        for name, entries in config["hooks"].items():
            hook = entries[0]["hooks"][0]
            self.assertEqual(hook["command"], 'python3 "${PLUGIN_ROOT}/runtime/pi_handoff.py" hook')
            self.assertLessEqual(hook["timeout"], 3, name)

    def test_hook_command_executes_through_a_shell_with_spaces(self):
        command = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]["Interrupt"][0][
            "hooks"][0]["command"]
        plugin_root = self.tmp / "plugin root with spaces"
        plugin_root.mkdir()
        os.symlink(RUNTIME, plugin_root / "runtime")
        proc = subprocess.run(["/bin/sh", "-c", command.replace("${PLUGIN_ROOT}", str(plugin_root))],
                              input=json.dumps({"hook_event_name": "Interrupt",
                                                "session_id": THREAD}),
                              capture_output=True, text=True, env=h_env(self.tmp), timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {})

    def test_old_binding_files_are_never_injected_on_prompt_or_session_start(self):
        repo, _ = self.make()
        env = h_env(self.tmp)
        for state in ("delivered", "armed", "suspended", "needs_recovery", "acked"):
            self.write_binding(repo, "alpha", state)
            for name in ("UserPromptSubmit", "SessionStart"):
                self.assertEqual(run_hook(name, THREAD, env), {}, f"{name} with {state} binding")

    def test_stop_event_is_a_noop(self):
        repo, _ = self.make()
        self.write_binding(repo, "alpha", "armed")
        self.assertEqual(run_hook("Stop", THREAD, h_env(self.tmp)), {})

    def test_interrupt_pauses_the_cli_queue_route_until_explicit_resume(self):
        env = h_env(self.tmp)
        with mock.patch.dict(os.environ, {"CODEX_PI_HANDOFF_ROOT": env["CODEX_PI_HANDOFF_ROOT"]}):
            self.assertEqual(pi_board.route_paused(THREAD)[0], False)
            self.assertEqual(run_hook("Interrupt", THREAD, env), {})
            self.assertEqual(pi_board.route_paused(THREAD)[0], True)
            # An ordinary prompt never resumes the pause.
            run_hook("UserPromptSubmit", THREAD, env)
            self.assertEqual(pi_board.route_paused(THREAD)[0], True)
            pi_board.resume_route(THREAD)
            self.assertEqual(pi_board.route_paused(THREAD)[0], False)

    def test_hooks_never_invoke_codex_or_pi(self):
        codex, codex_marker = make_pi_trap(self.tmp / "bin", name="codex")
        pi, pi_marker = make_pi_trap(self.tmp / "bin", name="pi-trap")
        env = h_env(self.tmp)
        env["PATH"] = str(codex.parent) + os.pathsep + env["PATH"]
        env["PI_BIN"] = str(pi)
        for name in ("Interrupt", "SessionStart", "UserPromptSubmit", "Stop"):
            run_hook(name, THREAD, env)
        self.assertFalse(codex_marker.exists())
        self.assertFalse(pi_marker.exists())

    # ------------------------------------------------------------------
    def test_removed_cli_entries_are_argument_errors(self):
        env = h_env(self.tmp)
        repo, _ = self.make()
        base = ["--repo", str(repo.root), "--task", "t"]
        cases = [
            [str(HANDOFF), "arm", *base, "--round", "1"],
            [str(HANDOFF), "status", "--all"],
            [str(HANDOFF), "ack", "--event-key", "k"],
            [str(HANDOFF), "release", "--event-key", "k"],
            [str(CLI), "check", *base, "--observer", "o"],
            [str(CLI), "wait", *base],
            [str(BOARD), "packet", *base],
            [str(BOARD), "dispatch", *base],
        ]
        for argv in cases:
            proc = subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                                  env=env, timeout=30)
            self.assertEqual(proc.returncode, 2, f"{argv[0]} {argv[1]}: {proc.stderr}")
            self.assertIn("invalid choice", proc.stderr)

    def test_help_lists_no_removed_entries(self):
        for script, removed in ((CLI, ("check", "wait")), (BOARD, ("packet", "dispatch"))):
            text = subprocess.run([sys.executable, str(script), "--help"], capture_output=True,
                                  text=True, timeout=30).stdout
            for name in removed:
                self.assertNotIn(f"\n    {name} ", text)
                self.assertNotRegex(text, rf"[{{,]{name}[,}}]")

    # ------------------------------------------------------------------
    def finished_task(self, name: str):
        repo, worktree = self.make()
        env = h_env(self.tmp, PI_DOUBLE_MODE="ok")
        repo.start(name, worktree, env=env)
        repo.wait_terminal(name, env=env)
        return repo, env

    def test_snapshot_contains_self_contained_helpers(self):
        repo, worktree = self.make()
        env = base_env(PI_DOUBLE_MODE="ok")
        repo.start("snap", worktree, env=env)
        tools = repo.task_dir("snap") / "tools"
        for name in ("pi_task.py", "pi_check.py", "pi_summary.py", "pi_copy.py", "pi_size.py",
                     "pi_board.py", "VERSION"):
            self.assertTrue((tools / name).is_file(), f"frozen snapshot is missing {name}")
        checked = subprocess.run(
            [sys.executable, str(tools / "pi_check.py"), "--output-dir", str(self.tmp / "c"),
             "--id", "snapshot-check", "--", sys.executable, "-c", "print('ok')"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        repo.wait_terminal("snap", env=env)


if __name__ == "__main__":
    unittest.main()
