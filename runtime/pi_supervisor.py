"""The detached supervisor: it launches Pi for one round, watches it, finishes the round
and records the phase round log. It never judges acceptance.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from pi_brief import WORKER_CONFIG_FILE, WORKER_READY_FILE
from pi_core import (
    TASK_CLI,
    LockHeld,
    READ_ONLY_TOOLS,
    WRITABLE_TOOLS,
    atomic,
    board_pause_active,
    git,
    lock_fd,
    read_json,
    require_allowed_model,
    runtime_version,
    terminate,
)
from pi_phase import (
    PhaseResourceMonitor,
    READINESS_FILE,
    _resource_scan_seconds,
    _resource_unknown_seconds,
    build_readiness,
    classify_readiness,
    read_phase_record,
    read_phase_state,
    write_phase_state,
    phase_budget,
)
from pi_summary import compact, summarize
from pi_execution import terminal_evidence
from pi_recovery import round_tools


def worker_env() -> dict:
    env = os.environ.copy()
    # The frozen helper snapshot is immutable evidence: never let bytecode
    # caches appear in the task tools directory while a worker runs.
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def spawn_worker(task_dir: Path, round_number: int, lock_fd_value: int, timeout_seconds: float) -> None:
    task = read_json(task_dir / "task.json", {}) or {}
    worktree = Path(task.get("worktree", "."))
    script = round_tools(task_dir,round_number) / "pi_task.py"
    argv = [sys.executable, str(script), "_worker", "--task-dir", str(task_dir),
            "--round", str(round_number), "--lock-fd", str(lock_fd_value),
            "--timeout-seconds", str(timeout_seconds)]
    with (task_dir / "supervisor.log").open("a", encoding="utf-8") as output:
        subprocess.Popen(argv, cwd=str(worktree), stdin=subprocess.DEVNULL,
                         stdout=output, stderr=output, start_new_session=True,
                         pass_fds=(lock_fd_value,), env=worker_env())


BOARD_REFRESH_SECONDS = 15.0


def board_refresh_seconds() -> float:
    raw = os.environ.get("CODEX_PI_BOARD_REFRESH_SECONDS")
    try:
        value = float(raw) if raw else BOARD_REFRESH_SECONDS
    except (TypeError, ValueError):
        value = BOARD_REFRESH_SECONDS
    return value if 0.05 <= value <= 600 else BOARD_REFRESH_SECONDS


def refresh_board_best_effort(task: dict) -> None:
    """Opt-in board projection refresh + cli-queue dispatch in the supervisor loop.

    It reuses bounded status/check evidence, never scans transcripts and never
    raises: a monitor error becomes bounded local evidence and the run goes on.
    Unregistered tasks return without touching any board file and never invoke
    the queue command.
    """
    try:
        from pi_board import supervisor_tick
        supervisor_tick(task)
    except Exception as exc:  # noqa: BLE001 - monitoring must never kill the worker
        try:
            from pi_store import record_monitor_error
            record_monitor_error(task, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------

def cancel_requested(task_dir: Path, round_number: int, field: str = "cancel.json") -> bool:
    data = read_json(task_dir / field, None)
    return bool(isinstance(data, dict) and data.get("round") == round_number)


def finish_round(task_dir: Path, round_number: int, round_dir: Path, task: dict,
                 state: dict, outcome: str, code, ended: dict) -> None:
    worktree = Path(task["worktree"])
    try:
        end_head = git(worktree, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        end_head = None
    state.update(state=outcome, exitCode=code, endedAt=time.time(), endHead=end_head, **ended)
    with (round_dir / "round.meta").open("a", encoding="utf-8") as meta:
        meta.write(f"exit={code}\nend={state['endedAt']}\nhead={end_head}\n"
                   f"outcome={outcome}\ntimed_out={bool(ended.get('timedOut'))}\n"
                   f"cancelled={bool(ended.get('cancelled'))}\n")
    atomic(round_dir / "round.state.json", state)
    try:
        data = summarize(round_dir / "round.jsonl", worktree, round_dir,
                         task.get("model"), checks_dir=round_dir / "round.checks",
                         session_dir=Path(task["sessionDir"]) if task.get("sessionDir") else None,
                         session_id=task.get("sessionId"),
                         window=(state.get("startedAt"), state.get("endedAt")))
        atomic(round_dir / "round.summary.json", data)
        (round_dir / "round.summary.txt").write_text(compact(data), encoding="utf-8")
    except Exception as exc:  # summary failure must not hide the raw evidence
        state["summaryError"] = str(exc)
        atomic(round_dir / "round.state.json", state)


def record_phase_round(task: dict, task_dir: Path, round_number: int, round_dir: Path,
                       state: dict, outcome: str) -> None:
    """Record the phase round log, the readiness snapshot and the post-round classification."""
    record, _problem = read_phase_record(task_dir)
    if not isinstance(record, dict):
        return
    contract = record.get("contract") or {}
    phase_state = read_phase_state(task_dir)
    rounds_log = phase_state.get("rounds") if isinstance(phase_state.get("rounds"), list) else []
    rounds_log = (rounds_log + [{"round": round_number, "state": outcome,
                                 "exitCode": state.get("exitCode"),
                                 "endedAt": state.get("endedAt") or time.time()}])[-50:]
    phase_state.update({"phaseId": contract.get("phaseId"),
                        "contractSha256": record.get("contractSha256"),
                        "rounds": rounds_log, "updatedAt": time.time()})
    if outcome == "completed" and state.get("exitCode") == 0:
        readiness = build_readiness(task_dir, task, round_number)
        atomic(round_dir / READINESS_FILE, readiness)
        decision = classify_readiness(readiness, record)
        phase_state["lastReadiness"] = {"status": readiness.get("status"),
                                        "phaseId": readiness.get("phaseId"),
                                        "candidate": readiness.get("candidate"),
                                        "coverage": readiness.get("coverage"),
                                        "generatedAt": readiness.get("generatedAt")}
        phase_state["lastDecision"] = {"action": decision.get("action"),
                                        "reason": decision.get("reason"), "at": time.time()}
    write_phase_state(task_dir, phase_state)


WORKER_NOT_LOADED = "WORKER_EXTENSION_NOT_LOADED"


def run_worker(args) -> int:
    task_dir = Path(args.task_dir)
    task = read_json(task_dir / "task.json", None)
    if not isinstance(task, dict):
        raise ValueError(f"worker cannot read task.json under {task_dir}")
    # Independent revalidation before any evidence write or process launch: the
    # internal _worker entry and an edited/stale task.json must not bypass the
    # model restriction.
    require_allowed_model(task.get("model"), "frozen task")
    blocked, reason = board_pause_active(task)
    if blocked and reason and reason.startswith("Codex takeover required"):
        raise ValueError(reason)
    round_number = int(args.round)
    timeout_seconds = float(args.timeout_seconds)

    # Supervisor lease: held only by this supervisor and never inherited by Pi,
    # so a vanished supervisor is detectable even while an orphaned Pi child
    # still holds the task lock. A previous supervisor may still be exiting, so
    # wait briefly for its lease instead of failing a fresh continuation.
    try:
        lock_fd(task_dir / ".supervisor.lock", blocking=True, timeout=10)
    except LockHeld as exc:
        round_dir = task_dir / "rounds" / str(round_number)
        state_path = round_dir / "round.state.json"
        state = read_json(state_path, {}) or {}
        state.update(taskDir=str(task_dir), round=round_number, state="unknown", exitCode=None,
                     endedAt=time.time(), error=f"supervisor lease unavailable: {exc}")
        atomic(state_path, state)
        return 1
    worktree = Path(task["worktree"])

    round_dir = task_dir / "rounds" / str(round_number)
    if not round_dir.is_dir():
        raise ValueError(f"round directory is missing: {round_dir}")
    for name in ("round.jsonl", "round.err"):
        (round_dir / name).touch(exist_ok=True)

    state_path = round_dir / "round.state.json"
    state = read_json(state_path, {}) or {}
    state.update(taskDir=str(task_dir), round=round_number)
    try:
        start_head = git(worktree, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        start_head = None
    state.update(state="running", supervisorPid=os.getpid(), startedAt=time.time(),
                 startHead=start_head, timedOut=False, cancelled=False, exitCode=None,
                 workerScript=str(TASK_CLI), runtimeVersion=runtime_version())
    state['deadlineAt']=state['startedAt']+timeout_seconds
    atomic(state_path, state)

    if cancel_requested(task_dir, round_number):
        atomic(task_dir / "cancel.observed", {"round": round_number, "at": time.time(),
                                              "beforeSpawn": True})
        finish_round(task_dir, round_number, round_dir, task, state, "cancelled", None,
                     {"timedOut": False, "cancelled": True,
                      "note": "cancellation was requested before Pi started"})
        # The pre-spawn cancel is a terminal exit too: make the same final
        # board notification/dispatch as every other terminal path.
        refresh_board_best_effort(task)
        return 0

    tools = READ_ONLY_TOOLS if task["readOnly"] else WRITABLE_TOOLS
    pi_bin = os.environ.get("PI_BIN") or "pi"
    argv = [pi_bin, "-p", "--mode", "json", "--session-id", task["sessionId"],
            "--session-dir", task["sessionDir"], "--model", task["model"],
            "--thinking", task["thinking"], "--tools", tools,
            "--no-extensions", "--no-approve", "-e", str(round_tools(task_dir,round_number) / "pi_worker.ts"),
            "--no-skills", "--no-prompt-templates",
            "@" + str(round_dir / "brief.md")]
    worker_environment = worker_env()
    worker_environment["CODEX_PI_WORKER_CONFIG"] = str(round_dir / WORKER_CONFIG_FILE)

    resource_monitor = None
    phase_record, _phase_problem = read_phase_record(task_dir)
    if isinstance(phase_record, dict):
        resource_contract = phase_record.get("contract") or {}
        declared_limits = resource_contract.get("resourceLimits") or []
        if declared_limits:
            resource_monitor = PhaseResourceMonitor(
                round_dir, worktree, declared_limits,
                resource_contract.get("phaseId"), phase_record.get("contractSha256"),
                round_number, _resource_unknown_seconds())

    child = None
    outcome, code = "failed", 1
    timed_out = cancelled = False
    error = None
    resource_stop_reason = None
    caught = {"signal": None}
    ready_marker = round_dir / WORKER_READY_FILE
    log_offset = 0

    def interrupted(sig, _frame):
        caught["signal"] = sig
        raise KeyboardInterrupt

    def tool_ran_before_ready() -> bool:
        """True when the JSON stream shows a tool execution but no load proof exists.

        The stream is read first and the marker second: the extension writes the
        marker before Pi can emit any tool event, so this order cannot misfire.
        """
        nonlocal log_offset
        with (round_dir / "round.jsonl").open("rb") as stream:
            stream.seek(log_offset)
            chunk = stream.read(1 << 20)
            log_offset += len(chunk)
        return b'"tool_execution_start"' in chunk and not ready_marker.is_file()

    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if isinstance(phase_record,dict):
            budget=phase_budget(task_dir,phase_record)
            remaining=budget.get('remainingSeconds')
            if remaining is None or remaining<=0:
                raise ValueError('phase deadline unavailable or exhausted before Pi spawn')
            timeout_seconds=min(timeout_seconds,remaining)
            state['deadlineAt']=min(state['deadlineAt'],budget['deadlineAt'])
            atomic(state_path,state)
        with (round_dir / "round.jsonl").open("ab") as out, \
                (round_dir / "round.err").open("ab") as err:
            # Pi inherits the task lock, so a SIGKILLed supervisor cannot free
            # capacity while its Pi child is still running.
            child = subprocess.Popen(argv, cwd=str(worktree), stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=err, start_new_session=True,
                                     env=worker_environment, pass_fds=(args.lock_fd,))
        state["piPid"] = child.pid
        atomic(state_path, state)
        deadline = time.monotonic() + max(0,state['deadlineAt']-time.time())
        board_interval = board_refresh_seconds()
        last_board_refresh = time.monotonic() - board_interval
        resource_interval = _resource_scan_seconds()
        last_resource_scan = time.monotonic() - resource_interval
        while True:
            raw = child.poll()
            if raw is not None:
                code = raw if raw >= 0 else 128 - raw
                outcome = "completed" if code == 0 else "failed"
                break
            if cancel_requested(task_dir, round_number):
                cancelled, outcome = True, "cancelled"
                break
            if time.monotonic() >= deadline:
                timed_out, outcome, code = True, "timed_out", 124
                break
            now = time.monotonic()
            if not ready_marker.is_file() and tool_ran_before_ready():
                error = f"{WORKER_NOT_LOADED}: a tool execution appeared before worker.ready"
                terminate(child)
                outcome, code = "failed", 76
                break
            if resource_monitor is not None \
                    and now - last_resource_scan >= resource_interval:
                last_resource_scan = now
                stop_reason = resource_monitor.scan()
                if stop_reason:
                    resource_stop_reason = stop_reason
                    error = error or stop_reason
                    terminate(child)
                    outcome, code = "failed", 75
                    break
            if now - last_board_refresh >= board_interval:
                last_board_refresh = now
                refresh_board_best_effort(task)
            time.sleep(0.2)
    except KeyboardInterrupt:
        outcome, code = "interrupted", 128 + (caught["signal"] or signal.SIGINT)
    except Exception as exc:
        error = str(exc)
        outcome, code = "unknown", None
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        if child is not None:
            terminate(child)
            raw = child.returncode
            if outcome == "completed":
                code = raw if raw is not None and raw >= 0 else code
            elif outcome in ("cancelled", "timed_out", "interrupted") and raw is not None:
                if outcome != "timed_out":
                    code = raw if raw >= 0 else 128 - raw
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    if outcome in ("completed", "failed") and not ready_marker.is_file():
        # Never ready: without the load proof no guard decision was ever made.
        error = error or f"{WORKER_NOT_LOADED}: the round ended without worker.ready"
        outcome = "failed"
        code = code if code not in (0, None) else 76
    execution_evidence = None
    if outcome == "completed" and code == 0:
        execution_evidence = terminal_evidence(round_dir / "round.jsonl")
        outcome = execution_evidence["status"]
        error = execution_evidence.get("error")
    elif outcome in ('failed','timed_out','cancelled','interrupted','unknown'):
        execution_evidence={'status':'unknown' if outcome=='unknown' else 'failed','reason':outcome,'error':error or f'Pi process {outcome}, exit {code}',
                            'processExitCode':code,'finalText':''}
    if cancelled:
        atomic(task_dir / "cancel.observed", {"round": round_number, "at": time.time()})
    resource_flags = {}
    if resource_monitor is not None:
        final_stop = resource_monitor.final_scan()
        resource_state = resource_monitor.state()
        if resource_state.get("status") == "breached":
            resource_flags["resourceBreached"] = True
        if resource_state.get("status") in ("unknown", "escalated"):
            resource_flags["resourceUnknown"] = True
        if resource_stop_reason or final_stop:
            resource_flags["resourceReason"] = resource_stop_reason or final_stop
            error = error or resource_flags["resourceReason"]
        write_error = resource_state.get("writeError")
        if write_error:
            resource_flags["resourceWriteError"] = write_error
            error = error or f"resource observation write failed: {write_error}"
    finish_round(task_dir, round_number, round_dir, task, state, outcome, code,
                 {"timedOut": timed_out, "cancelled": cancelled, "error": error,
                  "executionEvidence": execution_evidence, **resource_flags})
    try:
        record_phase_round(task, task_dir, round_number, round_dir, state, outcome)
    except Exception as exc:  # noqa: BLE001 - a phase-check error must still leave evidence
        error = error or f"phase delivery check failed: {type(exc).__name__}: {exc}"
        state.update(error=error)
        atomic(state_path, state)
    # One bounded final projection so terminal outcomes reach a registered card
    # without any external poller; ordinary progress already refreshed above.
    refresh_board_best_effort(task)
    return 0
