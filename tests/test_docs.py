"""Deterministic guards for the model-visible guidance size budget.

Offline and fast: no model, no network, no skip. The checks are

1. byte budgets for the dispatch-cycle loaded set (the documents a normal round
   actually reads), the separately capped on-demand tier, and the receiving cap
   of ``runtime/IMPLEMENTATION.md``;
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

# The metric is the set a round loads, not the size of a folder. These four files are
# read on an ordinary dispatch/review round, so their combined size is what a model pays
# per round; the target stays the accepted half of the 42692-byte baseline.
LOADED_SET = {
    "skills/collaborate/SKILL.md": 8424,
    "skills/collaborate/references/runtime.md": 6144,
    "skills/collaborate/references/task-packet.md": 5312,
    "skills/collaborate/references/handoff.md": 2132,
}
LOADED_SET_TOTAL = 21504
# Rarely-needed operations moved out of the loaded set. They stay in skills/**, stay
# linked from SKILL.md and stay covered by the command-surface checks below: splitting a
# reference may defer text, never delete it.
ON_DEMAND = {
    "skills/collaborate/references/runtime-ops.md": 4608,
}
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
    def test_the_dispatch_loaded_set_fits_the_half_size_target(self):
        total = 0
        for name, budget in LOADED_SET.items():
            size = len((ROOT / name).read_bytes())
            total += size
            self.assertLessEqual(size, budget, f"{name} is {size} bytes, budget {budget}")
        self.assertLessEqual(total, LOADED_SET_TOTAL,
                             f"dispatch-cycle loaded set is {total} bytes, budget {LOADED_SET_TOTAL}")

    def test_every_reference_file_declares_which_tier_it_belongs_to(self):
        tiered = set(LOADED_SET) | set(ON_DEMAND)
        for path in sorted((SKILLS / "collaborate" / "references").glob("*.md")):
            self.assertIn(str(path.relative_to(ROOT)), tiered,
                          f"{path.name} is neither loaded per round nor declared on demand")

    def test_on_demand_references_are_reachable_and_state_their_condition(self):
        skill = read(ROOT / "skills" / "collaborate" / "SKILL.md")
        for name, budget in ON_DEMAND.items():
            path = ROOT / name
            size = len(path.read_bytes())
            self.assertLessEqual(size, budget, f"{name} is {size} bytes, budget {budget}")
            body = read(path)
            self.assertIn(name.rsplit("/", 1)[-1], skill,
                          f"{name} is not linked from SKILL.md, so deferring it loses it")
            self.assertRegex(body, r"(?i)load (this file )?when|load when:",
                             f"{name} defers content without stating when to load it")

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

    def test_every_public_subcommand_is_documented(self):
        """The reverse direction: a control main cannot find is a control main cannot use.

        Shrinking a reference file may move text, never delete the only current syntax for a
        live subcommand; a documented->real check alone cannot see that deletion.
        """
        documented = "\n".join(read(path) for path in relative_files(SKILLS, DOCUMENTED_SUFFIXES))
        missing = [name for name in sorted(self.surface)
                   if not re.search(re.escape(name.split()[0]) + r"[\s`]+" + re.escape(name.split()[1]),
                                    documented)]
        self.assertEqual([], missing,
                         "public subcommands absent from the model-visible documents")


class MechanismHomeTest(unittest.TestCase):
    def test_markers_live_only_in_the_maintainer_notes(self):
        implementation = read(RUNTIME / "IMPLEMENTATION.md")
        for marker in MARKERS:
            self.assertGreaterEqual(implementation.count(marker), 1,
                                    f"{marker} is missing from runtime/IMPLEMENTATION.md")
            for name in LOADED_SET:
                self.assertEqual(read(ROOT / name).count(marker), 0,
                                 f"{name} must not name {marker}")


class ConstraintDirectionTest(unittest.TestCase):
    def test_constraints_name_real_paths_and_not_the_skill(self):
        config = json.loads(read(ROOT / ".agents" / "codex-pi.json"))
        constraints = config.get("constraints", [])
        self.assertIn("AGENTS.md", constraints)
        self.assertNotIn("skills/collaborate/SKILL.md", constraints)
        self.assertTrue(any(entry.startswith("docs/design/") for entry in constraints),
                        "constraints should include a design document")
        for entry in constraints:
            self.assertTrue((ROOT / entry).is_file(), f"constraint path does not exist: {entry}")


if __name__ == "__main__":
    unittest.main()
