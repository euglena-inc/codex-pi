"""Module layout after the pure-move split (Step 6).

* no runtime module is larger than about 1.8k lines;
* top-level imports between runtime modules are acyclic and flow downward;
* every module reachable from the entry modules is in the frozen helper list, and a
  frozen ``tools/`` directory keeps working with the plugin directory gone.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime_helpers import ROOT, RUNTIME, Repo, base_env, cleanup_repos, default_config, run_cli

sys.path.insert(0, str(RUNTIME))
import pi_task  # noqa: E402

MAX_LINES = 1800


def top_level_imports(path: Path) -> set:
    names = {p.stem for p in RUNTIME.glob("pi_*.py")}
    found = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.ImportFrom) and node.module in names:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names if alias.name in names}
    return found


class LayoutTest(unittest.TestCase):
    def test_no_runtime_module_exceeds_the_size_cap(self):
        for path in sorted(RUNTIME.glob("*.py")) + [RUNTIME / "pi_worker.ts"]:
            lines = len(path.read_text().splitlines())
            self.assertLessEqual(lines, MAX_LINES, f"{path.name} has {lines} lines")

    def test_top_level_imports_are_acyclic(self):
        graph = {p.stem: top_level_imports(p) for p in RUNTIME.glob("pi_*.py")}
        state = {}

        def visit(node, trail):
            state[node] = 1
            for dep in graph[node]:
                self.assertNotEqual(state.get(dep), 1, f"import cycle: {trail + [node, dep]}")
                if dep not in state:
                    visit(dep, trail + [node])
            state[node] = 2

        for name in graph:
            if name not in state:
                visit(name, [])

    def test_lower_modules_never_import_the_entry_modules(self):
        entries = {"pi_task", "pi_board", "pi_handoff"}
        for path in RUNTIME.glob("pi_*.py"):
            if path.stem in entries:
                continue
            self.assertFalse(top_level_imports(path) & entries, f"{path.name} imports an entry module")
        self.assertNotIn("pi_task", top_level_imports(RUNTIME / "pi_board.py") - {"pi_task"} or set())
        self.assertNotIn("pi_board", top_level_imports(RUNTIME / "pi_task.py"))

    def test_helper_list_covers_every_module_the_entries_need(self):
        needed, queue = set(), ["pi_task", "pi_board"]
        while queue:
            name = queue.pop()
            if name in needed:
                continue
            needed.add(name)
            queue.extend(top_level_imports(RUNTIME / f"{name}.py"))
        # function-local imports of the supervisor/hooks reach pi_board and pi_store lazily.
        needed |= {"pi_board", "pi_store"}
        frozen = {Path(name).stem for name in pi_task.HELPER_FILES if name.endswith(".py")}
        self.assertLessEqual(needed, frozen, sorted(needed - frozen))
        for name in pi_task.HELPER_FILES:
            self.assertTrue((RUNTIME / name).is_file(), name)


class CliHintTest(unittest.TestCase):
    """Printed command hints and recorded worker script name the CLI entry modules."""

    def test_hints_name_the_entry_scripts(self):
        import shlex
        import pi_events
        import pi_queue
        import pi_store
        card = pi_events._new_card("t1", "11111111-2222-3333-4444-555555555555", "T", "G", "b.md",
                                   None, "/r", "/c", "/w", pi_store.TRANSPORT_CLI_QUEUE,
                                   "/bin/true", 1)
        event = pi_events.add_event(card, "review_required", 1, "fp", "s",
                                    {"round": 1, "head": "a" * 40}, {}, "q", 1)
        board = shlex.quote(str((RUNTIME / "pi_board.py").resolve()))
        self.assertEqual(pi_store.BOARD_CLI, (RUNTIME / "pi_board.py").resolve())
        self.assertTrue(pi_events._decide_hint("/r", "t1", event).startswith(
            f"python3 {board} decide --repo /r --task t1 --event-id EVENT --decision "))
        self.assertEqual(pi_queue._show_hint(card),
                         f"python3 {board} show --repo /r --task t1")
        import pi_core
        self.assertEqual(pi_core.TASK_CLI, (RUNTIME / "pi_task.py").resolve())


class FrozenToolsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-frozen-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name).resolve()

    def test_frozen_tools_run_without_the_plugin_directory(self):
        plugin = self.tmp / "plugin-runtime"
        shutil.copytree(RUNTIME, plugin, ignore=shutil.ignore_patterns("__pycache__"))
        repo = Repo(self.tmp, name="repo", config=default_config())
        worktree = repo.worktree("wt")
        env = base_env(PI_DOUBLE_MODE="ok")
        proc = subprocess.run([sys.executable, str(plugin / "pi_task.py"), "start", "--repo", str(repo.root),
                               "--task", "frozen", "--worktree", str(worktree), "--prompt", "go"],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        repo.wait_terminal("frozen", env=env)
        tools = repo.task_dir("frozen") / "tools"
        for name in pi_task.HELPER_FILES:
            self.assertTrue((tools / name).is_file(), f"frozen snapshot lacks {name}")
        shutil.rmtree(plugin)  # the plugin directory is gone: only tools/ can supply imports
        clean = {key: value for key, value in env.items() if key != "PYTHONPATH"}
        for script, args in (("pi_task.py", ["status", "--repo", str(repo.root), "--task", "frozen"]),
                             ("pi_task.py", ["result", "--repo", str(repo.root), "--task", "frozen"]),
                             ("pi_board.py", ["register", "--repo", str(repo.root), "--task", "frozen"]),
                             ("pi_board.py", ["show", "--repo", str(repo.root), "--task", "frozen"]),
                             ("pi_board.py", ["metrics", "--repo", str(repo.root)])):
            proc = subprocess.run([sys.executable, str(tools / script), *args], capture_output=True,
                                  text=True, env=clean, cwd=str(self.tmp), timeout=60)
            self.assertEqual(proc.returncode, 0, f"{script} {args[0]}: {proc.stderr}")
            json.loads(proc.stdout.splitlines()[0])
        self.assertFalse(list(tools.glob("__pycache__")), "CLI wrote bytecode into the frozen tools")
        probe = subprocess.run(
            [sys.executable, "-c",
             # Import compiles the entry module before its body can disable bytecode.
             "import sys, json; sys.dont_write_bytecode = True; "
             "sys.path.insert(0, sys.argv[1]); import pi_task, pi_board; "
             "print(json.dumps(sorted({m.__file__ for n, m in sys.modules.items() "
             "if n.startswith('pi_') and getattr(m, '__file__', None)})))", str(tools)],
            capture_output=True, text=True, env=clean, cwd=str(self.tmp), timeout=60)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        files = json.loads(probe.stdout)
        self.assertTrue(files)
        for path in files:
            self.assertEqual(Path(path).parent, tools, f"{path} is not inside the frozen tools")
        self.assertFalse(list(tools.glob("__pycache__")), "bytecode written into the frozen tools")


if __name__ == "__main__":
    unittest.main()
