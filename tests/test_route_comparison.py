from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compare_routes.py"
BASELINE = "1" * 40
CANDIDATE = "2" * 40


def record(route: str, *, project: str = "synthetic-project", pair: str = "trial-1",
           case: str = "synthetic-case", baseline: str = BASELINE,
           acceptance_key: str = "acceptance-v1", outcome: str = "accepted",
           model_verified: bool = True, usage=None) -> dict:
    return {
        "schemaVersion": 1,
        "project": project,
        "pair": pair,
        "case": case,
        "baseline": baseline,
        "acceptanceKey": acceptance_key,
        "route": route,
        "candidate": CANDIDATE if outcome == "accepted" else None,
        "outcome": outcome,
        "modelVerified": model_verified,
        "solUsage": usage,
    }


def complete_usage(input_tokens: int, cached_tokens: int, output_tokens: int) -> dict:
    return {"complete": True, "basis": "exclusive-requests", "inputTokens": input_tokens,
            "cachedInputTokens": cached_tokens, "outputTokens": output_tokens}


class RouteComparisonCliTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="route-compare-synthetic-")
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

    def write_json(self, name: str, value) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def run_cli(self, *paths: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *(str(path) for path in paths)],
                              text=True, capture_output=True, check=False)

    def run_records(self, records: list[dict]) -> subprocess.CompletedProcess[str]:
        return self.run_cli(self.write_json("records.json", records))

    def parse_lines(self, result) -> list[dict]:
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_positive_and_negative_savings_are_relative_to_codex_pi(self):
        records = [
            record("sol-luna", pair="z-positive", usage=complete_usage(500, 100, 100)),
            record("codex-pi", pair="z-positive", usage=complete_usage(1000, 200, 200)),
            record("sol-luna", pair="a-negative", usage=complete_usage(200, 50, 40)),
            record("codex-pi", pair="a-negative", usage=complete_usage(100, 25, 20)),
        ]
        result = self.run_records(records)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.parse_lines(result)
        self.assertEqual([line["pair"] for line in lines], ["a-negative", "z-positive"])
        positive = lines[1]["comparison"]
        self.assertTrue(positive["comparable"])
        self.assertEqual(positive["savingsPercent"], {
            "input": 50.0, "uncachedInput": 50.0, "output": 50.0, "total": 50.0,
        })
        negative = lines[0]["comparison"]
        self.assertTrue(negative["comparable"])
        self.assertEqual(negative["savingsPercent"]["input"], -100.0)
        self.assertEqual(negative["savingsPercent"]["uncachedInput"], -100.0)
        self.assertEqual(negative["savingsPercent"]["output"], -100.0)
        self.assertEqual(negative["savingsPercent"]["total"], -100.0)

    def test_unknown_usage_and_missing_complete_counters_are_not_zero(self):
        codex = record("codex-pi", usage=None)
        luna_usage = {"complete": True, "basis": "exclusive-requests", "outputTokens": 20}
        result = self.run_records([codex, record("sol-luna", usage=luna_usage)])
        self.assertEqual(result.returncode, 0, result.stderr)
        comparison = self.parse_lines(result)[0]["comparison"]
        self.assertFalse(comparison["comparable"])
        self.assertIsNotNone(comparison["reason"])
        self.assertTrue(all(value is None for value in comparison["savingsPercent"].values()))
        self.assertIsNone(comparison["metricReasons"].get("input"))

    def test_missing_sol_usage_field_is_rejected_without_private_echo(self):
        row = record("codex-pi", usage=None)
        del row["solUsage"]
        path = self.write_json("private-input.json", [row])
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("solUsage is required", result.stderr)
        self.assertNotIn(str(path), result.stderr)

    def test_missing_cache_usage_keeps_other_metrics_comparable(self):
        pi_usage = {"complete": True, "basis": "exclusive-requests", "inputTokens": 100,
                    "outputTokens": 20}
        luna_usage = complete_usage(80, 20, 10)
        result = self.run_records([record("codex-pi", usage=pi_usage),
                                   record("sol-luna", usage=luna_usage)])
        self.assertEqual(result.returncode, 0, result.stderr)
        comparison = self.parse_lines(result)[0]["comparison"]
        self.assertTrue(comparison["comparable"])
        self.assertEqual(comparison["savingsPercent"]["input"], 20.0)
        self.assertIsNone(comparison["savingsPercent"]["uncachedInput"])
        self.assertEqual(comparison["metricReasons"]["uncachedInput"],
                         "metric is unknown for one or both routes")
        self.assertEqual(comparison["savingsPercent"]["output"], 50.0)
        self.assertEqual(comparison["savingsPercent"]["total"], 25.0)

    def test_incomplete_usage_is_visible_but_incomparable(self):
        partial = {"complete": False, "inputTokens": 100, "outputTokens": 10}
        result = self.run_records([record("codex-pi", usage=partial),
                                   record("sol-luna", usage=complete_usage(90, 10, 10))])
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.parse_lines(result)[0]
        self.assertEqual(output["routes"]["codex-pi"]["status"], "ineligible")
        self.assertEqual(output["routes"]["codex-pi"]["usage"]["inputTokens"], 100)
        self.assertFalse(output["comparison"]["comparable"])
        self.assertTrue(all(value is None for value in output["comparison"]["savingsPercent"].values()))

    def test_failed_or_unverified_runs_do_not_compare(self):
        result = self.run_records([
            record("codex-pi", outcome="rejected", usage=complete_usage(100, 0, 20)),
            record("sol-luna", model_verified=False, usage=complete_usage(50, 0, 10)),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.parse_lines(result)[0]
        self.assertEqual(output["routes"]["codex-pi"]["reason"], ["outcome is rejected"])
        self.assertEqual(output["routes"]["sol-luna"]["reason"], ["model is unverified"])
        self.assertFalse(output["comparison"]["comparable"])
        self.assertTrue(all(value is None for value in output["comparison"]["savingsPercent"].values()))

    def test_mismatched_project_baseline_and_acceptance_key_are_separate_groups(self):
        pairs = []
        for field, pi_value, luna_value in (
            ("project", "project-a", "project-b"),
            ("baseline", "3" * 40, "4" * 64),
            ("acceptance_key", "acceptance-a", "acceptance-b"),
        ):
            pi_fields = {field: pi_value}
            luna_fields = {field: luna_value}
            pairs.extend([
                record("codex-pi", pair="mismatch", usage=complete_usage(10, 0, 2), **pi_fields),
                record("sol-luna", pair="mismatch", usage=complete_usage(5, 0, 1), **luna_fields),
            ])
        result = self.run_records(pairs)
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = self.parse_lines(result)
        self.assertEqual(len(outputs), 6)
        for output in outputs:
            self.assertFalse(output["comparison"]["comparable"])
            self.assertIsNotNone(output["comparison"]["reason"])
            self.assertTrue(all(value is None for value in output["comparison"]["savingsPercent"].values()))

    def test_duplicate_route_in_group_is_an_actionable_error(self):
        rows = [record("codex-pi", usage=complete_usage(10, 0, 2)),
                record("codex-pi", usage=complete_usage(11, 0, 2))]
        path = self.write_json("duplicate.json", rows)
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("duplicate codex-pi record", result.stderr)
        self.assertNotIn(str(path), result.stderr)

    def test_duplicate_json_keys_at_any_depth_are_rejected_without_echoing_keys(self):
        base = json.dumps(record("codex-pi", usage=complete_usage(10, 0, 2)))
        raw_records = (
            base.replace('"modelVerified": true',
                        '"modelVerified": true, "modelVerified": false', 1),
            base.replace('"inputTokens": 10',
                         '"inputTokens": 10, "inputTokens": 11', 1),
        )
        duplicated_keys = ("modelVerified", "inputTokens")
        for index, (raw, key) in enumerate(zip(raw_records, duplicated_keys), start=1):
            with self.subTest(key=key):
                path = self.tmp / f"duplicate-json-{index}.json"
                path.write_text(raw, encoding="utf-8")
                result = self.run_cli(path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("duplicate JSON object key", result.stderr)
                self.assertNotIn(key, result.stderr)
                self.assertNotIn(str(path), result.stderr)

    def test_boolean_negative_and_out_of_range_token_counts_are_rejected(self):
        for usage, expected in (
            ({"complete": True, "basis": "exclusive-requests", "inputTokens": True,
              "outputTokens": 1}, "inputTokens must be a nonnegative integer"),
            ({"complete": True, "basis": "exclusive-requests", "inputTokens": 1,
              "outputTokens": -1}, "outputTokens must be a nonnegative integer"),
            ({"complete": True, "basis": "exclusive-requests", "inputTokens": 1,
              "cachedInputTokens": 2, "outputTokens": 1}, "cachedInputTokens cannot exceed inputTokens"),
            ({"complete": True, "basis": "exclusive-requests", "inputTokens": 2 ** 63,
              "outputTokens": 1}, "no greater than 9223372036854775807 (int64 maximum)"),
        ):
            with self.subTest(usage=usage):
                result = self.run_records([record("codex-pi", usage=usage)])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_schema_identifiers_route_outcome_and_candidate_are_validated(self):
        mutations = (
            (lambda row: row.update(schemaVersion=True), "schemaVersion must be integer 1"),
            (lambda row: row.update(project="  "), "project must be a nonempty string"),
            (lambda row: row.update(baseline="A" * 40), "baseline must be a 40- or 64-character"),
            (lambda row: row.update(route="other"), "route must be codex-pi or sol-luna"),
            (lambda row: row.update(outcome="passed"), "outcome must be accepted, rejected, blocked, or incomplete"),
            (lambda row: row.update(candidate="not-a-sha"), "accepted outcome requires a 40- or 64-character"),
            (lambda row: row.update(modelVerified=1), "modelVerified must be a boolean"),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                row = record("codex-pi", usage=complete_usage(10, 0, 2))
                mutate(row)
                result = self.run_records([row])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)

    def test_zero_codex_pi_baseline_yields_unknown_percentage_for_that_metric(self):
        result = self.run_records([
            record("codex-pi", usage=complete_usage(0, 0, 20)),
            record("sol-luna", usage=complete_usage(10, 5, 10)),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        comparison = self.parse_lines(result)[0]["comparison"]
        self.assertTrue(comparison["comparable"])
        self.assertIsNone(comparison["savingsPercent"]["input"])
        self.assertIsNone(comparison["savingsPercent"]["uncachedInput"])
        self.assertEqual(comparison["metricReasons"]["input"],
                         "codex-pi baseline for this metric is zero")
        self.assertEqual(comparison["savingsPercent"]["output"], 50.0)
        self.assertEqual(comparison["savingsPercent"]["total"], 0.0)

    def test_malformed_later_file_emits_no_partial_output_or_private_details(self):
        first = self.write_json("valid.json", [
            record("codex-pi", usage=complete_usage(10, 0, 2)),
            record("sol-luna", usage=complete_usage(5, 0, 1)),
        ])
        bad = self.tmp / "private-file-marker.json"
        bad.write_text('{"secret":"RAW-PRIVATE-CONTENT"', encoding="utf-8")
        result = self.run_cli(first, bad)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("input 2: invalid JSON", result.stderr)
        self.assertNotIn("RAW-PRIVATE-CONTENT", result.stderr)
        self.assertNotIn(str(bad), result.stderr)


if __name__ == "__main__":
    unittest.main()
