#!/usr/bin/env python3
"""Cross-project persistent Pi worker lifecycle for the Codex-Pi plugin.

Pure Python stdlib. This runtime never executes the Codex CLI and never starts a
Codex agent; the only coding-agent process it launches is Pi, or the executable
named by the explicit ``PI_BIN`` test/override environment variable.

Execution facts live under ``<git-common-dir>/codex-pi/tasks/<task>/``. Raw
round logs, briefs and receipts are immutable once written. A detached worker
holds the task lock until it has finished the owned Pi process group.

Modules: ``pi_core`` (filesystem, locks, config, layout), ``pi_evidence`` (receipt scanning),
``pi_phase`` (contracts, readiness, settle view), ``pi_brief`` (brief, contract text,
``worker.json``), ``pi_supervisor`` (the detached round supervisor). This file keeps
admission, status/result building and the CLI.

Exit code 0 means the Pi process completed execution. It is never acceptance
PASS; acceptance stays with the project's own checks and the main review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

# The frozen helper snapshot is immutable evidence: never write bytecode caches
# into the task tools directory. Must run before the local imports below.
sys.dont_write_bytecode = True

from pi_brief import ensure_round_inputs
from pi_core import (
    ACTIVE_STATES,
    ALLOWED_MODELS,
    CONFIG_KEYS,
    FULL_OID_RE,
    LockHeld,
    READ_ONLY_TOOLS,
    SCHEMA_VERSION,
    TERMINAL_STATES,
    WRITABLE_TOOLS,
    atomic,
    board_pause_active,
    canonical_root,
    git,
    git_common_dir,
    list_rounds,
    load_config,
    lock_fd,
    lock_is_held,
    primary_root,
    read_json,
    record_codex_io,
    require_allowed_model,
    require_task_arg,
    runtime_version,
    state_root,
    task_dir_for,
)
from pi_evidence import (
    _head_probe,
    _number,
    _pid_value,
    _redact_receipt_metadata,
    _scan_checks,
    acceptance_line,
    normalize_candidate,
)
from pi_phase import (
    MAX_PROGRESS_ITEMS,
    MAX_PROGRESS_REFS,
    PROGRESS_ACTIVITIES,
    PROGRESS_FILE,
    PROGRESS_LOCK,
    _phase_acceptance_on_board,
    _progress_text,
    _resolve_baseline,
    build_phase_snapshot,
    build_readiness,
    contract_hash,
    contract_view,
    install_phase_contract,
    load_contract,
    phase_budget,
    phase_path,
    read_phase_record,
    read_phase_state,
    read_progress,
    settle_quota_path,
    validate_contract,
    validate_resource_limits,
)
from pi_summary import bounded, compact, read_meta, summarize
from pi_supervisor import run_worker, spawn_worker
from pi_takeover import REVIEW_LIMIT


HELPER_FILES = ("pi_task.py", "pi_core.py", "pi_evidence.py", "pi_phase.py", "pi_brief.py",
                "pi_supervisor.py", "pi_summary.py", "pi_check.py", "pi_copy.py", "pi_size.py",
                "pi_board.py", "pi_store.py", "pi_events.py", "pi_queue.py", "pi_takeover.py",
                "pi_worker.ts", "VERSION")


def active_tasks(state: Path) -> list:
    tasks_dir = state / "tasks"
    result = []
    if tasks_dir.is_dir():
        for entry in sorted(tasks_dir.iterdir()):
            if entry.is_dir() and lock_is_held(entry / ".task.lock"):
                result.append(entry.name)
    return result


def claim_worktree(common: Path, worktree: Path, task: str) -> Path:
    claims = state_root(common) / "worktrees"
    claims.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(worktree).encode("utf-8")).hexdigest()
    path = claims / f"{key}.claim.json"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        owner = read_json(path, {}) or {}
        raise ValueError(f"worktree {worktree} is already claimed by task {owner.get('task', 'unknown')}; "
                         "claims last for the task lifetime and are not auto-released, so use a "
                         "separate checkout for every new writer task") from None
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"schemaVersion": 1, "task": task, "worktree": str(worktree),
                   "createdAt": time.time()}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


def release_claim(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def validate_worktree(common: Path, root: Path, worktree_arg: str):
    if not worktree_arg or not str(worktree_arg).strip():
        raise ValueError("worktree is required")
    worktree = Path(worktree_arg).expanduser()
    if not worktree.exists() or not worktree.is_dir():
        raise ValueError(f"worktree does not exist: {worktree}; create a separate checkout first")
    real = worktree.resolve()
    try:
        top = Path(git(real, "rev-parse", "--show-toplevel")).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ValueError(f"worktree is not a git checkout: {real}") from exc
    if top != real:
        raise ValueError(f"worktree argument must be the checkout root {top}, got {real}")
    wt_common = git_common_dir(real)
    if wt_common != common:
        raise ValueError(f"worktree {real} belongs to git common dir {wt_common}, not {common}; "
                         "foreign worktrees are rejected")
    try:
        primary = primary_root(root)
    except ValueError:
        primary = root
    if real == primary:
        raise ValueError("worktree must be a separate checkout, not the repository's Git primary checkout")
    if real == root:
        raise ValueError("worktree must be a separate checkout, not the configured project checkout passed as --repo")
    try:
        head = git(real, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        head = None
    return real, head


def cmd_progress(args) -> dict:
    root = canonical_root(Path(args.repo))
    common = git_common_dir(root)
    task = require_task_arg(args.task)
    task_dir = task_dir_for(common, task)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task!r}; no evidence at {task_dir}")
    rounds = list_rounds(task_dir)
    if not rounds:
        raise ValueError(f"task {task!r} has no rounds yet")
    if args.round is None:
        number = rounds[-1][0]
    else:
        number = int(args.round)
        if number not in [value for value, _ in rounds]:
            raise ValueError(f"round {number} does not exist for task {task!r}")
    round_dir = task_dir / "rounds" / str(number)
    state = read_json(round_dir / "round.state.json", {}) or {}
    if args.show:
        if args.activity is not None:
            raise ValueError("--show is read-only and cannot be combined with --activity")
        progress = read_progress(round_dir)
        return {"ok": True, "task": task, "round": number, "progress": progress,
                "note": "self-reported progress only; never acceptance or verified evidence"}
    activity = args.activity
    if activity not in PROGRESS_ACTIVITIES:
        raise ValueError(f"progress activity must be one of {list(PROGRESS_ACTIVITIES)}")
    step = _progress_text(args.step, "step")
    next_step = _progress_text(args.next, "next")
    blocker = _progress_text(args.blocker, "blocker")
    if args.blocker is not None and not blocker and activity == "blocked":
        raise ValueError("progress activity 'blocked' requires a blocker description")
    phase, problem = read_phase_record(task_dir)
    phase_id = contract_sha = None
    known_criteria = set()
    if isinstance(phase, dict):
        contract = phase.get("contract") or {}
        phase_id = contract.get("phaseId")
        contract_sha = phase.get("contractSha256")
        known_criteria = {item.get("id") for item in contract.get("acceptanceItems") or []}
    completed = args.completed_criteria or []
    if len(completed) > MAX_PROGRESS_ITEMS:
        raise ValueError(f"progress completed criteria exceed {MAX_PROGRESS_ITEMS} entries")
    for item in completed:
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", item):
            raise ValueError(f"progress completed criterion {item!r} is not a safe acceptance id")
        if known_criteria and item not in known_criteria:
            raise ValueError(f"progress completed criterion {item!r} is not in phase "
                             f"{phase_id!r}; scripts never invent acceptance ids")
    refs = args.evidence_ref or []
    if len(refs) > MAX_PROGRESS_REFS:
        raise ValueError(f"progress evidence refs exceed {MAX_PROGRESS_REFS} entries")
    clean_refs = []
    for ref in refs:
        text = _progress_text(ref, "evidence ref", required=True)
        if len(text) > 300 or ".." in text.replace("\\", "/").split("/"):
            raise ValueError(f"progress evidence ref {text!r} is not a safe path")
        clean_refs.append(text)
    fd = lock_fd(round_dir / PROGRESS_LOCK, blocking=True, timeout=5)
    try:
        previous = read_progress(round_dir) or {}
        criteria = list(dict.fromkeys(list(previous.get("completedCriteria") or []) + completed))
        evidence = list(dict.fromkeys(list(previous.get("evidenceRefs") or []) + clean_refs))
        record = {
            "schemaVersion": 1, "task": task, "phaseId": phase_id,
            "contractSha256": contract_sha, "round": number,
            "briefSha256": state.get("briefSha256"), "step": step,
            "activity": activity, "completedCriteria": criteria[-MAX_PROGRESS_ITEMS:],
            "next": next_step, "blocker": blocker,
            "evidenceRefs": evidence[-MAX_PROGRESS_REFS:],
            "reportedBy": "pi", "verified": False, "updatedAt": time.time(),
            "updateCount": int(previous.get("updateCount") or 0) + 1,
        }
        atomic(round_dir / PROGRESS_FILE, record)
    finally:
        os.close(fd)
    return {"ok": True, "task": task, "round": number, "activity": activity,
            "progress": {key: record[key] for key in (
                "phaseId", "contractSha256", "step", "activity", "completedCriteria",
                "next", "blocker", "evidenceRefs", "updatedAt", "updateCount")},
            "note": "self-reported progress only; it is never acceptance, never verified check "
                    "evidence and never a queue notification"}


def _board_phase_info(task: dict) -> dict | None:
    common = task.get("commonDir")
    task_id = task.get("task")
    if not isinstance(common, str):
        return None
    board = read_json(Path(common) / "codex-pi" / "board.json", None)
    if not isinstance(board, dict):
        return None
    card = (board.get("cards") or {}).get(task_id)
    if not isinstance(card, dict):
        return None
    info = card.get("phase")
    if not isinstance(info, dict):
        return None
    return {"phaseId": info.get("phaseId"), "contractHash": info.get("contractHash"),
            "status": info.get("status"), "candidate": info.get("candidate"),
            "acceptedHead": info.get("acceptedHead"), "acceptedAt": info.get("acceptedAt"),
            "reviewEventId": info.get("reviewEventId")}


def _select_round(task_dir: Path, round_arg):
    rounds = list_rounds(task_dir)
    if not rounds:
        raise ValueError(f"task has no rounds yet")
    if round_arg is None:
        return rounds[-1][0]
    number = int(round_arg)
    if number not in [value for value, _ in rounds]:
        raise ValueError(f"round {number} does not exist")
    return number


def cmd_readiness(args) -> dict:
    root = canonical_root(Path(args.repo))
    common = git_common_dir(root)
    task = require_task_arg(args.task)
    task_dir = task_dir_for(common, task)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task!r}; no evidence at {task_dir}")
    frozen = read_json(task_dir / "task.json", None)
    if not isinstance(frozen, dict):
        raise ValueError(f"task {task!r} has no readable task.json")
    number = _select_round(task_dir, args.round)
    result = build_readiness(task_dir, frozen, number)
    if args.phase is not None and result.get("phaseId") != args.phase:
        raise ValueError(f"phase identity mismatch: contract is {result.get('phaseId')!r}, "
                         f"requested {args.phase!r}")
    if args.contract_hash is not None and result.get("contractSha256") != args.contract_hash:
        raise ValueError("contract hash mismatch: the installed phase contract is a different revision")
    result["board"] = _board_phase_info(frozen)
    return result


def cmd_phase_status(args) -> dict:
    root = canonical_root(Path(args.repo))
    common = git_common_dir(root)
    task = require_task_arg(args.task)
    task_dir = task_dir_for(common, task)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task!r}; no evidence at {task_dir}")
    frozen = read_json(task_dir / "task.json", None)
    if not isinstance(frozen, dict):
        raise ValueError(f"task {task!r} has no readable task.json")
    number = _select_round(task_dir, args.round)
    round_dir = task_dir / "rounds" / str(number)
    record, problem = read_phase_record(task_dir)
    result = {
        "ok": True, "task": task, "round": number, "phaseInstalled": isinstance(record, dict),
        "phaseProblem": problem, "contract": None, "contractSha256": None,
        "baselineCommit": None, "budget": phase_budget(task_dir, record if isinstance(record, dict) else None),
        "phaseState": read_phase_state(task_dir), "settleQuotaUsed": None,
        "progress": read_progress(round_dir),
        "readiness": None, "board": _board_phase_info(frozen),
        "acceptance": "not_verified",
        "note": "read-only phase projection; readiness ready still only means deliverable to main "
                "review, never acceptance",
    }
    if isinstance(record, dict):
        contract = record.get("contract") or {}
        result["contract"] = contract_view(contract)
        result["contractSha256"] = record.get("contractSha256")
        result["baselineCommit"] = record.get("baselineCommit")
        result["settleQuotaUsed"] = settle_quota_path(task_dir, contract.get("phaseId")).exists()
        state = read_json(round_dir / "round.state.json", {}) or {}
        if state.get("state") in TERMINAL_STATES:
            result["readiness"] = build_readiness(task_dir, frozen, number)
    else:
        result["briefOnly"] = True
        result["note"] += "; no phase contract is installed, so the brief-only review path applies"
    return result


def snapshot_helpers(source: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in HELPER_FILES:
        source_file = source / name
        if not source_file.is_file():
            raise ValueError(f"runtime helper missing: {source_file}")
        data = source_file.read_bytes()
        (destination / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


MIN_PI_VERSION = (1, 0, 0)


def pi_version() -> str:
    """Installed Pi version; refuses a missing, unreadable or too old Pi (needs >= 1.0.0)."""
    pi_bin = os.environ.get("PI_BIN") or "pi"
    need = ".".join(str(part) for part in MIN_PI_VERSION)
    try:
        proc = subprocess.run([pi_bin, "--version"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot read the Pi version ({exc}); Pi >= {need} is required") from None
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", (proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0 or not found:
        raise ValueError(f"cannot read the Pi version (`{pi_bin} --version` exited "
                         f"{proc.returncode}); Pi >= {need} is required")
    parts = tuple(int(part) for part in found.groups())
    if parts < MIN_PI_VERSION:
        raise ValueError(f"Pi {'.'.join(found.groups())} is too old; Pi >= {need} is required")
    return ".".join(found.groups())


# ---------------------------------------------------------------------------
# start / continue
# ---------------------------------------------------------------------------

def project_context(repo_arg: str):
    root = canonical_root(Path(repo_arg))
    config = load_config(root)
    common = git_common_dir(root)
    return root, config, common


def record_worker_failure(task_dir: Path, round_number: int, error: str) -> None:
    path = task_dir / "rounds" / str(round_number) / "round.state.json"
    state = read_json(path, {}) or {}
    state.update(state="unknown", exitCode=None, endedAt=time.time(), error=error)
    atomic(path, state)


def cmd_start(args) -> dict:
    root, config, common = project_context(args.repo)
    require_allowed_model(config["model"], "config")
    task = require_task_arg(args.task)
    worktree, start_head = validate_worktree(common, root, args.worktree)
    prompt = read_prompt(args)
    installed_pi = pi_version()
    contract_raw = None
    if args.contract_file:
        contract_raw = load_contract(args.contract_file)
        # Validate fully before any task directory or evidence is created.
        if isinstance(contract_raw, dict):
            contract_raw = validate_contract(contract_raw, root)
            validate_resource_limits(contract_raw, worktree)
            _resolve_baseline(worktree, contract_raw["baseline"])
    state = state_root(common)
    tasks_dir = state / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_dir = tasks_dir / task

    admission = lock_fd(state / ".admission.lock", blocking=True, timeout=30)
    claim = None
    lock_value = None
    created = False
    try:
        if task_dir.exists():
            raise ValueError(f"task {task!r} already exists at {task_dir}; existing evidence is left "
                             "untouched, use pi_continue for it")
        os.mkdir(task_dir)
        created = True
        claim = claim_worktree(common, worktree, task)
        busy = active_tasks(state)
        if len(busy) >= config["maxWorkers"]:
            raise ValueError(f"project maxWorkers={config['maxWorkers']} reached; active tasks: {busy}")
        lock_value = lock_fd(task_dir / ".task.lock")
        (task_dir / "session").mkdir(parents=True, exist_ok=True)
        hashes = snapshot_helpers(Path(__file__).resolve().parent, task_dir / "tools")
        created_at = time.time()
        task_json = {
            "schemaVersion": SCHEMA_VERSION, "task": task, "taskDir": str(task_dir),
            "repo": str(root), "commonDir": str(common), "worktree": str(worktree),
            "configPath": config["path"], "readOnly": bool(args.read_only),
            "model": config["model"], "thinking": config["thinking"],
            "constraints": config["constraints"], "checks": config["checks"],
            "maxWorkers": config["maxWorkers"], "timeoutSeconds": config["timeoutSeconds"],
            "createdAt": created_at, "startHead": start_head,
            "runtimeVersion": runtime_version(), "helperHashes": hashes,
            "piVersion": installed_pi,
            "sessionId": task, "sessionDir": str(task_dir / "session"),
        }
        atomic(task_dir / "task.json", task_json)
        timeout_seconds = float(config["timeoutSeconds"])
        if contract_raw is not None:
            install_phase_contract(task_dir, contract_raw, root, worktree)
            timeout_seconds = min(timeout_seconds, float(contract_raw["budgetSeconds"]))
        ensure_round_inputs(task_dir, 1, prompt, task_json, None, installed_pi)
        spawn_worker(task_dir, 1, lock_value, timeout_seconds)
    except Exception as exc:
        if lock_value is not None:
            os.close(lock_value)
            lock_value = None
        if created:
            # Only a directory created by this invocation may be cleaned or
            # marked; a duplicate start must never mutate existing evidence.
            if claim is not None:
                release_claim(claim)
            if not (task_dir / "task.json").exists():
                shutil.rmtree(task_dir, ignore_errors=True)
            else:
                record_worker_failure(task_dir, 1, f"worker failed to start: {exc}")
        raise
    finally:
        if lock_value is not None:
            os.close(lock_value)
        os.close(admission)
    phase_record, _problem = read_phase_record(task_dir)
    return {"ok": True, "task": task, "round": 1, "state": "starting",
            "repo": str(root), "worktree": str(worktree),
            "readOnly": bool(args.read_only), "model": config["model"], "thinking": config["thinking"],
            "sessionId": task, "sessionDir": str(task_dir / "session"),
            "phase": None if phase_record is None else {
                "phaseId": (phase_record.get("contract") or {}).get("phaseId"),
                "contractSha256": phase_record.get("contractSha256"),
                "contractRef": str(phase_path(task_dir))},
            "evidence": evidence_paths(task_dir, 1),
            "note": "worker returned immediately; exit 0 will mean completed execution, never acceptance PASS"}


def cmd_continue(args) -> dict:
    root = canonical_root(Path(args.repo))
    common = git_common_dir(root)
    task = require_task_arg(args.task)
    task_dir = task_dir_for(common, task)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task!r}; expected evidence at {task_dir}")
    frozen = read_json(task_dir / "task.json", None)
    if not isinstance(frozen, dict):
        raise ValueError(f"task {task!r} has no readable task.json; inspect {task_dir} before continuing")
    frozen_repo_raw = frozen.get("repo")
    if not isinstance(frozen_repo_raw, str) or not frozen_repo_raw:
        raise ValueError(f"task {task!r} has no frozen repository identity; refusing to continue")
    if Path(frozen_repo_raw).resolve() != root:
        raise ValueError(f"task {task!r} belongs to checkout {frozen_repo_raw}, not {root}; "
                         "pass --repo inside the frozen checkout (result/wait/cancel can still use "
                         "common-dir evidence)")
    require_allowed_model(frozen.get("model"), "frozen task")
    config = load_config(root)
    worktree, _ = validate_worktree(common, root, frozen["worktree"])
    prompt = read_prompt(args)
    installed_pi = pi_version()
    contract_raw = None
    if args.contract_file:
        contract_raw = load_contract(args.contract_file)
        if isinstance(contract_raw, dict):
            contract_raw = validate_contract(contract_raw, root)
            validate_resource_limits(contract_raw, worktree)
    state = state_root(common)

    admission = lock_fd(state / ".admission.lock", blocking=True, timeout=30)
    lock_value = None
    round_dir = None
    number = 0
    try:
        try:
            lock_value = lock_fd(task_dir / ".task.lock")
        except LockHeld:
            raise ValueError("task lock is still held by an active worker or an orphaned Pi; "
                             "wait or inspect before continuing") from None
        if lock_is_held(task_dir / ".supervisor.lock"):
            lease_deadline = time.monotonic() + 2
            while time.monotonic() < lease_deadline and lock_is_held(task_dir / ".supervisor.lock"):
                time.sleep(0.05)
        if lock_is_held(task_dir / ".supervisor.lock"):
            raise ValueError("supervisor lease is still held; task is not terminal-known")
        paused, pause_reason = board_pause_active(frozen)
        if paused and pause_reason and pause_reason.startswith("Codex takeover required"):
            raise ValueError(pause_reason)
        if paused:
            raise ValueError(f"task handoff is paused ({pause_reason}); an explicit pi_board resume "
                             "is required before continuing")
        rounds = list_rounds(task_dir)
        if not rounds:
            raise ValueError(f"task {task!r} has no completed round; do not continue an unknown run")
        for value, candidate in rounds:
            round_state = read_json(candidate / "round.state.json", None)
            meta = candidate / "round.meta"
            if not isinstance(round_state, dict):
                raise ValueError(f"round {value} has no state evidence; stale artifacts, inspect {candidate}")
            if round_state.get("state") in ACTIVE_STATES or round_state.get("state") not in TERMINAL_STATES:
                raise ValueError(f"round {value} is not terminal-known (state={round_state.get('state')!r}); "
                                 "never replay an unknown run")
            if not meta.is_file() or "exit" not in read_meta(meta):
                raise ValueError(f"round {value} has no exit evidence; stale artifacts, inspect {candidate}")
            if not (candidate / "round.jsonl").is_file():
                raise ValueError(f"round {value} raw log is missing; stale artifacts, inspect {candidate}")
        latest_number, latest_dir = rounds[-1]
        latest_state = read_json(latest_dir / "round.state.json", {})
        busy = [name for name in active_tasks(state) if name != task]
        if len(busy) >= config["maxWorkers"]:
            raise ValueError(f"project maxWorkers={config['maxWorkers']} reached; active tasks: {busy}")
        claims = state_root(common) / "worktrees"
        key = hashlib.sha256(str(Path(frozen["worktree"])).encode("utf-8")).hexdigest()
        claim_file = claims / f"{key}.claim.json"
        owner = read_json(claim_file, {}) or {}
        if owner.get("task") != task:
            raise ValueError(f"worktree claim missing or foreign for {frozen['worktree']}; inspect evidence")
        number = latest_number + 1
        latest_head = latest_state.get("endHead") or latest_state.get("head")
        phase_record, phase_problem = read_phase_record(task_dir)
        if contract_raw is not None:
            new_hash = contract_hash(contract_raw)
            if not isinstance(phase_record, dict):
                phase_record = install_phase_contract(task_dir, contract_raw, root, worktree)
            else:
                old_contract = phase_record.get("contract") or {}
                if old_contract.get("phaseId") != contract_raw.get("phaseId"):
                    _info, gate_problem = _phase_acceptance_on_board(frozen, phase_record, latest_head)
                    if gate_problem:
                        raise ValueError(f"cannot dispatch the next phase: {gate_problem}; the main "
                                         "session must accept the previous phase and bind its contract "
                                         "and candidate on the board first")
                    phase_record = install_phase_contract(task_dir, contract_raw, root, worktree)
                elif phase_record.get("contractSha256") != new_hash:
                    # Explicit design revision for the same phase: allowed, but the
                    # original budget anchor stays.
                    phase_record = install_phase_contract(task_dir, contract_raw, root, worktree,
                                                          prior=phase_record)
        timeout_seconds = float(frozen["timeoutSeconds"])
        if isinstance(phase_record, dict):
            budget = phase_budget(task_dir, phase_record)
            remaining = budget.get("remainingSeconds")
            if remaining is None:
                raise ValueError("phase budget is unknown; refusing to start another phase round")
            if remaining <= 0:
                raise ValueError("phase budget is exhausted; the main session must accept, escalate "
                                 "or provide an explicit new phase contract revision instead of "
                                 "resetting the budget")
            timeout_seconds = min(timeout_seconds, max(1.0, remaining))
        prior = {"round": latest_number, "state": latest_state.get("state"),
                 "exitCode": latest_state.get("exitCode"), "endHead": latest_head}
        round_dir = ensure_round_inputs(task_dir, number, prompt, frozen, prior, installed_pi)
        spawn_worker(task_dir, number, lock_value, timeout_seconds)
    except Exception as exc:
        if lock_value is not None:
            os.close(lock_value)
            lock_value = None
        if round_dir is not None and round_dir.exists():
            record_worker_failure(task_dir, number, f"worker failed to start: {exc}")
        raise
    finally:
        if lock_value is not None:
            os.close(lock_value)
        os.close(admission)
    phase_record, _problem = read_phase_record(task_dir)
    return {"ok": True, "task": task, "round": number, "state": "starting", "repo": str(root),
            "worktree": str(worktree), "readOnly": bool(frozen.get("readOnly")),
            "model": frozen.get("model"), "thinking": frozen.get("thinking"),
            "sessionId": frozen.get("sessionId"), "sessionDir": frozen.get("sessionDir"),
            "phase": None if not isinstance(phase_record, dict) else {
                "phaseId": (phase_record.get("contract") or {}).get("phaseId"),
                "contractSha256": phase_record.get("contractSha256"),
                "contractRef": str(phase_path(task_dir))},
            "evidence": evidence_paths(task_dir, number),
            "note": "same pinned session and worktree; exit 0 is execution only, never acceptance PASS"}


def read_prompt(args) -> str:
    if getattr(args, "prompt_file", None):
        path = Path(args.prompt_file)
        if not path.is_file():
            raise ValueError(f"prompt file does not exist: {path}")
        return path.read_text(encoding="utf-8")
    if getattr(args, "prompt", None) is not None:
        return args.prompt
    raise ValueError("provide --prompt-file (preferred) or --prompt")


# ---------------------------------------------------------------------------
# result / wait / cancel
# ---------------------------------------------------------------------------

def evidence_paths(task_dir: Path, round_number: int | None) -> dict:
    result = {"taskDir": str(task_dir), "toolsDir": str(task_dir / "tools"),
              "supervisorLog": str(task_dir / "supervisor.log")}
    if round_number is None:
        return result
    round_dir = task_dir / "rounds" / str(round_number)
    result.update({"roundDir": str(round_dir), "brief": str(round_dir / "brief.md"),
                   "jsonl": str(round_dir / "round.jsonl"), "stderr": str(round_dir / "round.err"),
                   "meta": str(round_dir / "round.meta"), "state": str(round_dir / "round.state.json"),
                   "summaryJson": str(round_dir / "round.summary.json"),
                   "summaryText": str(round_dir / "round.summary.txt"),
                   "checksDir": str(round_dir / "round.checks")})
    return result


def effective_state(state: dict | None, task_held: bool, supervisor_alive: bool,
                    is_latest: bool) -> str:
    raw = (state or {}).get("state")
    if raw == "starting" and task_held and is_latest:
        # The parent or a just-spawned worker holds the task lock but the
        # supervisor lease has not been written yet; this is still active.
        return "starting"
    if raw in ACTIVE_STATES:
        return raw if (supervisor_alive and is_latest) else "unknown"
    if raw in TERMINAL_STATES:
        return raw
    return "unknown"


def round_compact(task_dir: Path, number: int, round_dir: Path, task_held: bool,
                  supervisor_alive: bool, latest: bool) -> dict:
    state = read_json(round_dir / "round.state.json", {}) or {}
    got = effective_state(state, task_held, supervisor_alive, latest)
    started = state.get("startedAt")
    ended = state.get("endedAt")
    return {"round": number, "state": got, "exitCode": state.get("exitCode"),
            "timedOut": bool(state.get("timedOut")), "cancelled": bool(state.get("cancelled")),
            "startedAt": started, "endedAt": ended,
            "durationMs": int((ended - started) * 1000) if isinstance(started, (int, float))
            and isinstance(ended, (int, float)) else None,
            "startHead": state.get("startHead"),
            "endHead": state.get("endHead") or state.get("head"),
            "briefSha256": state.get("briefSha256"),
            "summaryError": state.get("summaryError"),
            "evidence": evidence_paths(task_dir, number)}


def load_round_summary(task_dir: Path, task: dict, round_dir: Path, number: int):
    summary_path = round_dir / "round.summary.json"
    data = read_json(summary_path, None)
    if isinstance(data, dict) and "source" in data:
        return data
    log = round_dir / "round.jsonl"
    if not log.is_file():
        return None
    try:
        data = summarize(log, Path(task["worktree"]), round_dir, task.get("model"),
                         checks_dir=round_dir / "round.checks")
        atomic(summary_path, data)
        (round_dir / "round.summary.txt").write_text(compact(data), encoding="utf-8")
        return data
    except Exception as exc:
        return {"schema_version": 1, "acceptance": "not_verified",
                "error": f"summary unavailable: {exc}"}


def _invalid_phase_info(task_dir: Path, problem: str, candidate: dict,
                        round_number: int) -> dict:
    """Fail-closed phase identity for an unreadable or tampered contract record.

    The invalid record is never projected as absent: the board keeps a phase
    binding with unknown readiness so an old phase event cannot fall back to the
    brief-only accept path while the declared contract cannot be trusted.
    """
    raw = read_json(phase_path(task_dir), None)
    raw = raw if isinstance(raw, dict) else {}
    contract = raw.get("contract") if isinstance(raw.get("contract"), dict) else {}
    phase_id = contract.get("phaseId")
    if not isinstance(phase_id, str) \
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", phase_id):
        phase_id = "INVALID-CONTRACT"
    digest = raw.get("contractSha256")
    if not isinstance(digest, str) or not FULL_OID_RE.fullmatch(digest):
        digest = "0" * 64
    reason = f"phase contract record is invalid ({problem}); readiness is unknown"
    readiness = {"status": "unknown", "reason": reason,
                 "coverage": {"required": 0, "covered": 0, "failed": 0, "missing": 0,
                              "unknown": 0, "skipped": 0},
                 "gaps": [], "scope": "unknown", "writerFree": False,
                 "readyForReview": False, "execution": {"status": "unknown"},
                 "resource": None, "generatedAt": time.time()}
    return {
        "phaseId": phase_id, "contractSha256": digest,
        "contractRef": str(phase_path(task_dir)),
        "baselineCommit": raw.get("baselineCommit"),
        "state": "invalid", "candidate": candidate.get("head"),
        "budget": phase_budget(task_dir, None),
        "lastDecision": None,
        "evidence": {"schemaVersion": 1, "round": round_number, "phaseId": phase_id,
                     "contractSha256": digest, "candidate": candidate,
                     "execution": {"state": None, "exitCode": None, "cancelled": None,
                                   "timedOut": None},
                     "items": [], "coverage": readiness["coverage"], "gaps": [],
                     "scope": {"status": "unknown", "outOfScope": [], "changedFiles": []},
                     "readiness": readiness, "resource": None,
                     "evidenceLevels": {"gptAcceptance": False},
                     "notes": [reason], "acceptance": "not_verified"},
        "readiness": readiness,
        "acceptance": "not_verified",
    }


def build_status(repo_arg: str, task_arg: str, round_arg=None) -> dict:
    """Bounded read-only state snapshot. Never starts Pi, builds a summary or scans a transcript."""
    task = require_task_arg(task_arg)
    root = canonical_root(Path(repo_arg))
    common = git_common_dir(root)
    task_dir = task_dir_for(common, task)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task!r} for repository {root}; no evidence at {task_dir}")
    frozen = read_json(task_dir / "task.json", None)
    if not isinstance(frozen, dict):
        raise ValueError(f"task {task!r} has no readable task.json under {task_dir}")
    rounds = list_rounds(task_dir)
    if not rounds:
        raise ValueError(f"task {task!r} has no rounds yet; start it before reading status")
    latest_number = rounds[-1][0]
    if round_arg is None:
        selected_number = latest_number
    else:
        selected_number = int(round_arg)
        if selected_number not in [number for number, _ in rounds]:
            raise ValueError(f"round {selected_number} does not exist for task {task!r}")
    selected_dir = task_dir / "rounds" / str(selected_number)
    task_held = lock_is_held(task_dir / ".task.lock")
    supervisor_alive = lock_is_held(task_dir / ".supervisor.lock")
    state = read_json(selected_dir / "round.state.json", None)
    if not isinstance(state, dict):
        state = {}
    raw_state = state.get("state")
    effective = effective_state(state, task_held, supervisor_alive, selected_number == latest_number)
    current_head, head_problem = None, None
    if raw_state in ACTIVE_STATES:
        frozen_worktree = frozen.get("worktree")
        if isinstance(frozen_worktree, str) and frozen_worktree.strip():
            current_head, head_problem = _head_probe(Path(frozen_worktree))
        else:
            head_problem = "no frozen worktree"
    started = _number(state.get("startedAt"))
    ended = _number(state.get("endedAt"))
    now = time.time()
    elapsed_ms = int(((ended if ended is not None else now) - started) * 1000) if started is not None else None
    timeout = _number(frozen.get("timeoutSeconds"))
    deadline_at = started + timeout if started is not None and timeout is not None else None
    checks = _scan_checks(selected_dir / "round.checks")
    execution_activity = {}
    for label, name in (("roundJsonl", "round.jsonl"), ("roundErr", "round.err")):
        path = selected_dir / name
        try:
            info = path.stat()
            execution_activity[label] = {"path": str(path), "bytes": info.st_size,
                                         "mtime": info.st_mtime}
        except OSError:
            execution_activity[label] = {"path": str(path), "bytes": None, "mtime": None}
    execution_activity["note"] = ("raw execution evidence size/mtime only; a quiet or growing transcript "
                                  "is activity evidence, never useful progress and never acceptance")
    processes = {"taskLockHeld": task_held, "supervisorLeaseHeld": supervisor_alive}
    for key in ("supervisorPid", "piPid"):
        processes[key] = _pid_value(state.get(key))
    # Fixed explanatory notes (exit 0 is not acceptance, activity is not progress)
    # live once in the Skill and in ``result``; status only carries conditional facts.
    notes = []
    if raw_state in ACTIVE_STATES and not supervisor_alive:
        notes.append("the recorded state is active but no supervisor lease is held; ownership is "
                    "unknown, not progress")
    if raw_state in ACTIVE_STATES:
        if head_problem is not None:
            notes.append(f"the active round's current HEAD could not be read ({head_problem}); "
                         "the active candidate identity is unknown")
        elif isinstance(current_head, str) and current_head != state.get("startHead"):
            notes.append("the worktree HEAD advanced during the active round; currentHead is the "
                         "verified current candidate identity")
    candidate_block = normalize_candidate({"state": effective,
                                           "endHead": state.get("endHead") or state.get("head"),
                                           "currentHead": current_head})
    if raw_state in TERMINAL_STATES and task_held and not supervisor_alive:
        notes.append("a Pi descendant may still hold the task lock after a terminal record; inspect "
                     "before reuse")
    if checks["running"] is not None:
        notes.append("the running marker and wrapper deadline come from pi_check; an inner command "
                     "deadline is never inferred from shell syntax")
    if checks["unreceiptedLog"] is not None:
        notes.append("the latest unreceipted check log is an explicitly uncertain unreceipted-log candidate; "
                     "it is not proof of an active, hung or failed check")
    guard = checks.get("resourceGuard") or {}
    if guard.get("breached"):
        notes.append("a local no-follow resource guard reported a byte-budget breach; that is an "
                     "explicit resource observation, never a pass or acceptance")
    if guard.get("unknown"):
        notes.append("a resource measurement was incomplete or unreadable; budget status is unknown, "
                     "not verified under budget")
    if checks["partial"]:
        notes.append("the checks directory was only partially inspected (bounded subset of entries); "
                     "totals, latest receipts and candidates are not global and may be incomplete")
    elif checks["receipts"]["truncated"]:
        notes.append("receipt parsing was bounded to the newest entries; failed counts and latest "
                     "receipts may be incomplete")

    # Phase contract projection. Read-only: readiness is recomputed in memory for
    # terminal rounds and never written by status.
    phase_record, phase_problem = read_phase_record(task_dir)
    phase_info = None
    if isinstance(phase_record, dict):
        contract = phase_record.get("contract") or {}
        phase_state = read_phase_state(task_dir)
        budget = phase_budget(task_dir, phase_record)
        readiness = None
        snapshot = None
        try:
            snapshot = build_phase_snapshot(task_dir, frozen, selected_number, state=state,
                                            checks=checks, candidate=candidate_block)
            readiness = snapshot.get("readiness")
        except Exception as exc:  # noqa: BLE001 - status must not fail on one snapshot error
            readiness = {"status": "unknown", "reason": f"{type(exc).__name__}: {exc}",
                         "coverage": {"required": 0, "covered": 0, "failed": 0,
                                      "missing": 0, "unknown": 0, "skipped": 0},
                         "gaps": [], "scope": "unknown", "writerFree": False,
                         "readyForReview": False, "generatedAt": time.time()}
            snapshot = {"schemaVersion": 1, "status": "unknown", "candidate": candidate_block,
                        "items": [], "gaps": [], "readiness": readiness,
                        "error": f"{type(exc).__name__}: {exc}"}
        phase_info = {
            "phaseId": contract.get("phaseId"),
            "contractSha256": phase_record.get("contractSha256"),
            "contractRef": str(phase_path(task_dir)),
            "baselineCommit": phase_record.get("baselineCommit"),
            "contract": contract_view(contract),
            "state": phase_state.get("state") or "executing",
            "candidate": candidate_block.get("head"),
            "budget": budget,
            "lastDecision": phase_state.get("lastDecision"),
            "evidence": snapshot,
            "readiness": readiness,
            "acceptance": "not_verified",
        }
    elif phase_problem not in (None, "absent"):
        phase_info = _invalid_phase_info(task_dir, phase_problem, candidate_block,
                                         selected_number)
        notes.append(f"phase contract state is {phase_problem}; phase readiness is unknown")
    progress = read_progress(selected_dir)
    return {
        "schemaVersion": 1, "task": task, "repo": frozen.get("repo") or str(root),
        "worktree": frozen.get("worktree"), "readOnly": bool(frozen.get("readOnly")),
        "model": frozen.get("model"), "thinking": frozen.get("thinking"),
        "runtimeVersion": frozen.get("runtimeVersion"),
        "session": {"sessionId": frozen.get("sessionId"), "sessionDir": frozen.get("sessionDir")},
        "round": selected_number, "latestRound": latest_number,
        "state": effective, "recordedState": raw_state,
        "startedAt": started, "endedAt": ended, "elapsedMs": elapsed_ms, "deadlineAt": deadline_at,
        "exitCode": state.get("exitCode"), "timedOut": bool(state.get("timedOut")),
        "cancelled": bool(state.get("cancelled")),
        "startHead": state.get("startHead"), "endHead": state.get("endHead") or state.get("head"),
        "currentHead": current_head, "candidate": candidate_block,
        "briefSha256": state.get("briefSha256"),
        "ownership": {"activeWorker": task_held, "supervisorAlive": supervisor_alive},
        "processes": processes, "executionActivity": execution_activity,
        "checks": _redact_receipt_metadata(checks), "evidence": evidence_paths(task_dir, selected_number),
        "phase": phase_info, "phaseProblem": phase_problem, "progress": progress,
        "acceptance": "not_verified", "notes": notes,
    }


def build_result(repo_arg: str, task_arg: str, round_arg=None) -> dict:
    task = require_task_arg(task_arg)
    root = canonical_root(Path(repo_arg))
    common = git_common_dir(root)
    task_dir = task_dir_for(common, task)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task!r} for repository {root}; no evidence at {task_dir}")
    frozen = read_json(task_dir / "task.json", None)
    if not isinstance(frozen, dict):
        raise ValueError(f"task {task!r} has no readable task.json under {task_dir}")
    rounds = list_rounds(task_dir)
    if not rounds:
        raise ValueError(f"task {task!r} has no rounds yet; start it before reading a result")
    latest_number = rounds[-1][0]
    if round_arg is None:
        selected_number = latest_number
    else:
        selected_number = int(round_arg)
        if selected_number not in [number for number, _ in rounds]:
            raise ValueError(f"round {selected_number} does not exist for task {task!r}")
    selected_dir = task_dir / "rounds" / str(selected_number)
    task_held = lock_is_held(task_dir / ".task.lock")
    supervisor_alive = lock_is_held(task_dir / ".supervisor.lock")
    compacts = [round_compact(task_dir, number, rdir, task_held, supervisor_alive,
                              number == latest_number) for number, rdir in rounds]
    selected_state = next(item for item in compacts if item["round"] == selected_number)
    selected_raw = (read_json(selected_dir / "round.state.json", {}) or {}).get("state")
    summary_data = load_round_summary(task_dir, frozen, selected_dir, selected_number)
    summary = bounded(summary_data) if isinstance(summary_data, dict) and "source" in summary_data else None
    notes = ["exit 0 means the Pi process completed execution; it is never acceptance PASS"]
    if selected_state["state"] == "unknown":
        if selected_raw in ACTIVE_STATES and task_held:
            notes.append("the supervisor is gone but an owned Pi child still holds the task lock; "
                         "the outcome is unknown and no continuation may start")
        else:
            notes.append("the recorded state was active or missing without a live supervisor; "
                         "the outcome is unknown, not success")
    if selected_state["state"] in TERMINAL_STATES and task_held and not supervisor_alive:
        notes.append("a Pi descendant still holds the task lock after a terminal record; "
                     "inspect the recorded processes before reusing anything")
    if summary_data is not None and "error" in summary_data:
        notes.append(summary_data["error"])
    if summary is not None and not summary.get("usage_complete"):
        notes.append("model usage was not fully reported; missing values are unknown, not zero")
    if supervisor_alive:
        notes.append("the supervisor is alive and holds the task lock")
    checks = []
    if summary is not None:
        for receipt in ((summary.get("checks") or {}).get("receipts") or []):
            code = receipt.get("exit_code")
            status = "unknown" if code is None else ("failed" if code != 0 else "covered")
            checks.append(acceptance_line(receipt.get("id") or "?", status, code,
                                          receipt.get("test_counts")))
        if (summary.get("checks") or {}).get("total") == 0:
            checks = ["no check receipts recorded (missing evidence)"]
    evidence = evidence_paths(task_dir, selected_number)
    return {
        "schemaVersion": 1, "task": task, "round": selected_number, "latestRound": latest_number,
        "state": selected_state["state"], "exitCode": selected_state["exitCode"],
        "timedOut": selected_state["timedOut"], "cancelled": selected_state["cancelled"],
        "execution": "completed_execution" if selected_state["state"] == "completed"
        and selected_state["exitCode"] == 0 else selected_state["state"],
        "acceptance": "not_verified", "candidateHead": selected_state.get("endHead"),
        "model": frozen.get("model"), "modelCheck": (summary or {}).get("model_check"),
        "reportedModels": (summary or {}).get("models"),
        "activeWorker": task_held, "supervisorAlive": supervisor_alive,
        "usage": (summary or {}).get("usage"),
        "usageComplete": bool(summary and summary.get("usage_complete")),
        "costUsd": (summary or {}).get("reported_cost_usd"),
        "checks": checks,
        "final": ((summary or {}).get("final_excerpt") or "")[:COMPACT_FINAL_CHARS],
        "evidence": {"roundDir": evidence.get("roundDir"),
                     "summaryJson": evidence.get("summaryJson")},
        "notes": notes,
    }


COMPACT_FINAL_CHARS = 1200


def cmd_result(args) -> dict:
    return build_result(args.repo, args.task, args.round)


def cmd_status(args) -> dict:
    return build_status(args.repo, args.task, args.round)


def cmd_cancel(args) -> dict:
    task = require_task_arg(args.task)
    root = canonical_root(Path(args.repo))
    common = git_common_dir(root)
    task_dir = task_dir_for(common, task)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task!r}; no evidence at {task_dir}")
    rounds = list_rounds(task_dir)
    if not rounds:
        raise ValueError(f"task {task!r} has no rounds yet")
    latest_number = rounds[-1][0]
    latest_dir = task_dir / "rounds" / str(latest_number)
    active = lock_is_held(task_dir / ".task.lock")
    supervised = lock_is_held(task_dir / ".supervisor.lock")
    if supervised and not active:
        # An exiting supervisor may briefly outlive its task lock.
        exit_deadline = time.monotonic() + 1
        while time.monotonic() < exit_deadline and lock_is_held(task_dir / ".supervisor.lock"):
            time.sleep(0.05)
        supervised = lock_is_held(task_dir / ".supervisor.lock")
    if active and not supervised:
        raw_latest = (read_json(latest_dir / "round.state.json", {}) or {}).get("state")
        if raw_latest == "starting":
            # A worker may be between process spawn and lease acquisition.
            lease_deadline = time.monotonic() + 3
            while time.monotonic() < lease_deadline and not lock_is_held(task_dir / ".supervisor.lock"):
                time.sleep(0.05)
            supervised = lock_is_held(task_dir / ".supervisor.lock")
    if not active and not supervised:
        result = build_result(args.repo, task)
        result["cancel"] = {"request": "not_active", "round": latest_number,
                            "note": "no owned supervisor or worker holds the task lock; nothing was signaled. "
                                    "A vanished worker's outcome stays unknown. This is not acceptance."}
        return result
    if not supervised:
        result = build_result(args.repo, task)
        result["cancel"] = {"request": "orphaned", "round": latest_number,
                            "note": "the supervisor is gone; an owned Pi child may still hold the task lock. "
                                    "This runtime does not signal unverified PIDs and does not auto-clean orphans; "
                                    "inspect the recorded process evidence before removing anything. "
                                    "The outcome stays unknown and is not acceptance."}
        return result
    nonce = uuid.uuid4().hex
    atomic(task_dir / "cancel.json", {"schemaVersion": 1, "task": task, "round": latest_number,
                                      "requestedAt": time.time(), "nonce": nonce})
    deadline = time.monotonic() + 3
    observed = False
    while time.monotonic() < deadline:
        recorded = read_json(task_dir / "cancel.observed", {}) or {}
        if recorded.get("round") == latest_number:
            observed = True
            break
        if not lock_is_held(task_dir / ".task.lock"):
            observed = True
            break
        time.sleep(0.1)
    result = build_result(args.repo, task)
    result["cancel"] = {"request": "observed" if observed else "requested", "round": latest_number,
                        "nonce": nonce, "observed": observed,
                        "note": "the owned worker stops its Pi process group; empty exit means unknown, never success"}
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_project(args) -> dict:
    root = canonical_root(Path(args.repo))
    config = load_config(root)
    common = git_common_dir(root)
    state = state_root(common)
    busy = active_tasks(state)
    return {"ok": True, "schemaVersion": 1, "repo": str(root), "gitCommonDir": str(common),
            "configPath": config["path"],
            "config": {key: config[key] for key in CONFIG_KEYS if key in config},
            "capabilities": {
                "piExecutable": os.environ.get("PI_BIN") or "pi",
                "readOnlyTools": READ_ONLY_TOOLS.split(","),
                "writableTools": WRITABLE_TOOLS.split(","),
                "worktreeRule": "existing linked checkout of this repository, never the configured "
                                "checkout passed as --repo and never the Git primary checkout; one writer "
                                "per task and worktree; claims last for the task lifetime and are not "
                                "auto-released, so use a new linked checkout for each new task",
                "evidenceRoot": str(state / "tasks"),
                "configCheckout": str(root),
            },
            "limits": {"maxWorkers": config["maxWorkers"], "timeoutSeconds": config["timeoutSeconds"],
                       "model": config["model"], "thinking": config["thinking"],
                       "allowedModels": list(ALLOWED_MODELS),
                       "modelPolicy": "each new task pins one explicitly allowed project-configured model; "
                                      "existing task snapshots never change and unavailable models are "
                                      "rejected without substitution",
                       "reviewPolicy": {
                           "limit": REVIEW_LIMIT,
                           "note": "every task allows two complete deliveries; after the first "
                                   "reviewed quality failure the same Codex main session "
                                   "reassesses the complete outcome and the remaining plan "
                                   "before the second Pi delivery, and a second failure "
                                   "transfers implementation to that main session; contract "
                                   "revisions, phase renames, pauses and resumes never reset "
                                   "counted failures"},
                       "readOnlyIsNotASecuritySandbox": True, "codexCliInvocations": 0},
            "activeTasks": busy[:20], "activeTaskCount": len(busy),
            "note": "configuration references are instructions only; this runtime executes no configured commands"}


def add_repo_task(parser):
    parser.add_argument("--repo", required=True)
    parser.add_argument("--task", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi_task.py",
        description="Persistent Pi worker lifecycle for the Codex-Pi plugin. "
                    "Never invokes the Codex CLI. The Codex main session calls these "
                    "subcommands through its shell.")
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="resolve repo root, config, capabilities and limits")
    project.add_argument("--repo", required=True)
    project.set_defaults(func=cmd_project)

    start = sub.add_parser("start", help="create a fresh task, immutable round 1 and a detached worker")
    add_repo_task(start)
    start.add_argument("--worktree", required=True)
    start.add_argument("--prompt-file")
    start.add_argument("--prompt")
    start.add_argument("--read-only", action="store_true")
    start.add_argument("--contract-file", help="frozen phase contract JSON (optional; without it "
                                                  "the task runs from its brief alone)")
    start.set_defaults(func=cmd_start)

    cont = sub.add_parser("continue", help="continue a terminal-known task in its pinned session")
    add_repo_task(cont)
    cont.add_argument("--prompt-file")
    cont.add_argument("--prompt")
    cont.add_argument("--contract-file", help="phase contract JSON; changing to a different phaseId "
                                                 "requires the previous phase to be accepted on the board")
    cont.set_defaults(func=cmd_continue)

    result = sub.add_parser("result", help="bounded latest state and evidence pointers")
    add_repo_task(result)
    result.add_argument("--round", type=int)
    result.set_defaults(func=cmd_result)

    status = sub.add_parser("status", help="fast read-only round/check snapshot (no summary generation)")
    add_repo_task(status)
    status.add_argument("--round", type=int)
    status.set_defaults(func=cmd_status)

    cancel = sub.add_parser("cancel", help="request cancellation of the owned worker's process group")
    add_repo_task(cancel)
    cancel.set_defaults(func=cmd_cancel)

    progress = sub.add_parser("progress", help="validated atomic structured progress report from Pi")
    add_repo_task(progress)
    progress.add_argument("--round", type=int)
    progress.add_argument("--activity", choices=PROGRESS_ACTIVITIES)
    progress.add_argument("--step")
    progress.add_argument("--completed-criteria", action="append",
                          help="acceptance item id completed in this update (repeatable)")
    progress.add_argument("--next")
    progress.add_argument("--blocker")
    progress.add_argument("--evidence-ref", action="append",
                          help="safe evidence path reference (repeatable)")
    progress.add_argument("--show", action="store_true",
                          help="read the current self-report without writing")
    progress.set_defaults(func=cmd_progress)

    readiness = sub.add_parser("readiness", help="mechanical phase delivery coverage check "
                                                  "(never acceptance PASS)")
    add_repo_task(readiness)
    readiness.add_argument("--round", type=int)
    readiness.add_argument("--phase", help="expected phase id; mismatch is refused")
    readiness.add_argument("--contract-hash", help="expected contract revision; mismatch is refused")
    readiness.set_defaults(func=cmd_readiness)

    phase = sub.add_parser("phase-status", help="bounded read-only phase/budget/progress/readiness "
                                                  "projection")
    add_repo_task(phase)
    phase.add_argument("--round", type=int)
    phase.set_defaults(func=cmd_phase_status)

    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--task-dir", required=True)
    worker.add_argument("--round", type=int, required=True)
    worker.add_argument("--lock-fd", type=int, required=True)
    worker.add_argument("--timeout-seconds", type=float, required=True)
    worker.set_defaults(func=run_worker)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "_worker":
        try:
            return run_worker(args)
        finally:
            try:
                os.close(args.lock_fd)
            except OSError:
                pass
    result = args.func(args)
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    print(text)
    if args.command in ("status", "result"):
        try:
            root = canonical_root(Path(args.repo))
            record_codex_io(task_dir_for(git_common_dir(root), args.task), args.command,
                            len(text.encode("utf-8")) + 1)
        except Exception:  # noqa: BLE001 - never affects the command
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, LockHeld, OSError, subprocess.CalledProcessError) as exc:
        print(f"pi_task: {exc}", file=sys.stderr)
        raise SystemExit(2)
