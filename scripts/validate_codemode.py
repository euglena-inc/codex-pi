#!/usr/bin/env python3
"""Reproducible real Pi 1.0.0+ / QuickJS codemode integration probe.

It runs the candidate worker extension (`runtime/pi_worker.ts`) inside the actual
installed Pi SDK session pipeline with a local scripted provider: no network, no
credentials and no paid provider. The companion `scripts/codemode_probe.mjs`
records raw observations; every expectation and negative control is asserted here.

Required properties: native codemode registration and the absent `models` global,
structured check success/failure, nested forbidden writes blocked by the same
guard, bounded/omitted/invalid script deadlines with real cancellation, and
`store`/`load` commit/resume semantics.

Missing Node or a Pi older than 1.0.0 fails nonzero; it never skips and reports
success. A zero exit only proves execution finished, not acceptance.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
PROBE = ROOT / "scripts" / "codemode_probe.mjs"
MIN_PI_VERSION = (1, 0, 0)
PROBE_TIMEOUT_SECONDS = 540


class ProbeUnavailable(RuntimeError):
    """A required external dependency is missing; the probe must fail, never skip."""


def resolve_pi(pi_bin: str) -> tuple:
    found = shutil.which(pi_bin)
    if not found:
        raise ProbeUnavailable(f"Pi executable {pi_bin!r} is not on PATH")
    package_root = None
    for candidate in (Path(found).resolve(), *Path(found).resolve().parents):
        manifest = candidate / "package.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("name") == "@earendil-works/pi-coding-agent":
            package_root = candidate
            break
    if package_root is None:
        raise ProbeUnavailable(f"cannot find the Pi package above {found}")
    entry = package_root / "dist" / "index.js"
    if not entry.is_file():
        raise ProbeUnavailable(f"the Pi package has no dist/index.js: {package_root}")
    proc = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=60)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", (proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0 or match is None:
        raise ProbeUnavailable(f"cannot read the Pi version (`{found} --version` exited {proc.returncode})")
    version = tuple(int(part) for part in match.groups())
    if version < MIN_PI_VERSION:
        raise ProbeUnavailable(f"Pi {'.'.join(match.groups())} is too old; the probe needs >= 1.0.0")
    return found, package_root, entry, ".".join(match.groups())


def snapshot_tools(destination: Path) -> None:
    sys.path.insert(0, str(RUNTIME))
    import pi_task  # noqa: PLC0415 - local import keeps the header dependency-free
    pi_task.snapshot_helpers(RUNTIME, destination)


def build_workdir(root: Path) -> dict:
    main = root / "main"
    main.mkdir()
    (main / "secret.txt").write_text("SECRET\n", encoding="utf-8")
    worktree = root / "wt"
    worktree.mkdir()
    tools = root / "tools"
    snapshot_tools(tools)
    round_dir = root / "round"
    checks = round_dir / "round.checks"
    checks.mkdir(parents=True)
    session_dir = root / "session"
    session_dir.mkdir()
    agent_dir = root / "agent"
    agent_dir.mkdir()
    worker_config = {
        "schemaVersion": 1, "task": "codemode-probe", "round": 1,
        "repo": str(main), "worktree": str(worktree),
        "forbiddenRoots": [str(main)],
        "allowedRoots": [str(worktree), str(tools), str(checks)],
        "bashDefaultTimeoutSeconds": 2, "bashCeilingSeconds": 6,
        "checkTimeoutSeconds": 30, "checksDir": str(checks), "toolsDir": str(tools),
        "python": sys.executable, "phase": False, "settleQuotaPath": None,
        "roundDir": str(round_dir), "contract": "codemode probe contract",
    }
    worker_config_path = round_dir / "worker.json"
    worker_config_path.write_text(json.dumps(worker_config, indent=2), encoding="utf-8")
    return {"root": root, "main": main, "worktree": worktree, "tools": tools,
            "roundDir": round_dir, "checks": checks, "sessionDir": session_dir,
            "agentDir": agent_dir, "workerConfig": worker_config_path}


def run_probe_layout(workdir: Path, package_root: Path, entry: Path, pi_version: str) -> dict:
    report_path = workdir / "report.json"
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("CODEX_PI_") and key != "PI_SESSION_FILE"}
    env.update({
        "CODEX_PI_WORKER_CONFIG": str(workdir / "round" / "worker.json"),
        "PI_PACKAGE_ROOT": str(package_root),
        "PI_PACKAGE_ENTRY": str(entry),
        "PI_CODEMODE_EXTENSION": str(RUNTIME / "pi_worker.ts"),
        "PI_PROBE_REPORT": str(report_path),
        "PI_PI_VERSION": pi_version,
    })
    node = shutil.which("node")
    if node is None:
        raise ProbeUnavailable("node is required on PATH for the real QuickJS probe")
    try:
        proc = subprocess.run([node, str(PROBE), str(workdir)], capture_output=True, text=True,
                              env=env, timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"codemode probe timed out after {PROBE_TIMEOUT_SECONDS}s") from exc
    report = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except ValueError:
            report = None
    if proc.returncode != 0 or report is None:
        detail = (report or {}).get("fatal") or (proc.stderr or "")[-1200:] or proc.stdout[-1200:]
        raise AssertionError(f"codemode probe failed (exit {proc.returncode}): {detail}")
    report["piVersionExpected"] = pi_version
    return report


class CodemodeIntegrationTest(unittest.TestCase):
    maxDiff = None
    report = None
    layout = None

    @classmethod
    def setUpClass(cls):
        if cls.report is None or cls.layout is None:
            raise ProbeUnavailable("the probe was not prepared; run this script directly")

    def case(self, name: str) -> dict:
        cases = self.report["cases"]
        self.assertIn(name, cases, f"the probe did not record case {name}")
        return cases[name]

    def case_text(self, case: dict) -> str:
        return "\n".join(entry["text"] for entry in case["codemode"])

    def test_worker_load_proof_binds_native_codemode(self):
        marker = self.report["readyMarker"]
        self.assertIsInstance(marker, dict, "the worker extension never wrote worker.ready")
        self.assertIs(marker.get("codemode"), True)
        self.assertEqual(self.report["piVersion"], self.report["piVersionExpected"])
        self.assertEqual(marker.get("piVersion"), self.report["piVersion"])
        self.assertIn("codemode", self.report["activeTools"])
        self.assertIn("codemode", self.report["allTools"])
        for name in ("check", "progress", "readiness"):
            self.assertIn(name, self.report["allTools"])
            self.assertIn(name, self.report["toolsWithOutputSchema"])
        self.assertIs(self.report["readyBeforeFirstTool"], True,
                      "worker.ready must exist before the first tool execution")

    def test_models_global_is_absent_from_scripts(self):
        case = self.case("models")
        self.assertIsNone(case["error"])
        self.assertEqual(case["leftover"], 0)
        text = self.case_text(case)
        self.assertIn('"models":"undefined"', text)
        self.assertIn('"tools":"object"', text)

    def test_structured_check_success_and_semantic_failure(self):
        ok = self.case("check-ok")
        self.assertIsNone(ok["error"])
        self.assertEqual(ok["leftover"], 0)
        ok_text = self.case_text(ok)
        self.assertIn('"ok":true', ok_text)
        self.assertIn('"exit_code":0', ok_text)
        self.assertEqual(len(ok["codemode"]), 1)
        self.assertIs(ok["codemode"][0]["isError"], False)
        ok_nested = [entry for entry in ok["nestedEnds"] if entry["tool"] == "check"]
        self.assertEqual(len(ok_nested), 1)
        self.assertIs(ok_nested[0]["isError"], False)

        bad = self.case("check-fail")
        self.assertIsNone(bad["error"])
        self.assertEqual(bad["leftover"], 0)
        bad_text = self.case_text(bad)
        self.assertIn('"ok":false', bad_text)
        self.assertIn('"exit":1', bad_text)
        bad_nested = [entry for entry in bad["nestedEnds"] if entry["tool"] == "check"]
        self.assertEqual(len(bad_nested), 1)
        self.assertIs(bad_nested[0]["isError"], True)

    def test_nested_write_is_guarded_and_allowed_write_succeeds(self):
        case = self.case("nested-write")
        self.assertIsNone(case["error"])
        self.assertEqual(case["leftover"], 0)
        text = self.case_text(case)
        self.assertIn('"blocked":"forbidden-path:', text)
        self.assertEqual((self.layout["main"] / "secret.txt").read_text(encoding="utf-8"), "SECRET\n")
        self.assertEqual((self.layout["worktree"] / "nested-ok.txt").read_text(encoding="utf-8"), "ok")
        writes = [entry for entry in case["nestedEnds"] if entry["tool"] == "write"]
        self.assertEqual(len(writes), 2)
        self.assertEqual(sorted(bool(entry["isError"]) for entry in writes), [False, True])
        blocked = [entry for entry in self.report["blocks"] if entry.get("rule") == "forbidden-path"]
        self.assertTrue(blocked, "the nested forbidden write must be recorded in worker-blocks.jsonl")
        self.assertTrue(any(entry.get("tool") == "write" for entry in blocked))

    def test_omitted_and_excessive_deadlines_are_bounded(self):
        default = self.case("timeout-default")
        self.assertIsNone(default["error"])
        self.assertEqual(default["leftover"], 0)
        self.assertLess(default["elapsedMs"], 4500,
                        "an omitted deadline must use the bounded default, not run unbounded")
        self.assertTrue(default["codemode"][0]["isError"])
        self.assertIn("timed out", self.case_text(default).lower())

        excess = self.case("timeout-excess")
        self.assertIsNone(excess["error"])
        self.assertEqual(excess["leftover"], 0)
        self.assertGreaterEqual(excess["elapsedMs"], 4500,
                                "an excessive deadline must be clamped to the command cap")
        self.assertLess(excess["elapsedMs"], 20000)
        self.assertTrue(excess["codemode"][0]["isError"])

    def test_invalid_options_fail_closed_before_execution(self):
        case = self.case("invalid-options")
        self.assertIsNone(case["error"])
        self.assertEqual(case["leftover"], 0)
        self.assertEqual(len(case["codemode"]), 1)
        self.assertTrue(case["codemode"][0]["isError"])
        self.assertIn("invalid codemode source", case["codemode"][0]["text"])
        self.assertEqual(case["nestedStarts"], [], "a blocked script must not execute nested calls")

    def test_cancellation_propagates_to_nested_tools(self):
        case = self.case("abort")
        self.assertIs(self.report.get("abortPidAlive"), False,
                      "the nested bash child must be gone after the script is cancelled")
        self.assertTrue(case["codemode"], "the aborted codemode call must still produce a result")
        self.assertTrue(case["codemode"][0]["isError"])
        lowered = self.case_text(case).lower()
        self.assertTrue("abort" in lowered or "cancel" in lowered, self.case_text(case))
        self.assertTrue(any(entry["tool"] == "bash" for entry in case["nestedEnds"]))

    def test_store_success_commits_and_resumes(self):
        ok = self.case("store-ok")
        self.assertIsNone(ok["error"])
        self.assertEqual(ok["leftover"], 0)
        self.assertIn('"value":41', self.case_text(ok))
        branch = self.case("store-branch")
        self.assertIn('"ok":41', self.case_text(branch))
        self.assertIn('"bad":"undefined"', self.case_text(branch))
        self.assertIsNotNone(self.report.get("sessionFile"))
        resume = self.case("store-resume")
        self.assertIsNone(resume["error"])
        resume_text = self.case_text(resume)
        self.assertIn('"ok":41', resume_text)
        self.assertIn('"bad":"undefined"', resume_text)

    def test_failed_script_does_not_commit_store(self):
        case = self.case("store-fail")
        self.assertIsNone(case["error"])
        self.assertEqual(case["leftover"], 0)
        self.assertEqual(len(case["codemode"]), 1)
        self.assertTrue(case["codemode"][0]["isError"])
        self.assertIn("probe failure", self.case_text(case))
        for name in ("store-branch", "store-resume"):
            self.assertNotIn('"bad":7', self.case_text(self.case(name)))


def main() -> int:
    try:
        pi_bin, package_root, entry, pi_version = resolve_pi(os.environ.get("PI_BIN", "pi"))
        if shutil.which("node") is None:
            raise ProbeUnavailable("node is required on PATH for the real QuickJS probe")
    except ProbeUnavailable as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    temporary = tempfile.TemporaryDirectory(prefix="codex-pi-codemode-probe-")
    keep = bool(os.environ.get("CODEX_PI_PROBE_KEEP"))
    try:
        layout = build_workdir(Path(temporary.name).resolve())
        report = run_probe_layout(layout["root"], package_root, entry, pi_version)
        print(f"probe: pi={pi_version} node={report.get('node')} "
              f"model={report.get('model')} cases={len(report.get('cases', {}))}")
        CodemodeIntegrationTest.report = report
        CodemodeIntegrationTest.layout = layout
        suite = unittest.TestLoader().loadTestsFromTestCase(CodemodeIntegrationTest)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if result.wasSuccessful():
            print("probe properties: native=ok models-absent=ok structured=ok nested-guard=ok "
                  "deadline=ok cancel=ok store=ok")
        else:
            failed = ",".join(test.id() for test, _ in result.failures + result.errors)
            print(f"probe properties: failed={failed}")
        return 0 if result.wasSuccessful() else 1
    finally:
        if keep:
            print(f"probe evidence retained outside tracked files: {temporary.name}")
        else:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
