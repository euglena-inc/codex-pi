"""Module layout after the pure-move split (Step 6).

* the committed AST check proves every moved definition is identical;
* no runtime module is larger than about 1.8k lines;
* top-level imports between runtime modules are acyclic and flow downward;
* every module reachable from the entry modules is in the frozen helper list, and a
  frozen ``tools/`` directory keeps working with the plugin directory gone.
"""
from __future__ import annotations

import ast
import importlib.util
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

CHECK = ROOT / "scripts" / "check_pure_move.py"
MAX_LINES = 1800


def load_checker():
    spec = importlib.util.spec_from_file_location("check_pure_move", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def top_level_imports(path: Path) -> set:
    names = {p.stem for p in RUNTIME.glob("pi_*.py")}
    found = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.ImportFrom) and node.module in names:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names if alias.name in names}
    return found


class PureMoveTest(unittest.TestCase):
    def test_committed_baseline_matches_the_tree(self):
        proc = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PURE MOVE", proc.stdout)
        self.assertIn("changed (AST differs): 0; missing: 0; added: 0", proc.stdout)
        baseline = json.loads((ROOT / "scripts" / "pure_move_baseline.json").read_text())
        self.assertGreater(len(baseline["definitions"]), 400)

    def test_the_checker_flags_edits_additions_and_removals_but_not_moves(self):
        checker = load_checker()
        old = {"a": "def f(x):\n    return x + 1\n\nK = 3\n"}
        moved = {"a": "K = 3\n", "b": "def f(x):\n    return x + 1\n"}
        report = checker.compare(checker.fingerprints(old), checker.fingerprints(moved))
        self.assertEqual((report["changed"], report["missing"], report["added"]), ([], [], []))
        self.assertEqual(len(report["moved"]), 1)
        edited = {"a": "K = 3\n", "b": "def f(x):\n    return x + 2\n"}
        report = checker.compare(checker.fingerprints(old), checker.fingerprints(edited))
        self.assertEqual(report["changed"], ["f"])
        extra = {"a": old["a"], "b": "def g():\n    pass\n"}
        self.assertEqual(checker.compare(checker.fingerprints(old), checker.fingerprints(extra))["added"], ["g"])
        gone = {"a": "K = 3\n"}
        self.assertEqual(checker.compare(checker.fingerprints(old), checker.fingerprints(gone))["missing"], ["f"])
        local = {"a": "def f(x):\n    from m import y\n    return x\n"}
        retargeted = {"b": "def f(x):\n    from n import y\n    return x\n"}
        report = checker.compare(checker.fingerprints(local), checker.fingerprints(retargeted))
        self.assertEqual((report["changed"], len(report["retargeted"])), ([], 1))


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
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys, json; sys.path.insert(0, sys.argv[1]); import pi_task, pi_board; "
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
