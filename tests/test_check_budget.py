"""Behavioral tests for the budget-aware check path in runtime/pi_worker.ts.

The real extension is driven through the Node harness with synthetic repositories,
task identities and independent side-effect marker files. A refused check must not
spawn a child, must not write a receipt/log/running marker and must return a
structured ``ok:false``; an admitted check must actually run and leave its own
side effect. Contract metadata validation and the targeted-command evidence rule
are exercised through the real pi_phase/pi_brief/pi_evidence functions.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from runtime_helpers import RUNTIME, Repo, cleanup_repos

sys.path.insert(0, str(RUNTIME))
import pi_brief  # noqa: E402
import pi_evidence  # noqa: E402
import pi_phase  # noqa: E402
import pi_task  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "doubles" / "extension_harness.mjs"
EXTENSION = RUNTIME / "pi_worker.ts"
NODE = shutil.which("node")


def setUpModule():
    if NODE is None:
        raise AssertionError("node is required on PATH: the check-budget tests must not be skipped")


class BudgetCase(unittest.TestCase):
    """A synthetic task with a frozen tools snapshot and a real git worktree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="codex-pi-budget-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(cleanup_repos)
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = Repo(self.tmp, name="main")
        self.main = self.repo.root.resolve()
        self.worktree = self.repo.worktree("wt").resolve()
        self.task_dir = self.main / ".git" / "codex-pi" / "tasks" / "T"
        self.round_dir = self.task_dir / "rounds" / "1"
        self.tools = self.task_dir / "tools"
        self.checks = self.round_dir / "round.checks"
        self.markers = self.tmp / "markers"
        for path in (self.tools, self.checks, self.markers):
            path.mkdir(parents=True)
        pi_task.snapshot_helpers(RUNTIME, self.tools)
        (self.main / "design.md").write_text("design\n", encoding="utf-8")
        self.design_sha = hashlib.sha256((self.main / "design.md").read_bytes()).hexdigest()
        self.state_path = self.round_dir / "round.state.json"
        self.config = {
            "schemaVersion": 1, "task": "T", "round": 1, "repo": str(self.main),
            "worktree": str(self.worktree),
            "forbiddenRoots": [str(self.main)],
            "allowedRoots": [str(self.worktree), str(self.tools), str(self.checks)],
            "bashDefaultTimeoutSeconds": 30, "bashCeilingSeconds": 120,
            "checkTimeoutSeconds": 60, "checksDir": str(self.checks), "toolsDir": str(self.tools),
            "python": sys.executable, "phase": False, "settleQuotaPath": None,
            "roundDir": str(self.round_dir), "contract": "CONTRACT TEXT",
            "deadlinePath": str(self.state_path), "acceptanceItems": [],
        }

    # -- harness plumbing -------------------------------------------------
    def run_steps(self, steps, config=None):
        config_path = self.tmp / "worker.json"
        config_path.write_text(json.dumps(config if config is not None else self.config))
        scenario = self.tmp / "scenario.json"
        scenario.write_text(json.dumps({"steps": steps}))
        proc = subprocess.run([NODE, str(HARNESS), str(EXTENSION), str(scenario)],
                              capture_output=True, text=True,
                              env={**os.environ, "CODEX_PI_WORKER_CONFIG": str(config_path)},
                              timeout=120, cwd=str(self.worktree))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def tool(self, name, config=None, tool_call_id=None, **params):
        step = {"op": "tool", "name": name, "params": params}
        if tool_call_id is not None:
            step["toolCallId"] = tool_call_id
        return self.run_steps([step], config=config)[0]

    def codemode_then_check(self, code, tool_call_id="call-1/1", **params):
        """One Node process: the deadline map only lives inside one extension instance."""
        steps = [{"op": "tool_call", "toolName": "codemode", "input": {"code": code}},
                 {"op": "tool", "name": "check", "params": params, "toolCallId": tool_call_id}]
        return self.run_steps(steps)[1]

    # -- fixtures ---------------------------------------------------------
    def write_state(self, remaining=300.0, **overrides):
        state = {"schemaVersion": 1, "round": 1, "taskDir": str(self.task_dir),
                 "state": "running", "startedAt": time.time(),
                 "deadlineAt": time.time() + remaining}
        state.update(overrides)
        self.state_path.write_text(json.dumps(state))

    def command(self, name):
        return f'touch "{self.markers / name}"'

    def marker(self, name):
        return self.markers / name

    def artifacts(self):
        return sorted(path.name for path in self.checks.iterdir())

    def assertCleanRefusal(self, result, prefix, name):
        self.assertTrue(result.get("isError"), name)
        structured = result["structuredContent"]
        self.assertFalse(structured["ok"], name)
        self.assertIsNone(structured["receipt"], name)
        self.assertIn(prefix, structured["reason"], name)
        self.assertFalse(self.marker(name).exists(), name)
        self.assertEqual(self.artifacts(), [], name)

    def target_config(self, command, targeted, **item):
        entry = {"id": "A", "checkId": "A", "command": command, "targetedCommand": targeted}
        entry.update(item)
        return dict(self.config, acceptanceItems=[entry])

    # -- contract helpers -------------------------------------------------
    def contract(self, items, **overrides):
        data = {
            "schemaVersion": 1, "phaseId": "P-BUDGET", "goal": "goal", "result": "result",
            "baseline": "HEAD", "scope": ["."],
            "designRef": "design.md", "designSha256": self.design_sha,
            "acceptanceItems": items, "budgetSeconds": 3600, "commandTimeoutSeconds": 900,
            "resourceLimits": [], "autonomousRepair": ["fix"], "escalateWhen": ["blocked"],
        }
        data.update(overrides)
        return data

    def item(self, **overrides):
        entry = {"id": "A1", "description": "d", "command": "python3 -c pass",
                 "passCondition": "exit 0", "evidence": "receipt"}
        entry.update(overrides)
        return entry


class AdmissionTest(BudgetCase):
    def test_sufficient_budget_actually_runs_the_command(self):
        self.write_state(remaining=300)
        result = self.tool("check", id="ok", command=self.command("ok"))
        self.assertNotIn("isError", result)
        structured = result["structuredContent"]
        self.assertTrue(structured["ok"])
        self.assertEqual(structured["exit_code"], 0)
        self.assertIsNotNone(structured["receipt"])
        self.assertEqual(structured["estimateSource"], "timeout")
        self.assertEqual(structured["requiredSeconds"], 60)
        self.assertGreaterEqual(structured["elapsedSeconds"], 0)
        self.assertLess(structured["elapsedSeconds"], 30)
        self.assertTrue(self.marker("ok").exists())
        self.assertFalse(list(self.checks.glob("*.running")))
        self.assertEqual(len(list(self.checks.glob("*.json"))), 1)

    def test_insufficient_budget_refuses_before_spawning(self):
        self.write_state(remaining=65)
        result = self.tool("check", id="never", command=self.command("never"))
        structured = result["structuredContent"]
        self.assertCleanRefusal(result, "insufficient_budget", "never")
        self.assertEqual(structured["reserveSeconds"], 60)
        self.assertEqual(structured["requiredSeconds"], 60)
        self.assertGreaterEqual(structured["remainingSeconds"], 64)
        self.assertLessEqual(structured["remainingSeconds"], 66)
        self.assertGreater(structured["allowedSeconds"], 4)
        self.assertLessEqual(structured["allowedSeconds"], 5)

    def test_estimate_admits_a_short_check_near_the_deadline(self):
        self.write_state(remaining=65)
        result = self.tool("check", id="short", command=self.command("short"), estimatedSeconds=1)
        self.assertNotIn("isError", result)
        structured = result["structuredContent"]
        self.assertEqual(structured["estimateSource"], "call")
        self.assertEqual(structured["requiredSeconds"], 1)
        self.assertTrue(self.marker("short").exists())
        receipt = json.loads(Path(structured["receipt"]).read_text())
        self.assertLessEqual(receipt["deadline_at"] - receipt["started_at"], 5.5)

    def test_declared_cap_and_available_time_clamp_the_wrapper(self):
        self.write_state(remaining=300)
        config = dict(self.config, checkTimeoutSeconds=5)
        result = self.tool("check", config=config, id="cap", command=self.command("cap"), timeoutSeconds=9999)
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["requiredSeconds"], 5)
        receipt = json.loads(Path(result["structuredContent"]["receipt"]).read_text())
        self.assertLessEqual(receipt["deadline_at"] - receipt["started_at"], 5.5)

    def test_estimates_take_the_larger_value_and_report_their_source(self):
        self.write_state(remaining=300)
        command = self.command("est")
        item = {"id": "A", "checkId": "A", "command": command, "estimatedSeconds": 5}
        config = dict(self.config, acceptanceItems=[item])
        both = self.tool("check", config=config, id="A", command=command, estimatedSeconds=2)
        self.assertEqual(both["structuredContent"]["estimateSource"], "contract+call")
        self.assertEqual(both["structuredContent"]["requiredSeconds"], 5)
        contract = self.tool("check", config=config, id="A", command=command)
        self.assertEqual(contract["structuredContent"]["estimateSource"], "contract")
        self.assertEqual(contract["structuredContent"]["requiredSeconds"], 5)
        call = self.tool("check", id="call", command=command, estimatedSeconds=3)
        self.assertEqual(call["structuredContent"]["estimateSource"], "call")
        self.assertEqual(call["structuredContent"]["requiredSeconds"], 3)

    def test_requested_timeout_below_the_estimate_is_refused(self):
        self.write_state(remaining=300)
        result = self.tool("check", id="too-short", command=self.command("too-short"),
                           timeoutSeconds=1, estimatedSeconds=20)
        structured = result["structuredContent"]
        self.assertCleanRefusal(result, "timeout_below_estimate", "too-short")
        self.assertEqual(structured["requiredSeconds"], 20)
        self.assertEqual(structured["allowedSeconds"], 1)
        self.assertEqual(structured["reserveSeconds"], 60)
        self.assertGreaterEqual(structured["remainingSeconds"], 299)
        # The hint must not invert the semantics: the timeout may be raised only
        # inside the authorized cap to cover a trusted estimate, never by
        # inventing a lower estimate or expanding the budget.
        self.assertIn("adjust timeoutSeconds only within the authorized cap", structured["reason"])
        self.assertIn("correct the estimate only when evidence supports it", structured["reason"])
        self.assertNotIn("estimate is wrong", structured["reason"])

    def test_contract_cap_below_the_estimate_is_refused(self):
        self.write_state(remaining=300)
        command = self.command("cap-short")
        config = dict(self.config, checkTimeoutSeconds=5,
                      acceptanceItems=[{"id": "A", "checkId": "A", "command": command,
                                        "estimatedSeconds": 20}])
        result = self.tool("check", config=config, id="A", command=command)
        structured = result["structuredContent"]
        self.assertCleanRefusal(result, "timeout_below_estimate", "cap-short")
        self.assertEqual(structured["requiredSeconds"], 20)
        self.assertEqual(structured["allowedSeconds"], 5)

    def test_smaller_call_estimate_does_not_lower_a_contract_estimate(self):
        self.write_state(remaining=300)
        command = self.command("maximal")
        config = dict(self.config, acceptanceItems=[{"id": "A", "checkId": "A",
                                                     "command": command, "estimatedSeconds": 20}])
        result = self.tool("check", config=config, id="A", command=command, estimatedSeconds=1,
                           timeoutSeconds=30)
        self.assertNotIn("isError", result)
        structured = result["structuredContent"]
        self.assertEqual(structured["requiredSeconds"], 20)
        self.assertEqual(structured["estimateSource"], "contract+call")
        self.assertEqual(structured["allowedSeconds"], 30)
        self.assertTrue(self.marker("maximal").exists())

    def test_fractional_windows_are_not_rounded_below_the_estimate(self):
        self.write_state(remaining=300)
        result = self.tool("check", id="frac", command=self.command("frac"),
                           timeoutSeconds=3.7, estimatedSeconds=3.5)
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["allowedSeconds"], 3.7)
        receipt = json.loads(Path(result["structuredContent"]["receipt"]).read_text())
        self.assertGreaterEqual(receipt["deadline_at"] - receipt["started_at"], 3.69)
        self.assertLessEqual(receipt["deadline_at"] - receipt["started_at"], 3.71)
        boundary = self.tool("check", id="frac-eq", command=self.command("frac-eq"),
                             timeoutSeconds=2.5, estimatedSeconds=2.5)
        self.assertNotIn("isError", boundary)
        self.assertEqual(boundary["structuredContent"]["requiredSeconds"], 2.5)
        receipt = json.loads(Path(boundary["structuredContent"]["receipt"]).read_text())
        self.assertAlmostEqual(receipt["deadline_at"] - receipt["started_at"], 2.5, places=6)
        self.assertTrue(self.marker("frac").exists())
        self.assertTrue(self.marker("frac-eq").exists())

    def test_unknown_budget_reads_fail_closed(self):
        command = self.command("unknown")
        cases = {
            "missing": None,
            "malformed": "{not json",
            "wrong-round": json.dumps({"schemaVersion": 1, "round": 2, "taskDir": str(self.task_dir),
                                       "deadlineAt": time.time() + 300}),
            "wrong-task": json.dumps({"schemaVersion": 1, "round": 1, "taskDir": str(self.tmp),
                                      "deadlineAt": time.time() + 300}),
            "bad-deadline": json.dumps({"schemaVersion": 1, "round": 1, "taskDir": str(self.task_dir),
                                        "deadlineAt": "later"}),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                if raw is None:
                    if self.state_path.exists():
                        self.state_path.unlink()
                else:
                    self.state_path.write_text(raw)
                result = self.tool("check", id="unknown", command=command)
                self.assertCleanRefusal(result, "budget_unknown", "unknown")
        # A past deadline is a real remaining budget, not an unreadable one.
        self.write_state(remaining=-10)
        result = self.tool("check", id="unknown", command=command)
        self.assertCleanRefusal(result, "insufficient_budget", "unknown")
        self.assertEqual(result["structuredContent"]["remainingSeconds"], 0)
        self.assertEqual(result["structuredContent"]["allowedSeconds"], 0)

    def test_old_config_without_deadline_metadata_keeps_old_behavior(self):
        config = dict(self.config)
        config.pop("deadlinePath")
        config.pop("acceptanceItems")
        result = self.tool("check", config=config, id="legacy", command=self.command("legacy"))
        self.assertNotIn("isError", result)
        self.assertTrue(self.marker("legacy").exists())
        self.assertNotIn("requiredSeconds", result["structuredContent"])
        self.assertNotIn("estimateSource", result["structuredContent"])

    def test_invalid_estimate_and_final_values_are_refused(self):
        self.write_state(remaining=300)
        command = self.command("invalid")
        for params in ({"estimatedSeconds": 0}, {"estimatedSeconds": -1}, {"estimatedSeconds": "soon"},
                       {"estimatedSeconds": None}, {"final": "yes"}):
            with self.subTest(params=params):
                result = self.tool("check", id="bad", command=command, **params)
                structured = result["structuredContent"]
                self.assertTrue(result.get("isError"), params)
                self.assertIsNone(structured["receipt"], params)
                self.assertTrue(structured["reason"].startswith("invalid_"), structured["reason"])
                self.assertFalse(self.marker("invalid").exists())
        self.assertEqual(self.artifacts(), [])


class TargetedRepairTest(BudgetCase):
    def test_full_command_requires_final_and_returns_the_targeted_command(self):
        self.write_state(remaining=300)
        command = self.command("full")
        targeted = self.command("targeted")
        config = self.target_config(command, targeted)
        quoted = f"touch '{self.markers / 'full'}'"
        for params in ({"id": "A", "command": command},
                       {"id": "other-id", "command": quoted},
                       {"id": "A", "command": command, "final": False}):
            with self.subTest(params=params):
                result = self.tool("check", config=config, **params)
                self.assertCleanRefusal(result, "final_required", "full")
                self.assertEqual(result["structuredContent"]["targetedCommand"], targeted)
        final = self.tool("check", config=config, id="A", command=command, final=True)
        self.assertNotIn("isError", final)
        self.assertTrue(self.marker("full").exists())
        self.assertTrue(final["structuredContent"]["receipt"])

    def test_dirty_worktree_blocks_the_final_check(self):
        self.write_state(remaining=300)
        command = self.command("full")
        config = self.target_config(command, self.command("targeted"))
        untracked = self.worktree / "untracked.txt"
        untracked.write_text("dirty\n")
        result = self.tool("check", config=config, id="A", command=command, final=True)
        self.assertCleanRefusal(result, "dirty_final", "full")
        untracked.unlink()
        result = self.tool("check", config=config, id="A", command=command, final=True)
        self.assertNotIn("isError", result)
        self.assertTrue(self.marker("full").exists())

    def test_a_command_without_targeted_advice_is_not_classified_as_full(self):
        self.write_state(remaining=300)
        command = self.command("plain")
        config = dict(self.config, acceptanceItems=[{"id": "A", "checkId": "A", "command": command}])
        result = self.tool("check", config=config, id="A", command=command)
        self.assertNotIn("isError", result)
        self.assertTrue(self.marker("plain").exists())

    def test_targeted_receipt_does_not_cover_the_formal_acceptance_item(self):
        full = self.command("full")
        targeted = self.command("targeted")
        contract = pi_phase.validate_contract(
            self.contract([self.item(checkId="A1", command=full, targetedCommand=targeted)]),
            self.main)
        proc = subprocess.run([sys.executable, str(self.tools / "pi_check.py"),
                               "--output-dir", str(self.checks), "--id", "A1",
                               "--timeout-seconds", "30", "--", "touch", str(self.marker("targeted"))],
                              cwd=str(self.worktree), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        head = subprocess.check_output(["git", "-C", str(self.worktree), "rev-parse", "HEAD"],
                                       text=True).strip()
        checks = pi_evidence._scan_checks(self.checks)
        items, coverage, _gaps, _budget = pi_phase.evaluate_acceptance_items(
            contract, head, checks, self.checks)
        self.assertEqual(items[0]["status"], "failed")
        self.assertIn("argv", items[0]["reason"])
        self.assertEqual(coverage["covered"], 0)


class CodemodeEnvelopeTest(BudgetCase):
    def test_nested_check_without_a_recorded_outer_deadline_is_refused(self):
        self.write_state(remaining=300)
        result = self.tool("check", id="nested", command=self.command("nested"),
                           tool_call_id="call-1/1")
        structured = result["structuredContent"]
        self.assertCleanRefusal(result, "codemode_deadline_unknown", "nested")
        self.assertEqual(structured["requiredSeconds"], 60)

    def test_nested_check_inside_a_short_codemode_script_is_refused(self):
        self.write_state(remaining=300)
        result = self.codemode_then_check('// @options: {"timeout_ms": 1500}\nreturn 1;',
                                          id="nested", command=self.command("nested"))
        structured = result["structuredContent"]
        self.assertCleanRefusal(result, "codemode_deadline_too_short", "nested")
        self.assertEqual(structured["requiredSeconds"], 60)
        self.assertLess(structured["codemodeRemainingSeconds"], 3)

    def test_nested_check_within_the_codemode_envelope_runs(self):
        self.write_state(remaining=300)
        result = self.codemode_then_check('// @options: {"timeout_ms": 5000}\nreturn 1;',
                                          id="nested", command=self.command("nested"),
                                          estimatedSeconds=1)
        self.assertNotIn("isError", result)
        structured = result["structuredContent"]
        self.assertEqual(structured["estimateSource"], "call")
        self.assertTrue(self.marker("nested").exists())
        receipt = json.loads(Path(structured["receipt"]).read_text())
        self.assertLessEqual(receipt["deadline_at"] - receipt["started_at"], 5.5)

    def test_nested_requested_timeout_below_the_estimate_is_refused(self):
        self.write_state(remaining=300)
        result = self.codemode_then_check('// @options: {"timeout_ms": 20000}\nreturn 1;',
                                          id="nested-short", command=self.command("nested-short"),
                                          timeoutSeconds=1, estimatedSeconds=20)
        structured = result["structuredContent"]
        self.assertCleanRefusal(result, "timeout_below_estimate", "nested-short")
        self.assertEqual(structured["requiredSeconds"], 20)
        self.assertEqual(structured["allowedSeconds"], 1)


class ContractMetadataTest(BudgetCase):
    def test_old_contract_items_stay_byte_stable(self):
        normalized = pi_phase.validate_contract(self.contract([self.item()]), self.main)
        entry = normalized["acceptanceItems"][0]
        self.assertNotIn("targetedCommand", entry)
        self.assertNotIn("estimatedSeconds", entry)

    def test_new_optional_fields_are_validated(self):
        normalized = pi_phase.validate_contract(
            self.contract([self.item(targetedCommand="python3 -m unittest tests.test_a",
                                     estimatedSeconds=12)]), self.main)
        entry = normalized["acceptanceItems"][0]
        self.assertEqual(entry["targetedCommand"], "python3 -m unittest tests.test_a")
        self.assertEqual(entry["estimatedSeconds"], 12.0)
        for bad in ({"estimatedSeconds": 0}, {"estimatedSeconds": -1.0},
                    {"estimatedSeconds": True}, {"estimatedSeconds": "5"},
                    {"estimatedSeconds": float("inf")}, {"targetedCommand": ""},
                    {"targetedCommand": 7}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    pi_phase.validate_contract(self.contract([self.item(**bad)]), self.main)

    def test_contradictory_metadata_for_one_normalized_argv_is_rejected(self):
        for items in ([self.item(command="python3  -c pass", targetedCommand="t1"),
                       self.item(id="A2", command="python3 -c  pass", targetedCommand="t2")],
                      [self.item(command="python3 -c pass", targetedCommand="t1"),
                       self.item(id="A2", command="python3  -c  pass")]):
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    pi_phase.validate_contract(self.contract(items), self.main)
        identical = [self.item(command="python3 -c pass", targetedCommand="t1", estimatedSeconds=3),
                     self.item(id="A2", command="python3  -c pass", targetedCommand="t1",
                               estimatedSeconds=3)]
        normalized = pi_phase.validate_contract(self.contract(identical), self.main)
        self.assertEqual(len(normalized["acceptanceItems"]), 2)

    def test_contract_text_and_worker_config_carry_the_metadata(self):
        contract = self.contract([self.item(
            targetedCommand="python3 -m unittest tests.test_a", estimatedSeconds=30)])
        pi_phase.install_phase_contract(self.task_dir, contract, self.main, self.worktree)
        task = {"task": "T", "model": "m", "thinking": "max", "readOnly": False,
                "repo": str(self.main), "worktree": str(self.worktree),
                "taskDir": str(self.task_dir), "timeoutSeconds": 900,
                "constraints": [], "checks": []}
        text = pi_brief.compose_contract(task)
        self.assertIn("targeted_command: python3 -m unittest tests.test_a", text)
        self.assertIn("estimated_seconds: 30.0", text)
        self.assertIn("final:true", text)
        config_path = pi_brief.write_worker_config(self.task_dir, 1, task, text)
        config = json.loads(config_path.read_text())
        self.assertEqual(config["deadlinePath"], str(self.round_dir / "round.state.json"))
        self.assertEqual(config["acceptanceItems"], [{
            "id": "A1", "checkId": "A1", "command": "python3 -c pass",
            "targetedCommand": "python3 -m unittest tests.test_a", "estimatedSeconds": 30.0}])


class FreezeTest(BudgetCase):
    def test_version_and_frozen_snapshot_carry_the_new_runtime(self):
        root = Path(__file__).resolve().parent.parent
        self.assertEqual((RUNTIME / "VERSION").read_text().strip(), "0.8.0")
        manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], "0.8.0")
        self.assertIn("pi_worker.ts", pi_task.HELPER_FILES)
        self.assertEqual((self.tools / "pi_worker.ts").read_bytes(),
                         (RUNTIME / "pi_worker.ts").read_bytes())
        self.assertEqual((self.tools / "pi_brief.py").read_bytes(),
                         (RUNTIME / "pi_brief.py").read_bytes())
        self.assertEqual((self.tools / "pi_phase.py").read_bytes(),
                         (RUNTIME / "pi_phase.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
