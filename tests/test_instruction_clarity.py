"""Offline tests for the instruction-clarity pilot runner.

These tests never launch a model. A stub ``pi`` executable exercises the
per-session wall and assistant-turn enforcement, and the scorer runs only on
hand-authored traces.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

import importlib.util

ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = ROOT / "scripts" / "validate_instruction_clarity.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("validate_instruction_clarity", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_runner()


STUB_PI = r"""#!/usr/bin/env python3
import json, os, sys, time

mode = os.environ.get("STUB_PI_MODE", "turns")
if mode == "hang":
    print(json.dumps({"type": "turn_start"}), flush=True)
    time.sleep(120)
else:
    for index in range(20):
        print(json.dumps({"type": "turn_start"}), flush=True)
        print(json.dumps({
            "type": "message_end",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "turn %d" % index}],
                        "stopReason": "stop",
                        "usage": {"input": 1, "output": 1}},
        }), flush=True)
        time.sleep(0.05)
"""


def make_stub_bin(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "pi"
    stub.write_text(STUB_PI, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def make_session_fixture(root: Path) -> dict:
    worktree = root / "wt"
    task_dir = root / "task"
    worktree.mkdir(parents=True, exist_ok=True)
    (task_dir / "session").mkdir(parents=True, exist_ok=True)
    brief = task_dir / "brief.md"
    brief.write_text("stub prompt\n", encoding="utf-8")
    worker_config = task_dir / "worker.json"
    worker_config.write_text("{}", encoding="utf-8")
    return {
        "trial": "t99", "scenario": 1, "family": "stub", "arm": "old", "rep": 1,
        "worktree": str(worktree), "task_dir": str(task_dir), "brief": str(brief),
        "worker_config": str(worker_config),
        "trace": str(root / "session.jsonl"), "stderr": str(root / "session.err"),
        "checks_dir": str(task_dir / "checks"), "start_head": "a" * 40,
    }


class InstructionClarityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="clarity-tests-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # ------------------------------------------------------------------
    # Frozen material and treatment isolation
    # ------------------------------------------------------------------
    def test_scenario_specs_hash_is_stable_and_hashed(self):
        first = RUNNER.scenario_specs_hash()
        self.assertEqual(first, RUNNER.scenario_specs_hash())
        self.assertEqual(len(first), 64)
        self.assertEqual(len(RUNNER.schedule()), 36)
        arms = [entry["arm"] for entry in RUNNER.schedule()]
        self.assertEqual(arms.count("old"), 18)
        self.assertEqual(arms.count("new"), 18)
        self.assertEqual(len({entry["trial"] for entry in RUNNER.schedule()}), 36)

    def test_treatment_sources_differ_only_in_instruction_text(self):
        treatment = RUNNER.extract_arms(self.tmp / "arms-out")
        brief = treatment["isolation"]["pi_brief"]
        self.assertTrue(brief["outside_text_builders_identical"])
        self.assertTrue(brief["changed_lines_in_text_builders"])
        worker = treatment["isolation"]["pi_worker_ts"]
        self.assertGreaterEqual(worker["changed_lines"], 5)
        self.assertEqual(sorted(worker["ranges"]), ["check", "progress", "readiness"])
        self.assertEqual(RUNNER.ts_token_normalize("const a = f(1);"),
                         RUNNER.ts_token_normalize("const a = f(1);"))
        self.assertNotEqual(RUNNER.ts_token_normalize("const a = f(1);"),
                            RUNNER.ts_token_normalize("const a = f(2);"))

    def test_ts_isolation_rejects_code_change(self):
        old = 'const a = "one"; function g() { return 1; }'
        new = 'const a = "two"; function g() { return 2; }'
        with self.assertRaises(ValueError):
            RUNNER.ts_string_only_isolation(old, new)

    # ------------------------------------------------------------------
    # Scorer negative controls
    # ------------------------------------------------------------------
    def test_negative_controls_all_pass(self):
        cases = RUNNER.run_negative_controls()
        failures = [case for case in cases if not case["ok"]]
        self.assertGreaterEqual(len(cases), 13)
        self.assertEqual(failures, [], f"control failures: {failures}")

    def test_missing_trace_is_not_a_pass(self):
        spec = RUNNER.SCENARIOS[5]
        base = self.tmp / "empty"
        (base / "wt").mkdir(parents=True)
        (base / "checks").mkdir()
        fixture = {
            "trial": "t99", "scenario": 5, "family": spec["family"], "arm": "old", "rep": 1,
            "worktree": str(base / "wt"), "checks_dir": str(base / "checks"),
            "trace": str(base / "session.jsonl"), "start_head": "a" * 40,
            "expected_readiness": None,
        }
        record = RUNNER.score_trial(spec, fixture, {"session_status": "spawn_error"})
        self.assertFalse(record["family_success"])
        self.assertFalse(record["evidence_complete"])

    def test_receipt_head_mismatch_is_not_covered(self):
        # The evaluator must not treat a receipt bound to another commit as
        # candidate evidence; the readiness fixture records the ground truth.
        spec = RUNNER.SCENARIOS[6]
        base = self.tmp / "head-mismatch"
        (base / "wt").mkdir(parents=True)
        checks = base / "checks"
        checks.mkdir()
        (checks / "R1-deadbeef.json").write_text(json.dumps({
            "id": "R1", "argv": ["python3", "-c", "print('ready')"], "exit_code": 0,
            "dirty": False, "head": "b" * 40,
        }), encoding="utf-8")
        trace = base / "session.jsonl"
        trace.write_text(json.dumps({"type": "message_end", "message": {
            "role": "assistant", "content": [{"type": "text",
                                              "text": '{"summary":"x","completed":["R1"],'
                                                      '"verification":{"R1":"passed","R2":"not_run"},'
                                                      '"ready_for_review":true,"acceptance_claimed":false}'}],
            "stopReason": "stop"}}), encoding="utf-8")
        fixture = {
            "trial": "t99", "scenario": 6, "family": spec["family"], "arm": "old", "rep": 1,
            "worktree": str(base / "wt"), "checks_dir": str(checks), "trace": str(trace),
            "start_head": "a" * 40,
            "expected_readiness": {"status": "unknown", "coverage": None},
        }
        record = RUNNER.score_trial(spec, fixture, {"session_status": "completed"})
        self.assertFalse(record["family_success"])

    # ------------------------------------------------------------------
    # Session enforcement with a stub Pi (no model)
    # ------------------------------------------------------------------
    def test_turn_limit_is_enforced(self):
        bin_dir = make_stub_bin(self.tmp)
        fixture = make_session_fixture(self.tmp)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bin_dir) + os.pathsep + old_path
        os.environ["STUB_PI_MODE"] = "turns"
        try:
            result = RUNNER.run_session(fixture, wall_seconds=60)
        finally:
            os.environ["PATH"] = old_path
            os.environ.pop("STUB_PI_MODE", None)
        self.assertEqual(result["killed"], "turn_limit")
        self.assertGreater(result["assistant_turns"], RUNNER.MAX_ASSISTANT_TURNS)

    def test_wall_timeout_is_enforced(self):
        bin_dir = make_stub_bin(self.tmp)
        fixture = make_session_fixture(self.tmp)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bin_dir) + os.pathsep + old_path
        os.environ["STUB_PI_MODE"] = "hang"
        try:
            started = time.monotonic()
            result = RUNNER.run_session(fixture, wall_seconds=1.5)
            elapsed = time.monotonic() - started
        finally:
            os.environ["PATH"] = old_path
            os.environ.pop("STUB_PI_MODE", None)
        self.assertEqual(result["killed"], "wall_timeout")
        self.assertLess(elapsed, 12)

    # ------------------------------------------------------------------
    # Evidence integrity
    # ------------------------------------------------------------------
    def test_verify_rejects_incomplete_and_stale_manifests(self):
        out = self.tmp / "evidence"
        out.mkdir()
        treatment = RUNNER.extract_arms(out)
        manifest = RUNNER.new_manifest(out, treatment)
        RUNNER.save_manifest(out, manifest)
        problems, _summary = RUNNER.verify_evidence(out)
        self.assertTrue(any("36-trial" in problem for problem in problems))

        manifest["harnessSha256"] = "0" * 64
        RUNNER.save_manifest(out, manifest)
        problems, _summary = RUNNER.verify_evidence(out)
        self.assertTrue(any("harness hash" in problem for problem in problems))

    def test_verify_rejects_tampered_treatment(self):
        out = self.tmp / "evidence-tamper"
        out.mkdir()
        treatment = RUNNER.extract_arms(out)
        manifest = RUNNER.new_manifest(out, treatment)
        treatment["refs"]["old"] = "0" * 40
        manifest["treatment"] = treatment
        RUNNER.save_manifest(out, manifest)
        problems, _summary = RUNNER.verify_evidence(out)
        self.assertTrue(any("treatment refs" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
