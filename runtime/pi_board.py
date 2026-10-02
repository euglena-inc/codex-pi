#!/usr/bin/env python3
"""Opt-in structured board + CLI-queue transport for Codex-Pi coordination.

A repository stores one bounded snapshot at ``<git-common-dir>/codex-pi/board.json``.
The board is a projection over existing immutable task evidence (task.json,
round.state.json, round.checks receipts); it never replaces PLAN/briefs/receipts
and it never proves acceptance.

Roles are explicit commands, not a permission framework:
  * ``register``  -- main: bind an existing task to an owning desktop thread
    UUID, title/goal/brief refs and (explicit opt-in) the ``cli-queue``
    transport; offline boards remain fully usable without CLI use;
  * ``refresh``   -- Pi/runner: project real bounded status into the card and
    publish deduplicated attention events;
  * ``decide``    -- main only: handle one exact event with an explicit
    accepted/rejected/changes_requested/resolved decision bound to an exact
    commit resolved in the registered repository;
  * ``pause``/``resume`` -- explicit persisted control state;
  * ``rearm``     -- explicit requeue after a lost/interrupted owner turn;
  * ``show``/``metrics`` -- bounded compact reads and size/usage accounting.

Modules: ``pi_store`` (board files, monitors, routes, pause), ``pi_events`` (cards and
events, status projection), ``pi_queue`` (queue claims and dispatch). This file holds
registration, refresh, decisions, compact views and the CLI.

Delivery state lives in ``board.queue.json`` and is separate from immutable
events and main decisions. The CLI queue has no caller-provided idempotency
key: a timeout or crash after send is recorded as uncertain and requires an
explicit rearm, never a blind exactly-once claim, endless retry or second model.

This module never invokes a Codex model: ``queue`` only enqueues a message for
the exact existing thread. No exec/resume/fork/app-server, MCP, daemon or
private IPC/database integration.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

# The frozen helper snapshot is immutable evidence: never write bytecode caches
# into the task tools directory. Must run before the local imports below.
sys.dont_write_bytecode = True
RUNTIME_DIR = Path(__file__).resolve().parent
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

from pi_core import (
    LockHeld,
    canonical_root,
    git_common_dir,
    lock_fd,
    lock_is_held,
    read_json,
    record_codex_io,
    require_allowed_model,
    require_task_arg,
    task_dir_for,
)
from pi_events import (
    _default_codex,
    _new_card,
    _notify_view,
    _prune_events,
    _supersede_stale_phase_events,
    _update_overflow,
    add_event,
    find_event,
    maybe_publish_progress,
    pending_events,
    project_events,
    project_status,
)
from pi_evidence import probe_worktree_head
from pi_queue import (
    DEFAULT_CODEX_BIN,
    QUEUE_LIMITATION,
    _clear_queue_claims,
    _resolve_codex_bin,
    dispatch_task,
    queue_paths,
    queue_view,
)
from pi_store import (
    BOARD_LOCK,
    DECISIONS,
    EVENT_ID_RE,
    FULL_OID_RE,
    GIT_TIMEOUT_SECONDS,
    MAX_NOTE,
    REVIEW_KINDS,
    SCHEMA_VERSION,
    TRANSPORTS,
    TRANSPORT_CLI_QUEUE,
    TRANSPORT_OFFLINE,
    _read_bounded_json,
    _text,
    _validate_thread,
    _validate_transport,
    _write_board,
    board_file_for_common,
    board_file_for_repo,
    monitor_for,
    monitor_lease_seconds,
    read_board,
    read_monitors,
    read_route,
    record_monitor_error,
    register_route,
    resume_route,
    route_paths,
    route_paused,
    write_monitor_record,
)
from pi_takeover import FAILURE_KINDS, _records as decision_records, review_policy
from pi_task import build_status


def refresh_with_status(board_file, task_id: str, status: dict, now=None, block: bool = True,
                        source: str = "cli"):
    """Short-lock projection refresh; no board write when nothing meaningful changed."""
    now = time.time() if now is None else now
    board_file = Path(board_file)
    if not board_file.is_file():
        return {"ok": True, "refreshed": False, "reason": "no board"}
    try:
        fd = lock_fd(board_file.with_name(BOARD_LOCK), blocking=block,
                     timeout=5.0 if block else 0.0)
    except LockHeld:
        write_monitor_record(board_file, task_id, {
            "task": task_id, "healthy": False, "error": "board lock was held during refresh",
            "source": source, "state": status.get("state"), "round": status.get("round")}, now)
        return {"ok": True, "refreshed": False, "reason": "board lock held"}
    try:
        board, problem = read_board(board_file)
        if board is None:
            raise ValueError(f"board state is {problem}: {board_file}")
        card = board["cards"].get(task_id)
        if not isinstance(card, dict):
            return {"ok": True, "refreshed": False, "reason": "task is not registered"}
        changed = project_status(card, status, now)
        if isinstance(card.get("phase"), dict) and _supersede_stale_phase_events(card, now):
            changed = True
        added = project_events(card, status, now)
        progress = maybe_publish_progress(card, status, now)
        if progress.get("created") is not None:
            added.append(progress["created"])
        if progress.get("changed"):
            changed = True
        _update_overflow(card, now)
        changed = changed or bool(added)
        if changed:
            board["revision"] = int(board.get("revision") or 0) + 1
            board["updatedAt"] = now
            _write_board(board_file, board)
        write_monitor_record(board_file, task_id, {
            "task": task_id, "healthy": True, "error": None, "source": source,
            "state": status.get("state"), "round": status.get("round"),
            "boardRevision": board.get("revision"), "pid": os.getpid()}, now)
        return {"ok": True, "refreshed": bool(changed), "revision": board.get("revision"),
                "taskId": task_id, "pi": dict(card.get("pi") or {}),
                "newEvents": [event["id"] for event in added],
                "pendingEvents": len(pending_events(card)),
                "overflow": card.get("overflow"),
                "card": compact_card(card)}
    finally:
        os.close(fd)


def refresh_registered_task(task: dict, now=None, block: bool = True, source: str = "supervisor"):
    """Cheap registration pre-check before any status work."""
    common_raw = task.get("commonDir") if isinstance(task, dict) else None
    task_id = task.get("task") if isinstance(task, dict) else None
    if not isinstance(common_raw, str) or not common_raw.strip() or not isinstance(task_id, str):
        return {"ok": True, "refreshed": False, "reason": "task has no commonDir"}
    board_file = board_file_for_common(Path(common_raw))
    if not board_file.is_file():
        return {"ok": True, "refreshed": False, "reason": "no board"}
    board, problem = read_board(board_file)
    if board is None:
        raise ValueError(f"board state is {problem}: {board_file}")
    if not isinstance((board.get("cards") or {}).get(task_id), dict):
        return {"ok": True, "refreshed": False, "reason": "task is not registered"}
    status = build_status(task["repo"], task_id)
    return refresh_with_status(board_file, task_id, status, now, block, source=source)


def supervisor_tick(task: dict):
    """Refresh a registered task and dispatch new actionable events via cli-queue."""
    try:
        refresh = refresh_registered_task(task, block=False, source="supervisor")
    except Exception as exc:  # noqa: BLE001 - monitor errors must not kill Pi
        try:
            record_monitor_error(task, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "refreshed": False, "reason": "monitor error"}
    dispatch = None
    common_raw = task.get("commonDir") if isinstance(task, dict) else None
    if isinstance(common_raw, str) and common_raw.strip():
        board_file = board_file_for_common(Path(common_raw))
        if board_file.is_file():
            try:
                dispatch = dispatch_task(board_file, task.get("task"))
            except Exception as exc:  # noqa: BLE001
                try:
                    record_monitor_error(task, f"dispatch error: {type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
                dispatch = {"ok": False, "dispatched": False, "error": str(exc)}
    return {"ok": True, "refresh": refresh, "dispatch": dispatch}


def route_summary(thread: str) -> dict | None:
    """Bounded recovery evidence for one registered thread; None when not routed."""
    route, problem = read_route(thread)
    if route is None:
        return None if problem == "missing" else {"thread": thread, "lines": [
            f"route state is {problem}"], "paused": False, "problem": problem}
    board_file = Path(str(route.get("boardPath", "")))
    lines = []
    board, board_problem = read_board(board_file)
    if board is None:
        lines.append(f"board state is {board_problem}")
    else:
        monitors, monitor_problem = read_monitors(board_file)
        lease = monitor_lease_seconds()
        now = time.time()
        problems = []
        for task_id in (route.get("taskIds") or [])[:5]:
            card = (board.get("cards") or {}).get(task_id)
            if not isinstance(card, dict):
                problems.append(f"task={task_id} state=not-registered")
                continue
            state = (card.get("pi") or {}).get("state")
            view = queue_view(board_file, task_id)
            record = monitor_for(monitors, task_id)
            if monitors is None:
                monitor = f"unknown ({monitor_problem})"
            elif not isinstance(record, dict):
                monitor = "missing"
            elif record.get("error"):
                monitor = f"error ({_text(record.get('error'), 120)})"
            else:
                refreshed = record.get("refreshedAt")
                if isinstance(refreshed, bool) or not isinstance(refreshed, (int, float)):
                    monitor = "corrupt"
                elif now - refreshed > lease:
                    monitor = f"stale ({int(now - refreshed)}s)"
                else:
                    monitor = "fresh"
            interesting = (bool(card.get("paused")) or monitor != "fresh"
                           or int(view.get("uncertain") or 0) > 0
                           or int(view.get("failed") or 0) > 0)
            if interesting:
                problems.append(
                    f"task={task_id} state={state} paused={bool(card.get('paused'))} "
                    f"pending={len(pending_events(card))} queued={view.get('queued')} "
                    f"uncertain={view.get('uncertain')} failed={view.get('failed')} "
                    f"monitor={monitor}")
                for failure in view.get("failures") or []:
                    problems.append(
                        f"  transport {failure.get('status')} at {failure.get('at')}: "
                        f"{_text(failure.get('error'), 120)} "
                        f"(rearm: python3 {Path(__file__).resolve()} rearm --repo "
                        f"{card.get('repo')} --task {task_id})")
        lines.extend(problems)
    paused, pause_problem = route_paused(thread)
    if pause_problem is not None:
        lines.append(f"route pause state is {pause_problem}")
    return {"thread": thread, "lines": lines[:12], "paused": bool(paused),
            "limitations": [QUEUE_LIMITATION], "note":
                "an in-process monitor cannot report its own SIGKILL; stale evidence is exposed "
                "only on the next recovery interaction, not detected automatically"}


# ---------------------------------------------------------------------------
# compact read views
# ---------------------------------------------------------------------------

def compact_event(event: dict) -> dict:
    return {"id": event.get("id"), "seq": event.get("seq"), "round": event.get("round"),
            "kind": event.get("kind"), "summary": event.get("summary"),
            "question": event.get("question"), "candidate": event.get("candidate"),
            "phaseId": event.get("phaseId"), "contractHash": event.get("contractHash"),
            "coverage": event.get("coverage"),
            "evidence": event.get("evidence"), "createdAt": event.get("createdAt"),
            "handled": bool(event.get("handled")), "decision": event.get("decision"),
            "reviewedHead": event.get("reviewedHead")}


def compact_card(card: dict, max_events: int = 5) -> dict:
    events = [event for event in card.get("events", []) if isinstance(event, dict)]
    pending = [event for event in events if not event.get("handled")]
    handled = [event for event in events if event.get("handled")]
    return {
        "taskId": card.get("taskId"), "ownerThread": card.get("ownerThread"),
        "title": card.get("title"), "goal": card.get("goal"),
        "briefRef": card.get("briefRef"), "planRef": card.get("planRef"),
        "repo": card.get("repo"), "worktree": card.get("worktree"),
        "transport": card.get("transport"), "codexBin": card.get("codexBin"),
        "paused": bool(card.get("paused")), "pausedAt": card.get("pausedAt"),
        "pauseNote": card.get("pauseNote"), "pi": card.get("pi"),
        "phase": card.get("phase"), "progress": card.get("progress"),
        "reviewPolicy": review_policy(card),
        "evidence": card.get("evidence"), "check": card.get("check"),
        "progressNotify": _notify_view(card),
        "attention": card.get("attention"), "codex": card.get("codex"),
        "pendingEvents": [compact_event(event) for event in pending[:max_events]],
        "pendingCount": len(pending),
        "handledCount": len(card.get("handled") or {}),
        "overflow": card.get("overflow"),
        "recentHandled": [compact_event(event) for event in handled[-2:]],
    }


def select_cards(board: dict, thread=None, task_id=None, include_all=False) -> list:
    cards = board.get("cards") or {}
    if task_id is not None:
        card = cards.get(task_id)
        return [card] if isinstance(card, dict) else []
    if thread:
        return [card for card in cards.values()
                if isinstance(card, dict) and card.get("ownerThread") == thread]
    if include_all:
        return [card for card in cards.values() if isinstance(card, dict)][:50]
    raise ValueError("provide --task, --thread or --all")


def _monitor_view(board_file, task_ids) -> dict:
    monitors, problem = read_monitors(board_file)
    lease = monitor_lease_seconds()
    now = time.time()
    view = {}
    for task_id in task_ids:
        if monitors is None:
            view[task_id] = {"status": f"unknown ({problem})", "leaseSeconds": lease}
            continue
        record = monitor_for(monitors, task_id)
        if not isinstance(record, dict):
            view[task_id] = {"status": "missing", "leaseSeconds": lease}
            continue
        refreshed = record.get("refreshedAt")
        entry = {"status": "healthy", "refreshedAt": refreshed,
                 "ageSeconds": int(now - refreshed) if isinstance(refreshed, (int, float))
                 and not isinstance(refreshed, bool) else None,
                 "source": record.get("source"), "error": record.get("error"),
                 "healthy": bool(record.get("healthy")), "leaseSeconds": lease}
        if entry["error"]:
            entry["status"] = "error"
        elif entry["ageSeconds"] is not None and entry["ageSeconds"] > lease:
            entry["status"] = "stale"
        view[task_id] = entry
    return view


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def register_task(repo, task, thread=None, transport=TRANSPORT_OFFLINE, codex_bin=None,
                  title=None, goal=None, brief_ref=None, plan_ref=None, codex_task_id=None,
                  now=None) -> dict:
    now = time.time() if now is None else now
    root = canonical_root(Path(repo))
    common = git_common_dir(root)
    task_id = require_task_arg(task)
    task_dir = task_dir_for(common, task_id)
    if not task_dir.is_dir():
        raise ValueError(f"unknown task {task_id!r}; no evidence at {task_dir}")
    frozen = read_json(task_dir / "task.json", None)
    if not isinstance(frozen, dict) or frozen.get("task") != task_id:
        raise ValueError(f"task {task_id!r} has no readable task.json; inspect {task_dir}")
    require_allowed_model(frozen.get("model"), "board registration")
    transport = _validate_transport(transport)
    owner_thread = _validate_thread(thread, required=(transport == TRANSPORT_CLI_QUEUE))
    resolved_bin = None
    if codex_bin:
        resolved_bin = _resolve_codex_bin(codex_bin)
    elif transport == TRANSPORT_CLI_QUEUE:
        resolved_bin = _resolve_codex_bin(DEFAULT_CODEX_BIN)
    board_file = board_file_for_common(common)
    board_file.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_fd(board_file.with_name(BOARD_LOCK), blocking=True, timeout=10)
    try:
        board = None
        if board_file.exists():
            board, problem = read_board(board_file)
            if board is None:
                raise ValueError(f"existing board is {problem}: {board_file}")
        if board is None:
            board = {"schemaVersion": SCHEMA_VERSION, "revision": 0,
                     "createdAt": now, "updatedAt": now, "cards": {}}
        cards = board.setdefault("cards", {})
        card = cards.get(task_id)
        if not isinstance(card, dict):
            card = _new_card(task_id, owner_thread, title or task_id, goal or "", brief_ref,
                             plan_ref, root, common, frozen.get("worktree"), transport,
                             resolved_bin, now)
            cards[task_id] = card
        else:
            card["ownerThread"] = owner_thread or card.get("ownerThread")
            card["codexTaskId"] = codex_task_id or card.get("codexTaskId") \
                or owner_thread or task_id
            card["transport"] = transport
            card["codexBin"] = resolved_bin if transport == TRANSPORT_CLI_QUEUE \
                else card.get("codexBin")
            if title:
                card["title"] = _text(title, 200)
            if goal:
                card["goal"] = _text(goal, 600)
            if brief_ref:
                card["briefRef"] = _text(brief_ref, 400)
            if plan_ref:
                card["planRef"] = _text(plan_ref, 400)
            card["repo"] = str(root)
            card["commonDir"] = str(common)
            card["worktree"] = frozen.get("worktree")
            card["updatedAt"] = now
        board["revision"] = int(board.get("revision") or 0) + 1
        board["updatedAt"] = now
        _write_board(board_file, board)
    finally:
        os.close(fd)
    route = None
    if transport == TRANSPORT_CLI_QUEUE:
        route = register_route(owner_thread, board_file, [task_id], transport, now)
    try:
        refresh_with_status(board_file, task_id, build_status(str(root), task_id), now,
                            block=True, source="register")
    except (ValueError, OSError, LockHeld):
        pass
    dispatch = None
    if transport == TRANSPORT_CLI_QUEUE:
        dispatch = dispatch_task(board_file, task_id, now=now)
    queue = queue_view(board_file, task_id)
    paused = None
    if transport == TRANSPORT_CLI_QUEUE:
        paused, pause_problem = route_paused(owner_thread)
        paused = True if pause_problem is not None else bool(paused)
    warning = None
    if paused:
        warning = (f"owner thread {owner_thread} is paused (user interrupt or unreadable pause state); "
                   "nothing is delivered until the user authorizes: python3 "
                   f"{shlex.quote(str(Path(__file__).resolve()))} resume --repo {shlex.quote(str(root))} "
                   f"--task {task_id} --thread {owner_thread}, then register again")
    return {
        "ok": True, "taskId": task_id, "ownerThread": owner_thread,
        "routePaused": paused, "warning": warning,
        "mode": transport, "transport": transport, "codexBin": card.get("codexBin"),
        "boardPath": str(board_file), "queuePath": str(queue_paths(board_file)[0]),
        "routePath": str(route_paths(owner_thread)[0]) if route else None,
        "revision": board.get("revision"),
        "queueArgvTemplate": ([card.get("codexBin") or DEFAULT_CODEX_BIN, "--disable",
                               "daemon_auto_start", "queue", "--thread", owner_thread,
                               "--message", "<bounded runtime-handoff packet>"]
                              if transport == TRANSPORT_CLI_QUEUE else None),
        "refreshDispatch": dispatch,
        "queue": queue,
        "testCommands": [
            f"python3 {shlex.quote(str(Path(__file__).resolve()))} refresh --repo "
            f"{shlex.quote(str(root))} --task {task_id}",
        ],
        "limitations": [QUEUE_LIMITATION],
        "note": "registration refreshes and dispatches an already-terminal task; offline mode never "
                "invokes Codex. Reading or delivering an event does not handle or accept it.",
    }


def _resolve_commit(worktree, object_id):
    """Exact full object id resolved as a commit in the registered worktree."""
    if not isinstance(object_id, str) or not FULL_OID_RE.fullmatch(object_id):
        return None, "must be a full 40- or 64-hex object id"
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet",
             f"{object_id}^{{commit}}"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SECONDS, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not be resolved with bounded git: {exc}"
    if proc.returncode != 0:
        return None, "is not a commit object in the registered repository/worktree"
    resolved = proc.stdout.strip().lower()
    if not FULL_OID_RE.fullmatch(resolved):
        return None, "git returned an unexpected object id"
    return resolved, None


def decide(repo, task, event_id, decision, reviewed_head=None, note=None, phase=None,
           contract_hash=None, now=None, failure_kind=None) -> dict:
    now = time.time() if now is None else now
    event_id = str(event_id)
    if not EVENT_ID_RE.fullmatch(event_id):
        raise ValueError("event id must be a 64-character lowercase hex digest")
    mapped = DECISIONS.get(decision)
    if mapped is None:
        raise ValueError("decision must be accept, reject, changes_requested or resolve")
    if failure_kind is not None and (failure_kind not in FAILURE_KINDS or mapped not in ("rejected", "changes_requested")):
        raise ValueError("--failure-kind applies only to reject/changes_requested: quality or external")
    if failure_kind == "external" and not (isinstance(note, str) and note.strip()):
        raise ValueError("external blockers require --note with the missing input/authority and unlock condition")
    _root, _common, board_file = board_file_for_repo(repo)
    task_id = require_task_arg(task)
    fd = lock_fd(board_file.with_name(BOARD_LOCK), blocking=True, timeout=10)
    try:
        board, problem = read_board(board_file)
        if board is None:
            raise ValueError(f"board state is {problem}: {board_file}")
        card = board["cards"].get(task_id)
        if not isinstance(card, dict):
            raise ValueError(f"task {task_id!r} is not registered on this board")
        phase_info = card.get("phase") if isinstance(card.get("phase"), dict) else {}
        event = find_event(card, event_id)
        history = (card.get("handled") or {}).get(event_id) if event is None else None
        if event is None and not isinstance(history, dict):
            raise ValueError(f"unknown event {event_id} for task {task_id!r}")
        effective_kind = failure_kind or ("quality" if mapped in ("rejected", "changes_requested") else None)
        prior_decision = event if event is not None else history
        if prior_decision.get("handled", event is None) and failure_kind is not None and prior_decision.get("failureKind", "quality") != failure_kind:
            raise ValueError("conflicting replay: failure classification is immutable")
        if event is None:
            if history.get("phaseId") and mapped == "accepted" \
                    and (phase != history.get("phaseId")
                         or contract_hash != history.get("contractHash")):
                raise ValueError("accept replay for a phase event must bind --phase and "
                                 "--contract-hash to the exact contract revision")
            same = history.get("decision") == mapped and (
                mapped != "accepted" or history.get("reviewedHead") == reviewed_head)
            if same:
                return {"ok": True, "idempotent": True, "taskId": task_id, "eventId": event_id,
                        "decision": mapped, "revision": board.get("revision")}
            raise ValueError(f"event {event_id} was already decided as "
                             f"{history.get('decision')!r}; refusing to overwrite or replay it "
                             "with a different decision/reviewed head")
        kind = event.get("kind")
        event_phase = event.get("phaseId")
        if event.get("handled"):
            if event_phase and mapped == "accepted" and (phase != event_phase
                                                         or contract_hash != event.get("contractHash")):
                raise ValueError("accept replay for a phase event must bind --phase and "
                                 "--contract-hash to the exact contract revision")
            same = event.get("decision") == mapped and (
                mapped != "accepted" or event.get("reviewedHead") == reviewed_head)
            if same:
                return {"ok": True, "idempotent": True, "taskId": task_id, "eventId": event_id,
                        "decision": mapped, "revision": board.get("revision")}
            if mapped == "accepted" and event.get("reviewedHead") != reviewed_head:
                raise ValueError("conflicting replay: this event was already accepted with a "
                                 "different reviewed head")
            raise ValueError(f"event {event_id} is already handled as "
                             f"{event.get('decision')!r}; refusing to overwrite a decision")
        if event_phase or event.get("contractHash"):
            # Phase events are only meaningful for the exact installed contract and
            # current candidate/round. Stale evidence can never be accepted or sent
            # back as if it were the current delivery.
            if mapped == "accepted" and (phase != event_phase
                                         or contract_hash != event.get("contractHash")):
                raise ValueError("accept for a phase event must bind --phase and --contract-hash "
                                 "to the exact contract revision")
            if event_phase != phase_info.get("phaseId") \
                    or event.get("contractHash") != phase_info.get("contractHash"):
                raise ValueError(f"stale phase event: the current phase is "
                                 f"{phase_info.get('phaseId')!r}/"
                                 f"{phase_info.get('contractHash')!r}; refresh the board and use "
                                 "the current review event")
            card_round = (card.get("pi") or {}).get("round")
            if event.get("round") != card_round:
                raise ValueError(f"stale phase event: round {event.get('round')} is not the current "
                                 f"round {card_round}")
            if (event.get("candidate") or {}).get("head") != phase_info.get("candidate"):
                raise ValueError("stale phase event: its candidate is no longer the current phase "
                                 "candidate")
        if mapped == "accepted":
            if kind not in REVIEW_KINDS:
                raise ValueError(f"event kind {kind!r} is a fault/observation; "
                                 "use --decision resolve, not accept")
            if event_phase:
                # Re-confirm the live task state instead of trusting a possibly
                # stale board projection: round, contract, candidate, readiness,
                # worktree HEAD and writer-free must all still agree.
                live_root = card.get("repo") or card.get("worktree") or _root
                try:
                    live = build_status(str(live_root), task_id)
                except Exception as exc:  # noqa: BLE001 - fail closed on unknown live state
                    raise ValueError(f"cannot re-confirm the live phase state: "
                                     f"{type(exc).__name__}: {exc}") from None
                live_phase = live.get("phase") if isinstance(live.get("phase"), dict) else {}
                live_candidate = live.get("candidate") \
                    if isinstance(live.get("candidate"), dict) else {}
                live_readiness = live_phase.get("readiness") \
                    if isinstance(live_phase.get("readiness"), dict) else {}
                live_contract = live_phase.get("contractSha256")
                live_head = live_candidate.get("head") \
                    if live_candidate.get("status") == "known" else None
                event_head = (event.get("candidate") or {}).get("head")
                problems = []
                if live.get("round") != event.get("round"):
                    problems.append(f"live round {live.get('round')} != event round "
                                    f"{event.get('round')}")
                if live_contract != event.get("contractHash"):
                    problems.append("the installed contract revision changed")
                if live_candidate.get("status") != "known":
                    problems.append(f"the live candidate is unknown "
                                    f"({live_candidate.get('reason')})")
                elif live_head != event_head:
                    problems.append("the live candidate no longer matches the event candidate")
                if phase_info.get("candidate") not in (None, live_head):
                    problems.append("the stored board candidate is stale")
                if phase_info.get("contractHash") != live_contract:
                    problems.append("the stored board contract revision is stale")
                if live_readiness.get("status") != "ready":
                    problems.append(f"live readiness is {live_readiness.get('status')!r}: "
                                    f"{live_readiness.get('reason')}")
                worktree_probe = card.get("worktree") or _root
                head_now, head_problem = probe_worktree_head(Path(str(worktree_probe)))
                if head_problem is not None:
                    problems.append(f"worktree HEAD is unknown: {head_problem}")
                elif head_now != event_head:
                    problems.append("the worktree HEAD no longer matches the reviewed candidate")
                evidence_dir = task_dir_for(_common, task_id)
                if lock_is_held(evidence_dir / ".task.lock") \
                        or lock_is_held(evidence_dir / ".supervisor.lock"):
                    problems.append("a worker or supervisor lock is still held")
                if problems:
                    raise ValueError("refusing a stale phase accept: " + "; ".join(problems))
            event_head = (event.get("candidate") or {}).get("head")
            worktree = card.get("worktree") or _root
            resolved_reviewed, problem = _resolve_commit(worktree, reviewed_head)
            if problem is not None:
                raise ValueError(f"reviewed head {reviewed_head!r} {problem}")
            resolved_event, problem = _resolve_commit(worktree, event_head)
            if problem is not None:
                raise ValueError(f"event candidate head {event_head!r} {problem}")
            if resolved_reviewed != resolved_event:
                raise ValueError("reviewed head does not resolve to the event candidate commit")
            reviewed_head = resolved_reviewed
        elif reviewed_head is not None and not FULL_OID_RE.fullmatch(str(reviewed_head)):
            raise ValueError("--reviewed-head must be a full 40- or 64-hex object id")
        latest_review = None
        for item in card.get("events", []):
            if isinstance(item, dict) and item.get("kind") in REVIEW_KINDS \
                    and not item.get("handled"):
                if latest_review is None or (item.get("seq") or 0) > (latest_review.get("seq") or 0):
                    latest_review = item
        card_round = (card.get("pi") or {}).get("round")
        is_current = (kind in REVIEW_KINDS and latest_review is not None
                      and latest_review.get("id") == event_id
                      and event.get("round") == card_round)
        event.update(handled=True, handledAt=now, decision=mapped, failureKind=effective_kind,
                     reviewedHead=str(reviewed_head) if reviewed_head else None,
                     note=_text(note, MAX_NOTE) if note else None)
        card.setdefault("handled", {})[event_id] = {
            "at": now, "decision": mapped,
            "reviewedHead": str(reviewed_head) if reviewed_head else None,
            "round": event.get("round"), "phaseId": event.get("phaseId"),
            "contractHash": event.get("contractHash"), "eventKind": kind,
            "failureKind": effective_kind}
        if event_phase:
            phase_card = card.setdefault("phase", {})
            phase_card["decidedEventId"] = event_id
            phase_card["lastDecision"] = mapped
            phase_card["lastDecisionAt"] = now
            if mapped == "accepted":
                phase_card["status"] = "accepted"
                phase_card["acceptedHead"] = str(reviewed_head) if reviewed_head else None
                phase_card["acceptedAt"] = now
                phase_card["reviewEventId"] = event_id
            elif mapped == "changes_requested":
                phase_card["status"] = "changes_requested"
            elif mapped == "rejected":
                phase_card["status"] = "rejected"
            phase_card["updatedAt"] = now
        _prune_events(card)
        _update_overflow(card, now)
        codex = card.setdefault("codex", _default_codex())
        codex["lastEventId"] = event_id
        codex["lastDecision"] = mapped
        codex["lastDecisionAt"] = now
        if is_current:
            codex["review"] = mapped
            if mapped == "accepted":
                codex["reviewedHead"] = str(reviewed_head)
            codex["decidedAt"] = now
        else:
            history_entry = {"eventId": event_id, "decision": mapped, "at": now,
                             "round": event.get("round"),
                             "reviewedHead": str(reviewed_head) if reviewed_head else None,
                             "phaseId": event.get("phaseId"),
                             "contractHash": event.get("contractHash")}
            codex["history"] = (codex.get("history") or [])[-19:] + [history_entry]
        policy = review_policy(card)
        if policy["takeoverRequired"]:
            existing_latch = codex.get("takeover") \
                if isinstance(codex.get("takeover"), dict) else None
            if isinstance(existing_latch, dict) and existing_latch.get("required"):
                existing_latch.update({"required": True, "scope": policy["scope"],
                                       "outcome": policy.get("outcome"),
                                       "limit": policy["limit"],
                                       "failedReports": policy["failedReports"]})
            else:
                # First reach of the pinned limit: keep the exact time so a later
                # acceptance can be proven to have happened after the latch.
                codex["takeover"] = {"required": True, "at": now, "scope": policy["scope"],
                                     "outcome": policy.get("outcome"),
                                     "limit": policy["limit"],
                                     "failedReports": policy["failedReports"]}
            takeover = add_event(card, "codex_takeover_required", card_round,
                                 "quality-failure-limit-reached",
                                 f"Codex must take over implementation after {policy['limit']} "
                                 f"failed reviewed "
                                 f"deliver{'y' if policy['limit'] == 1 else 'ies'}",
                                 event.get("candidate") or {}, {"reviewPolicy": policy},
                                 policy["instruction"], now)
            if takeover is not None:
                takeover["phaseId"] = event_phase
                takeover["contractHash"] = event.get("contractHash")
        board["revision"] = int(board.get("revision") or 0) + 1
        board["updatedAt"] = now
        _write_board(board_file, board)
        return {"ok": True, "idempotent": False, "taskId": task_id, "eventId": event_id,
                "decision": mapped, "reviewedHead": reviewed_head, "aggregate": is_current,
                "reviewPolicy": policy,
                "phaseId": event_phase, "contractHash": event.get("contractHash"),
                "revision": board["revision"], "pendingCount": len(pending_events(card)),
                "note": "decision binds this exact event/candidate/round; accepted is explicit "
                        "main review of the current review event, never exit 0"}
    finally:
        os.close(fd)


def set_paused(repo, task, paused: bool, note=None, now=None) -> dict:
    now = time.time() if now is None else now
    _root, _common, board_file = board_file_for_repo(repo)
    task_id = require_task_arg(task)
    fd = lock_fd(board_file.with_name(BOARD_LOCK), blocking=True, timeout=10)
    try:
        board, problem = read_board(board_file)
        if board is None:
            raise ValueError(f"board state is {problem}: {board_file}")
        card = board["cards"].get(task_id)
        if not isinstance(card, dict):
            raise ValueError(f"task {task_id!r} is not registered on this board")
        card["paused"] = bool(paused)
        card["pausedAt"] = now if paused else None
        card["pauseNote"] = _text(note, MAX_NOTE) if note else None
        card["updatedAt"] = now
        board["revision"] = int(board.get("revision") or 0) + 1
        board["updatedAt"] = now
        _write_board(board_file, board)
        return {"ok": True, "taskId": task_id, "paused": bool(paused),
                "revision": board["revision"],
                "note": ("paused: queue dispatch stops and progress events do not resume work"
                         if paused else "resumed explicitly; pending events and decisions are unchanged")}
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_register(args) -> dict:
    return register_task(args.repo, args.task, thread=args.thread, transport=args.transport,
                         codex_bin=args.codex_bin, title=args.title, goal=args.goal,
                         brief_ref=args.brief_ref, plan_ref=args.plan_ref,
                         codex_task_id=args.codex_task_id)


def cmd_rearm(args) -> dict:
    _root, _common, board_file = board_file_for_repo(args.repo)
    task_id = require_task_arg(args.task)
    return _clear_queue_claims(board_file, task_id, event_id=args.event_id)


def cmd_refresh(args) -> dict:
    root, _common, board_file = board_file_for_repo(args.repo)
    task_id = require_task_arg(args.task)
    return refresh_with_status(board_file, task_id, build_status(str(root), task_id),
                               block=True, source="cli")


def cmd_show(args) -> dict:
    _root, _common, board_file = board_file_for_repo(args.repo)
    board, problem = read_board(board_file)
    if board is None:
        raise ValueError(f"board state is {problem}: {board_file}")
    thread = _validate_thread(args.thread, required=False) if args.thread else None
    cards = select_cards(board, thread=thread, task_id=args.task, include_all=args.all)
    task_ids = [card.get("taskId") for card in cards]
    queues = {}
    for task_id in task_ids:
        view = queue_view(board_file, task_id)
        if isinstance(view, dict):
            # The fixed queue limitation is stated once in the Skill/registration
            # output; repeating it per task in every read only costs tokens.
            view = {key: value for key, value in view.items() if key != "limitations"}
        queues[task_id] = view
    return {"ok": True, "revision": board.get("revision"), "count": len(cards),
            "cards": [compact_card(card) for card in cards],
            "monitor": _monitor_view(board_file, task_ids), "queue": queues}


def cmd_decide(args) -> dict:
    return decide(args.repo, args.task, args.event_id, args.decision,
                  reviewed_head=args.reviewed_head, note=args.note,
                  phase=args.phase, contract_hash=args.contract_hash,
                  failure_kind=args.failure_kind)


def cmd_pause(args) -> dict:
    return set_paused(args.repo, args.task, True, note=args.note)


def cmd_resume(args) -> dict:
    result = set_paused(args.repo, args.task, False, note=args.note)
    thread = args.thread or os.environ.get("CODEX_THREAD_ID")
    if thread:
        result["routePause"] = resume_route(thread)
    return result


def cmd_recover(args) -> dict:
    thread = _validate_thread(args.thread or os.environ.get("CODEX_THREAD_ID"))
    summary = route_summary(thread)
    if summary is None:
        raise ValueError(f"no queue route is registered for thread {thread}")
    return {"ok": True, **summary}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi_board.py",
        description="Opt-in structured Codex-Pi board projection with the verified CLI queue "
                    "transport. Never invokes a Codex model or agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="bind an existing task to a thread and transport")
    register.add_argument("--repo", required=True)
    register.add_argument("--task", required=True)
    register.add_argument("--transport", choices=TRANSPORTS, default=TRANSPORT_OFFLINE)
    register.add_argument("--thread", help="exact owner desktop thread UUID (required for cli-queue)")
    register.add_argument("--codex-bin", help="resolved executable for queue commands")
    register.add_argument("--title")
    register.add_argument("--goal")
    register.add_argument("--brief-ref")
    register.add_argument("--plan-ref")
    register.add_argument("--codex-task-id")
    register.set_defaults(func=cmd_register)

    rearm = sub.add_parser("rearm", help="explicitly requeue after a lost/interrupted owner turn")
    rearm.add_argument("--repo", required=True)
    rearm.add_argument("--task", required=True)
    rearm.add_argument("--event-id")
    rearm.set_defaults(func=cmd_rearm)

    refresh = sub.add_parser("refresh", help="one-shot bounded card projection refresh")
    refresh.add_argument("--repo", required=True)
    refresh.add_argument("--task", required=True)
    refresh.set_defaults(func=cmd_refresh)

    show = sub.add_parser("show", help="bounded compact cards for one thread or task")
    show.add_argument("--repo", required=True)
    show.add_argument("--thread")
    show.add_argument("--task")
    show.add_argument("--all", action="store_true")
    show.set_defaults(func=cmd_show)

    metrics = sub.add_parser("metrics", help="one compact JSON line per task: rounds, usage/cost, "
                                             "review decisions, takeover, Codex-facing bytes")
    metrics.add_argument("--repo", required=True)
    metrics.add_argument("--task")
    metrics.set_defaults(func=cmd_metrics)

    decide = sub.add_parser("decide", help="handle one exact event with an explicit decision")
    decide.add_argument("--repo", required=True)
    decide.add_argument("--task", required=True)
    decide.add_argument("--event-id", required=True)
    decide.add_argument("--decision", required=True, choices=sorted(DECISIONS))
    decide.add_argument("--reviewed-head")
    decide.add_argument("--phase", help="required with accept for a phase-bound event; the exact "
                                          "phase id")
    decide.add_argument("--contract-hash", help="required with accept for a phase-bound event; the "
                                                 "exact contract revision hash")
    decide.add_argument("--failure-kind", choices=FAILURE_KINDS,
                        help="negative delivery decision: quality (default) counts toward the "
                             "pinned task limit; external requires an explanatory note and "
                             "does not count")
    decide.add_argument("--note")
    decide.set_defaults(func=cmd_decide)

    pause = sub.add_parser("pause", help="persist explicit pause; queue dispatch stops")
    pause.add_argument("--repo", required=True)
    pause.add_argument("--task", required=True)
    pause.add_argument("--note")
    pause.set_defaults(func=cmd_pause)

    resume = sub.add_parser("resume", help="explicitly resume a card and/or route pause")
    resume.add_argument("--repo", required=True)
    resume.add_argument("--task", required=True)
    resume.add_argument("--thread", help="also clear an interrupt pause for this thread")
    resume.add_argument("--note")
    resume.set_defaults(func=cmd_resume)

    recover = sub.add_parser("recover", help="bounded recovery evidence for one thread route")
    recover.add_argument("--thread")
    recover.set_defaults(func=cmd_recover)
    return parser


def _record_show_bytes(args, result, total: int) -> None:
    """Attribute a ``show`` stdout to the tasks it covered.

    One output may cover several tasks; its bytes are split evenly (remainder to
    the first) so the per-task sums equal what was printed, never double counted.
    Each line also carries ``shared`` (number of tasks) and ``total``.
    """
    try:
        _root, _common, board_file = board_file_for_repo(args.repo)
        ids = [card.get("taskId") for card in result.get("cards") or []
               if isinstance(card, dict) and isinstance(card.get("taskId"), str)]
        for index, task_id in enumerate(ids):
            share = total // len(ids) + (total % len(ids) if index == 0 else 0)
            record_codex_io(board_file.parent / "tasks" / task_id, "show", share,
                            shared=len(ids), total=total)
    except Exception:  # noqa: BLE001 - never affects the command
        pass


def _sum_known(total: dict, usage) -> None:
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def task_metrics(board_file: Path, card, task_id: str) -> dict:
    """One compact metrics record; unknown stays unknown, never a partial sum as complete."""
    task_dir = Path(board_file).parent / "tasks" / task_id
    rounds = []
    if (task_dir / "rounds").is_dir():
        rounds = sorted(int(entry.name) for entry in (task_dir / "rounds").iterdir()
                        if entry.name.isdigit() and entry.is_dir())
    usage, cost = {}, 0.0
    missing, incomplete, cost_unknown = [], [], []
    for number in rounds:
        summary, problem = _read_bounded_json(task_dir / "rounds" / str(number)
                                              / "round.summary.json", 4_000_000)
        if not isinstance(summary, dict) or "usage" not in summary:
            missing.append(number)
            cost_unknown.append(number)
            continue
        _sum_known(usage, summary.get("usage"))
        if not summary.get("usage_complete"):
            incomplete.append(number)
        value = summary.get("reported_cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cost += value
        else:
            cost_unknown.append(number)
    record = {"task": task_id, "rounds": len(rounds),
              "usage": {"known": usage, "complete": bool(rounds) and not missing and not incomplete,
                        "roundsMissing": missing, "roundsIncomplete": incomplete},
              "costUsd": {"known": cost, "complete": bool(rounds) and not cost_unknown,
                          "roundsUnknown": cost_unknown}}
    if isinstance(card, dict):
        by_kind, failure_kinds = {}, {}
        for row in decision_records(card).values():
            decision = row.get("decision")
            if not decision:
                continue
            kind = row.get("eventKind") or row.get("kind") or "unknown"
            by_kind.setdefault(kind, {})
            by_kind[kind][decision] = by_kind[kind].get(decision, 0) + 1
            if decision in ("rejected", "changes_requested"):
                failure = row.get("failureKind", "quality")
                failure_kinds[failure] = failure_kinds.get(failure, 0) + 1
        record.update(registered=True, decisions=by_kind, failureKinds=failure_kinds,
                      takeover=bool(review_policy(card).get("takeoverRequired")))
    else:
        record.update(registered=False, decisions=None, failureKinds=None, takeover=None)
    by_command, packet, total, bad = {}, 0, 0, 0
    path = task_dir / "codex-io.jsonl"
    tracked = path.is_file()
    if tracked:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
                size = row["bytes"]
                command = str(row["command"])
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    raise ValueError("bad size")
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            total += size
            if row.get("kind") == "packet":
                packet += size
            else:
                by_command[command] = by_command.get(command, 0) + size
    record["codexBytes"] = {"tracked": tracked, "packet": packet, "commands": by_command,
                            "total": total, "badLines": bad}
    return record


def cmd_metrics(args) -> list:
    _root, _common, board_file = board_file_for_repo(args.repo)
    board, _problem = read_board(board_file)
    cards = (board or {}).get("cards") or {}
    if args.task:
        ids = [require_task_arg(args.task)]
    else:
        directory = Path(board_file).parent / "tasks"
        found = {entry.name for entry in directory.iterdir()
                 if entry.is_dir() and (entry / "task.json").is_file()} if directory.is_dir() else set()
        ids = sorted(found | set(cards))
    return [task_metrics(board_file, cards.get(task_id), task_id) for task_id in ids]


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = args.func(args)
    except (ValueError, LockHeld, OSError) as exc:
        print(f"pi_board: {exc}", file=sys.stderr)
        return 2
    if isinstance(result, list):  # metrics: one compact JSON line per task
        for item in result:
            print(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        return 0
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    print(text)
    if args.command == "show":
        _record_show_bytes(args, result, len(text.encode("utf-8")) + 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
