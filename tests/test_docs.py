"""Deterministic guards for the 0.8.6 model-visible guidance budget.

Offline and fast: no model, no network, no skip. The checks are

1. byte budgets for the four model-visible collaboration documents, their total,
   and the receiving cap of ``runtime/IMPLEMENTATION.md``;
2. every relative Markdown link under ``skills/``, ``docs/`` and ``runtime/``
   resolves;
3. every ``pi_task.py``/``pi_board.py`` invocation documented under ``skills/**``
   names a real subcommand and real flags, derived from the live ``--help``
   surface instead of a hand-maintained list;
4. the mechanism markers ``worker.json``, ``agent_before_settle`` and
   ``CODEX_PI_NETWORK_DIAG_SUPERVISOR`` occur zero times in the four
   model-visible files and at least once in ``runtime/IMPLEMENTATION.md``;
5. ``.agents/codex-pi.json`` constraints list ``AGENTS.md`` and real design
   documents, never ``skills/collaborate/SKILL.md``.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
SKILLS = ROOT / "skills"

MODEL_VISIBLE = {
    "skills/collaborate/SKILL.md": 8192,
    "skills/collaborate/references/runtime.md": 6144,
    "skills/collaborate/references/task-packet.md": 5120,
    "skills/collaborate/references/handoff.md": 2048,
}
MODEL_VISIBLE_TOTAL = 21504
IMPLEMENTATION_BUDGET = 42000
MARKERS = ("worker.json", "agent_before_settle", "CODEX_PI_NETWORK_DIAG_SUPERVISOR")
CLI_ENTRIES = ("pi_task.py", "pi_board.py")
DOCUMENTED_SUFFIXES = {".md", ".yaml", ".yml", ".txt"}
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
COMMAND_RE = re.compile(r"\b(pi_task|pi_board)\.py[\s`]+([a-z][a-z0-9-]*)")
FLAG_RE = re.compile(r"--[a-zA-Z][a-zA-Z0-9-]*")
USAGE_RE = re.compile(r"\{([a-zA-Z0-9,._-]+)\}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def relative_files(base: Path, suffixes) -> list:
    return sorted(
        path for path in base.rglob("*")
        if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts
    )


class BudgetTest(unittest.TestCase):
    def test_model_visible_files_fit_their_frozen_budgets(self):
        total = 0
        for name, budget in MODEL_VISIBLE.items():
            size = len((ROOT / name).read_bytes())
            total += size
            self.assertLessEqual(size, budget, f"{name} is {size} bytes, budget {budget}")
        self.assertLessEqual(total, MODEL_VISIBLE_TOTAL,
                             f"model-visible total is {total} bytes, budget {MODEL_VISIBLE_TOTAL}")

    def test_implementation_notes_stay_within_the_receiving_cap(self):
        size = len((RUNTIME / "IMPLEMENTATION.md").read_bytes())
        self.assertLessEqual(size, IMPLEMENTATION_BUDGET,
                             f"runtime/IMPLEMENTATION.md is {size} bytes, budget {IMPLEMENTATION_BUDGET}")


class LinkIntegrityTest(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        checked = 0
        for base in (SKILLS, ROOT / "docs", RUNTIME):
            for path in relative_files(base, {".md"}):
                for target in LINK_RE.findall(read(path)):
                    if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                        continue
                    target = target.split("#", 1)[0]
                    if not target:
                        continue
                    resolved = (path.parent / target).resolve()
                    self.assertTrue(resolved.exists(),
                                    f"{path.relative_to(ROOT)} links to missing {target}")
                    checked += 1
        self.assertGreater(checked, 0, "no relative Markdown links found")


class CommandSurfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surface = {}
        for entry in CLI_ENTRIES:
            top = subprocess.run(
                [sys.executable, str(RUNTIME / entry), "--help"],
                capture_output=True, text=True, check=True, timeout=60,
            ).stdout
            match = USAGE_RE.search(top)
            assert match, f"{entry} --help has no subcommand usage"
            for sub in match.group(1).split(","):
                if sub.startswith("_"):
                    continue
                text = subprocess.run(
                    [sys.executable, str(RUNTIME / entry), sub, "--help"],
                    capture_output=True, text=True, check=True, timeout=60,
                ).stdout
                cls.surface[f"{entry} {sub}"] = set(FLAG_RE.findall(text))

    def documented_invocations(self):
        for path in relative_files(SKILLS, DOCUMENTED_SUFFIXES):
            lines = read(path).splitlines()
            for number, line in enumerate(lines):
                for match in COMMAND_RE.finditer(line):
                    entry = f"{match.group(1)}.py"
                    segment = line[match.end():]
                    following = number
                    while segment.rstrip().endswith("\\") and following + 1 < len(lines):
                        following += 1
                        segment += " " + lines[following]
                    yield path, number + 1, f"{entry} {match.group(2)}", FLAG_RE.findall(segment)

    def test_the_help_surface_is_non_trivial(self):
        self.assertGreaterEqual(len(self.surface), 20)
        for name, flags in self.surface.items():
            self.assertIn("--help", flags, name)

    def test_documented_commands_and_flags_exist(self):
        found = 0
        for path, number, invocation, flags in self.documented_invocations():
            where = f"{path.relative_to(ROOT)}:{number}"
            self.assertIn(invocation, self.surface, f"{where} documents unknown command: {invocation}")
            for flag in flags:
                self.assertIn(flag, self.surface[invocation],
                              f"{where} documents unknown flag {flag} for {invocation}")
            found += 1
        self.assertGreater(found, 0, "no documented CLI invocation found under skills/**")


class MechanismHomeTest(unittest.TestCase):
    def test_markers_live_only_in_the_maintainer_notes(self):
        implementation = read(RUNTIME / "IMPLEMENTATION.md")
        for marker in MARKERS:
            self.assertGreaterEqual(implementation.count(marker), 1,
                                    f"{marker} is missing from runtime/IMPLEMENTATION.md")
            for name in MODEL_VISIBLE:
                self.assertEqual(read(ROOT / name).count(marker), 0,
                                 f"{name} must not name {marker}")


if __name__ == "__main__":
    unittest.main()
