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
elif mode == "twelve":
    for index in range(12):
        print(json.dumps({"type": "turn_start"}), flush=True)
        print(json.dumps({"type": "message_end", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "turn %d" % index}],
            "stopReason": "stop",
            "usage": {"input": 1, "output": 1}},
        }), flush=True)
        time.sleep(0.02)
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
        self.assertGreaterEqual(len(cases), 18)
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

    def test_normal_completion_within_turn_limit(self):
        bin_dir = make_stub_bin(self.tmp)
        fixture = make_session_fixture(self.tmp)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bin_dir) + os.pathsep + old_path
        os.environ["STUB_PI_MODE"] = "twelve"
        try:
            result = RUNNER.run_session(fixture, wall_seconds=60)
        finally:
            os.environ["PATH"] = old_path
            os.environ.pop("STUB_PI_MODE", None)
        self.assertIsNone(result["killed"])
        self.assertEqual(result["session_status"], "completed")
        self.assertEqual(result["assistant_turns"], 12)

    def test_count_progress_buffers_cross_chunk_lines(self):
        trace = self.tmp / "trace.jsonl"
        partial = '{"type": "turn_start"}'
        trace.write_bytes(partial.encode())
        starts, messages, offset, residual = RUNNER._count_progress(trace, 0)
        self.assertEqual((starts, messages), (0, 0))
        self.assertTrue(residual)
        with trace.open("ab") as stream:
            stream.write(b"\n")
        starts, messages, offset, residual = RUNNER._count_progress(trace, offset, residual)
        self.assertEqual(starts, 1)
        self.assertEqual(residual, b"")

    def test_guard_refusal_is_not_proactive(self):
        spec, fixture = RUNNER._control_trial(
            self.tmp, 2,
            [RUNNER._tool_call("c1", "check", {"id": "E1", "command": RUNNER.SCENARIOS[2]["items"][0]["command"],
                                                 "estimatedSeconds": 300}),
             RUNNER._refused("c1", "E1", "insufficient_budget")],
            [], [], {"summary": "budget refused", "completed": [],
                      "verification": {"E1": "refused"},
                      "ready_for_review": False, "acceptance_claimed": False})
        record = RUNNER.score_trial(spec, fixture, {"session_status": "completed", "exit_code": 0})
        self.assertTrue(record["family_success"])
        self.assertFalse(record["first_action_correct"])
        self.assertEqual(record["first_action_stance"], "guard_blocked")
        self.assertFalse(record["subscores"]["model_avoided"])
        self.assertTrue(record["subscores"]["guard_refused"])

    def test_check_baseline_detects_tamper_and_missing(self):
        root = self.tmp / "baseline-root"
        root.mkdir()
        (root / "raw.bin").write_bytes(b"original")
        baseline = {"evidence": {"raw.bin": {"sha256": RUNNER.sha256_bytes(b"original")}}}
        self.assertEqual(RUNNER.check_baseline(root, baseline), [])
        (root / "raw.bin").write_bytes(b"tampered")
        problems = RUNNER.check_baseline(root, baseline)
        self.assertTrue(any("hash mismatch" in problem for problem in problems))
        (root / "raw.bin").unlink()
        problems = RUNNER.check_baseline(root, baseline)
        self.assertTrue(any("missing" in problem for problem in problems))

    def test_ast_confinement_allows_scoring_only(self):
        locked = "MODEL = 'x'\ndef prepare_fixture():\n    return 1\ndef score_trial():\n    return 1\n"
        scoring = "MODEL = 'x'\ndef prepare_fixture():\n    return 1\ndef score_trial():\n    return 2\n"
        self.assertTrue(RUNNER.ast_confinement(locked, scoring)["ok"])
        scenario_change = "MODEL = 'y'\ndef prepare_fixture():\n    return 1\ndef score_trial():\n    return 2\n"
        self.assertFalse(RUNNER.ast_confinement(locked, scenario_change)["ok"])
        fixture_change = "MODEL = 'x'\ndef prepare_fixture():\n    return 9\ndef score_trial():\n    return 2\n"
        self.assertFalse(RUNNER.ast_confinement(locked, fixture_change)["ok"])

    def test_verify_evidence_rejects_wrong_model_manifest(self):
        out = self.tmp / "wrong-model"
        out.mkdir()
        RUNNER.write_json(out / "manifest.json", {"schemaVersion": 1, "model": "wrong/model"})
        problems, _summary, _limitations = RUNNER.verify_evidence(out)
        self.assertTrue(any("model" in problem for problem in problems))

    def test_report_generator_denominators_and_disclosures(self):
        results = []
        for entry in RUNNER.schedule():
            results.append({
                "trial": entry["trial"], "scenario": entry["scenario"],
                "family": entry["family"], "arm": entry["arm"], "rep": entry["rep"],
                "session_status": "completed", "family_success": True, "report_status": "complete",
                "first_action_stance": "proactive", "assistant_turns": 1, "wall_seconds": 1.0,
                "turn_start_count": 1, "usage": {"totalTokens": 10}, "guard_blocked": [],
                "claims_problems": [],
            })
        manifest = {
            "model": RUNNER.MODEL, "thinking": RUNNER.THINKING, "piVersion": "1.0.0",
            "wallSeconds": RUNNER.WALL_SECONDS, "maxAssistantTurns": RUNNER.MAX_ASSISTANT_TURNS,
            "results": results, "controls": {"cases": []}, "controlsPassed": True,
            "rescore": [], "harnessSha256": "a" * 64, "scenarioSpecsSha256": "b" * 64,
            "scheduleSha256": "c" * 64, "treatment": {"files": {}},
        }
        report = RUNNER.build_report(manifest)
        self.assertIn("/3 |", report)
        self.assertNotIn("/6", report)
        self.assertIn("acceptance_claimed", report)
        self.assertIn("Reported cost was zero/absent", report)

    # ------------------------------------------------------------------
    # Evidence integrity
    # ------------------------------------------------------------------
    def test_verify_rejects_incomplete_and_stale_manifests(self):
        out = self.tmp / "evidence"
        out.mkdir()
        treatment = RUNNER.extract_arms(out)
        manifest = RUNNER.new_manifest(out, treatment)
        RUNNER.save_manifest(out, manifest)
        problems, _summary, _limitations = RUNNER.verify_evidence(out)
        self.assertTrue(any("36-trial" in problem for problem in problems))

        manifest["harnessSha256"] = "0" * 64
        RUNNER.save_manifest(out, manifest)
        problems, _summary, _limitations = RUNNER.verify_evidence(out)
        self.assertTrue(any("harness hash" in problem for problem in problems))

    def test_verify_rejects_tampered_treatment(self):
        out = self.tmp / "evidence-tamper"
        out.mkdir()
        treatment = RUNNER.extract_arms(out)
        manifest = RUNNER.new_manifest(out, treatment)
        treatment["refs"]["old"] = "0" * 40
        manifest["treatment"] = treatment
        RUNNER.save_manifest(out, manifest)
        problems, _summary, _limitations = RUNNER.verify_evidence(out)
        self.assertTrue(any("treatment refs" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
