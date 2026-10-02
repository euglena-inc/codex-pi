"""CLI-queue delivery: queue claim state, the bounded delivery card and the single
dispatch of new events through the verified ``codex queue`` command.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from pi_core import LockHeld, atomic, lock_fd, record_codex_io, terminate
from pi_events import PROGRESS_EVENT_KIND, _decide_hint, _phase_event_stale, pending_events
from pi_evidence import acceptance_line
from pi_store import (
    BOARD_CLI,
    BOARD_DIR,
    BOARD_FILE,
    REVIEW_KINDS,
    THREAD_RE,
    TRANSPORT_CLI_QUEUE,
    _read_bounded_json,
    _serialized,
    _text,
    read_board,
    route_paused,
)
from pi_takeover import review_policy


QUEUE_FILE = "board.queue.json"
QUEUE_LOCK = "board.queue.lock"
MAX_QUEUE_BYTES = 131_072
MAX_QUEUE_TASKS = 200
MAX_QUEUE_FAILURES = 10
DISPATCH_TIMEOUT_SECONDS = 20.0
MAX_TRANSPORT_RETRIES = 2
MAX_CLI_OUTPUT_BYTES = 4096
QUEUE_STALE_INFLIGHT_SECONDS = 120.0
MAX_PACKET_CHARS = 1200  # UTF-8 bytes: the delivery card limit
MAX_PACKET_EVENTS = 3
DEFAULT_CODEX_BIN = "codex"
QUEUE_LIMITATION = ("the CLI queue has no caller-provided idempotency key; a timeout, crash or "
                    "nonzero exit after spawn is recorded as uncertain and requires explicit "
                    "rearm; explicit recovery of an uncertain delivery may duplicate it and is "
                    "never an exactly-once guarantee")


class QueueOverflow(ValueError):
    """The bounded queue snapshot cannot hold the delivery state."""


# ---------------------------------------------------------------------------
# queue delivery state (separate from events and decisions)
# ---------------------------------------------------------------------------

def queue_paths(board_file):
    directory = Path(board_file).parent
    return directory / QUEUE_FILE, directory / QUEUE_LOCK


def read_queue(board_file):
    path, _lock = queue_paths(board_file)
    if not path.exists():
        return {"schemaVersion": 1, "tasks": {}}, None
    data, problem = _read_bounded_json(path, MAX_QUEUE_BYTES)
    if problem is not None:
        return None, problem
    if not isinstance(data, dict) or data.get("schemaVersion") != 1 \
            or not isinstance(data.get("tasks"), dict):
        return None, "invalid"
    return data, None


def _queue_entry(queue: dict, task_id: str) -> dict:
    entry = queue.setdefault("tasks", {}).setdefault(
        task_id, {"claims": {}, "failures": [], "lastStatus": "idle"})
    if not isinstance(entry.get("claims"), dict):
        entry["claims"] = {}
    if not isinstance(entry.get("failures"), list):
        entry["failures"] = []
    return entry


def _write_queue(board_file, queue) -> None:
    tasks = queue.get("tasks") or {}
    if len(tasks) > MAX_QUEUE_TASKS:
        ordered = sorted(tasks.items(),
                         key=lambda item: (item[1] or {}).get("updatedAt") or 0)
        queue["tasks"] = dict(ordered[-MAX_QUEUE_TASKS:])
    if len(_serialized(queue)) > MAX_QUEUE_BYTES:
        raise QueueOverflow(
            f"queue snapshot would exceed MAX_QUEUE_BYTES={MAX_QUEUE_BYTES}; refusing the write "
            "instead of silently dropping delivery state")
    atomic(board_file.parent / QUEUE_FILE, queue)


def _normalize_claims(entry: dict, now: float) -> dict:
    claims = entry.setdefault("claims", {})
    for event_id, claim in list(claims.items()):
        if not isinstance(claim, dict):
            claims[event_id] = {"status": "uncertain", "at": now,
                                "lastError": "corrupt claim record"}
            continue
        if claim.get("status") == "inflight":
            at = claim.get("at")
            if isinstance(at, bool) or not isinstance(at, (int, float)) \
                    or now - at > QUEUE_STALE_INFLIGHT_SECONDS:
                claim["status"] = "uncertain"
                claim["at"] = now
                claim["lastError"] = ("inflight claim expired without a confirmed queue result; "
                                      "explicit rearm required")
    return claims


def _claim_queue(board_file, task_id: str, event_ids, now: float, packet_id: str):
    """Nonblocking claim of the exact events included in one packet.

    Returns the claimed ids, ``[]`` when nothing was claimable, or ``None`` when
    the short lock was held (the caller must retry on a later tick).
    """
    path, lock = queue_paths(board_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = lock_fd(lock, blocking=False)
    except (LockHeld, OSError):
        return None
    try:
        queue, problem = read_queue(board_file)
        if queue is None:
            return None
        entry = _queue_entry(queue, task_id)
        claims = _normalize_claims(entry, now)
        claimed = []
        for event_id in event_ids:
            claim = claims.get(event_id)
            previous_attempts = int(claim.get("attempts") or 0) if isinstance(claim, dict) else 0
            if isinstance(claim, dict):
                status = claim.get("status")
                if status in ("queued", "uncertain", "inflight"):
                    continue
                if status == "failed" and previous_attempts >= MAX_TRANSPORT_RETRIES:
                    continue
            claims[event_id] = {"status": "inflight", "at": now,
                                "attempts": previous_attempts + 1, "packetId": packet_id}
            claimed.append(event_id)
        entry["updatedAt"] = now
        _write_queue(board_file, queue)
        return claimed
    finally:
        os.close(fd)


def _finish_queue(board_file, task_id: str, event_ids, result: dict, now: float,
                  packet_id: str) -> None:
    """Finalize a queue result only while this packet still owns each claim."""
    try:
        fd = lock_fd(queue_paths(board_file)[1], blocking=True, timeout=5)
    except (LockHeld, OSError):
        return
    try:
        queue, problem = read_queue(board_file)
        if queue is None:
            return
        entry = _queue_entry(queue, task_id)
        claims = entry.setdefault("claims", {})
        status = result.get("status", "uncertain")
        finalized = []
        skipped = []
        for event_id in event_ids:
            claim = claims.get(event_id)
            if not isinstance(claim, dict) or claim.get("packetId") != packet_id \
                    or claim.get("status") != "inflight":
                # A recovery/rearm or newer packet owns this event now; the late
                # result must never overwrite the newer claim.
                skipped.append(event_id)
                continue
            claim.update({"status": status, "at": now,
                          "lastError": _text(result.get("error"), 300) if result.get("error")
                                       else None,
                          "exitCode": result.get("exitCode"),
                          "notStarted": bool(result.get("notStarted"))})
            claims[event_id] = claim
            finalized.append(event_id)
        if finalized:
            entry["lastStatus"] = status
            entry["lastDispatch"] = {
                "at": now, "packetId": packet_id, "status": status,
                "eventIds": [event_id for event_id in finalized if isinstance(event_id, str)],
                "exitCode": result.get("exitCode"), "timedOut": bool(result.get("timedOut")),
                "notStarted": bool(result.get("notStarted")),
                "outputSha256": result.get("outputSha256"),
                "outputExcerpt": result.get("outputExcerpt"),
                "argv0": result.get("argv0"),
            }
            if status in ("failed", "uncertain"):
                entry.setdefault("failures", []).append({
                    "at": now, "status": status, "packetId": packet_id,
                    "exitCode": result.get("exitCode"),
                    "error": _text(result.get("error") or status, 300),
                    "eventIds": [event_id for event_id in finalized
                                 if isinstance(event_id, str)][:10]})
                entry["failures"] = entry["failures"][-MAX_QUEUE_FAILURES:]
        if skipped:
            entry["lastSkippedFinalize"] = {
                "at": now, "packetId": packet_id,
                "eventIds": [event_id for event_id in skipped if isinstance(event_id, str)][:10],
                "note": "late queue result did not own the current claim; newer state was preserved"}
        entry["updatedAt"] = now
        _write_queue(board_file, queue)
    finally:
        os.close(fd)


def _clear_queue_claims(board_file, task_id: str, event_id=None, now=None) -> dict:
    """Explicit recovery rearm. Live inflight claims are never cleared; stale
    inflight becomes uncertain and needs a further explicit recovery call.
    """
    now = time.time() if now is None else now
    try:
        fd = lock_fd(queue_paths(board_file)[1], blocking=True, timeout=10)
    except (LockHeld, OSError) as exc:
        raise ValueError(f"queue lock unavailable: {exc}") from None
    try:
        queue, problem = read_queue(board_file)
        if queue is None:
            raise ValueError(f"queue state is {problem}")
        entry = _queue_entry(queue, task_id)
        claims = entry.setdefault("claims", {})
        cleared, refused, marked_uncertain = [], [], []
        targets = [event_id] if event_id is not None else list(claims)
        for target in targets:
            claim = claims.get(target)
            if claim is None:
                continue
            if not isinstance(claim, dict):
                claims[target] = {"status": "uncertain", "at": now,
                                  "lastError": "corrupt claim record"}
                marked_uncertain.append(target)
                continue
            if claim.get("status") == "inflight":
                at = claim.get("at")
                if isinstance(at, bool) or not isinstance(at, (int, float)) \
                        or now - at > QUEUE_STALE_INFLIGHT_SECONDS:
                    claim.update(status="uncertain", at=now,
                                 lastError="stale inflight claim; explicit recovery required "
                                           "before requeue")
                    marked_uncertain.append(target)
                else:
                    refused.append(target)
                continue
            claims.pop(target, None)
            cleared.append(target)
        if event_id is not None and event_id in refused:
            raise ValueError("refusing to clear a live inflight claim; wait for the result or for "
                             "the claim to age into uncertain, then rearm explicitly")
        if cleared:
            entry["lastStatus"] = "rearmed"
        entry["updatedAt"] = now
        _write_queue(board_file, queue)
        return {"ok": not refused, "taskId": task_id, "clearedClaims": len(cleared),
                "refusedInflight": refused, "markedUncertain": marked_uncertain,
                "eventId": event_id,
                "note": "live inflight claims are never cleared; stale inflight becomes uncertain "
                        "and needs a further explicit recovery; recovery may duplicate an "
                        "uncertain delivery and is never exactly-once"}
    finally:
        os.close(fd)


def queue_view(board_file, task_id: str) -> dict:
    queue, problem = read_queue(board_file)
    if queue is None:
        return {"status": f"unknown ({problem})", "limitations": [QUEUE_LIMITATION]}
    entry = (queue.get("tasks") or {}).get(task_id)
    if not isinstance(entry, dict):
        return {"status": "idle", "queued": 0, "inflight": 0, "uncertain": 0, "failed": 0,
                "lastDispatch": None, "failures": [], "limitations": [QUEUE_LIMITATION]}
    claims = entry.get("claims") if isinstance(entry.get("claims"), dict) else {}
    counts = {"queued": 0, "inflight": 0, "uncertain": 0, "failed": 0}
    now = time.time()
    for claim in claims.values():
        if not isinstance(claim, dict):
            continue
        status = claim.get("status")
        if status == "inflight":
            at = claim.get("at")
            if isinstance(at, bool) or not isinstance(at, (int, float)) \
                    or now - at > QUEUE_STALE_INFLIGHT_SECONDS:
                status = "uncertain"  # a crashed in-flight claim is never auto-resent
        if status in counts:
            counts[status] += 1
    return {
        "status": entry.get("lastStatus") or "idle",
        "queued": counts["queued"], "inflight": counts["inflight"],
        "uncertain": counts["uncertain"], "failed": counts["failed"],
        "totalClaims": len(claims),
        "lastDispatch": entry.get("lastDispatch"),
        "failures": (entry.get("failures") or [])[-3:],
        "limitations": [QUEUE_LIMITATION],
    }


# ---------------------------------------------------------------------------
# dispatch (CLI queue transport)
# ---------------------------------------------------------------------------

def _read_bounded_stream(stream, limit: int) -> bytes:
    """Read at most ``limit`` bytes from a child pipe, draining the rest."""
    data = bytearray()
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            if len(data) < limit:
                data.extend(chunk[: limit - len(data)])
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass
    return bytes(data)


def _packet_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _run_queue_cli(argv, timeout: float) -> dict:
    """Run the exact queue argv with shell=False, bounded live capture and cleanup.

    Only a proven not-started child (Popen OSError) is retryable. Any spawned
    child that times out, is interrupted, dies or exits nonzero is uncertain:
    it may already have enqueued the message and is never auto-resent.
    """
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return {"status": "failed", "notStarted": True, "exitCode": None, "timedOut": False,
                "error": f"queue command could not start: {exc}"}
    results = {}

    def reader(stream, key):
        results[key] = _read_bounded_stream(stream, MAX_CLI_OUTPUT_BYTES)

    readers = [threading.Thread(target=reader, args=(child.stdout, "stdout"), daemon=True),
               threading.Thread(target=reader, args=(child.stderr, "stderr"), daemon=True)]
    for thread in readers:
        thread.start()
    timed_out = False
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate(child)
    except KeyboardInterrupt:
        timed_out = True
        terminate(child)
    for thread in readers:
        thread.join(timeout=2.0)
    output = (results.get("stdout", b"") + results.get("stderr", b""))[:MAX_CLI_OUTPUT_BYTES]
    result = {"exitCode": child.returncode, "timedOut": timed_out,
              "outputSha256": hashlib.sha256(output).hexdigest(),
              "outputExcerpt": output.decode("utf-8", errors="replace")[:1000],
              "argv0": argv[0]}
    if timed_out:
        result.update(status="uncertain",
                      error="queue command timed out or was interrupted after being spawned; "
                            "delivery is uncertain and requires explicit rearm")
    elif child.returncode == 0:
        result["status"] = "queued"
    else:
        # A spawned process that exits nonzero may still have enqueued before
        # failing; it is ambiguous, never a retryable not-sent result.
        result.update(status="uncertain",
                      error=f"queue command exited {child.returncode} after being spawned; "
                            "delivery is uncertain and requires explicit rearm")
    return result


def _resolve_codex_bin(value) -> str:
    raw = str(value)
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() or os.sep in raw:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise ValueError(f"codex executable is not an executable file: {candidate}")
        return str(candidate.resolve())
    found = shutil.which(raw)
    if not found:
        raise ValueError(f"codex executable {raw!r} was not found on PATH; pass --codex-bin")
    return str(Path(found).resolve())


def _show_hint(card: dict) -> str:
    return (f'python3 {shlex.quote(str(BOARD_CLI))} show --repo '
            f'{shlex.quote(str(card.get("repo")))} --task {shlex.quote(str(card.get("taskId")))}')


def _drain_reference(card: dict) -> str:
    return "drain the board in this same turn: " + _show_hint(card)


def _clip_bytes(text: str, limit: int) -> str:
    """Whole-character UTF-8 prefix of at most ``limit`` bytes, with an ellipsis if cut."""
    text = " ".join(str(text).split())
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    if limit <= 3:
        return ""
    return raw[: limit - 3].decode("utf-8", "ignore") + "..."


def _final_report(event: dict) -> str:
    """Pi's final report for the event's round, from the stored round summary."""
    state_ref = (event.get("evidence") or {}).get("stateRef")
    if not isinstance(state_ref, str) or not state_ref:
        return ""
    data, _problem = _read_bounded_json(Path(state_ref).parent / "round.summary.json",
                                        4_000_000)
    text = data.get("final_text") if isinstance(data, dict) else None
    return text if isinstance(text, str) else ""


def _event_policy(card: dict, event: dict):
    policy = (event.get("evidence") or {}).get("reviewPolicy")
    return policy if isinstance(policy, dict) else review_policy(card)


def _event_block(card: dict, event: dict) -> list:
    """Compact delivery-card lines for one event (no absolute evidence paths)."""
    head = (event.get("candidate") or {}).get("head") or "unknown"
    lines = [f"{event.get('kind')} round={event.get('round')} event={event.get('id')} "
             f"head={head}"]
    if event.get("phaseId"):
        lines.append(f"phase={event.get('phaseId')} contract={event.get('contractHash')}")
    delivery = event.get("delivery") if isinstance(event.get("delivery"), dict) else {}
    for item in delivery.get("items") or []:
        if isinstance(item, dict):
            lines.append(acceptance_line(item.get("id") or "?", item.get("st"),
                                         item.get("exit"), item.get("counts")))
    if delivery.get("more"):
        lines.append(f"+{delivery['more']} more items (show)")
    policy = _event_policy(card, event)
    if isinstance(policy, dict) and policy.get("limit") is not None:
        lines.append(f"policy={policy.get('failedDeliveries')}/{policy.get('limit')} "
                     f"{policy.get('implementationOwner')}")
    if event.get("kind") not in REVIEW_KINDS:
        lines.append("note=" + _clip_bytes(event.get("summary") or "", 160))
        if event.get("kind") == PROGRESS_EVENT_KIND or event.get("kind") == "phase_blocked":
            lines.append("ask=" + _clip_bytes(event.get("question") or "", 220))
    return lines


def build_packet(card: dict, events, limit: int = MAX_PACKET_EVENTS):
    """Build the bounded delivery card; return ``(text, included_events)``.

    The limit is measured in UTF-8 bytes (``MAX_PACKET_CHARS``, 1200). It holds
    task, round, event id, kind, the full candidate SHA, one line per acceptance
    item, the review policy as ``failed/limit owner``, Pi's final report
    (truncated to whatever room remains) and one decide hint. Room for the
    mandatory board-drain instruction is reserved *before* filling event
    blocks, so a near-full packet can never silently omit it. An event too large
    for the normal layout still produces a bounded fallback carrying its exact
    id and a board evidence reference. Absolute evidence paths stay out of the
    card; ``show --task`` gives them on demand.
    """
    header = ["codex-pi cli-queue handoff; not a user goal. Queue exit 0 is not acceptance."]
    lines = [f"task={card.get('taskId')}"]
    overflow = card.get("overflow")
    if isinstance(overflow, dict) and overflow.get("active"):
        lines.append(f"overflow={overflow.get('pendingCount')} pending; drain in this same turn: "
                     f"{_show_hint(card)}")
    drain_reference = _drain_reference(card)
    # Reserve the longest drain prefix ("+NNN pending event(s); ") plus one byte
    # before filling any event block.
    reserve = _packet_bytes(drain_reference) + 24
    event_budget = max(0, MAX_PACKET_CHARS - reserve)
    included = []
    for event in events:
        if len(included) >= limit:
            break
        block = _event_block(card, event)
        anchor = included[0] if included else event
        probe = "decide=" + _decide_hint(card.get("repo"), card.get("taskId"), anchor) \
            + (" (same form for the other event ids above)" if included else "")
        if _packet_bytes("\n".join(header + lines + block + [probe])) > event_budget:
            break
        lines.extend(block)
        included.append(event)
    remaining = len(events) - len(included)
    if not included and events:
        event = events[0]
        board_ref = str(Path(str(card.get("commonDir") or card.get("repo") or ""))
                        / BOARD_DIR / BOARD_FILE)
        pieces = [
            f"event={event.get('id')} kind={event.get('kind')} round={event.get('round')}",
            f"board_ref={board_ref}",
        ]
        if remaining > 1:
            pieces.append(f"+{remaining - 1} pending event(s); {drain_reference}")
        pieces.extend([
            "oversized packet fallback; read this board evidence in the same turn: "
            + _show_hint(card),
            f"task={card.get('taskId')}",
            f"summary={_text(event.get('summary'), 200)}",
        ])
        text = "\n".join(pieces)
        if _packet_bytes(text) > MAX_PACKET_CHARS:
            # Even under a tiny cap keep the exact event id and a board ref.
            minimal = f"event={event.get('id')} board_ref={board_ref}"
            encoded = minimal.encode("utf-8")
            text = encoded[:MAX_PACKET_CHARS].decode("utf-8", "ignore") \
                if len(encoded) > MAX_PACKET_CHARS else minimal
        return text, [event]
    # One hint per card: the first event's decide command; the other included
    # events use the same form with their own event ids.
    hint = "decide=" + _decide_hint(card.get("repo"), card.get("taskId"), included[0])
    if len(included) > 1:
        hint += " (same form for the other event ids above)"
    tail = f"+{remaining} pending event(s); {drain_reference}" if remaining > 0 else None
    text = "\n".join(header + lines + [hint] + ([tail] if tail else []))
    # Pi's final report takes whatever room is left, on the first included event.
    report = _final_report(included[0])
    if report:
        room = MAX_PACKET_CHARS - _packet_bytes(text) - len("\nreport=")
        clipped = _clip_bytes(report, room) if room >= 24 else ""
        if clipped:
            index = text.find("\ndecide=")
            text = text[:index] + "\nreport=" + clipped + text[index:]
    return text, included


def dispatch_task(board_file, task_id: str, now=None, timeout=None, cli_runner=None) -> dict:
    """One bounded queue dispatch; never raises for transport failure."""
    now = time.time() if now is None else now
    timeout = DISPATCH_TIMEOUT_SECONDS if timeout is None else float(timeout)
    board, problem = read_board(board_file)
    if board is None:
        return {"ok": False, "dispatched": False, "status": "unknown",
                "error": f"board state is {problem}: {board_file}"}
    card = (board.get("cards") or {}).get(task_id)
    if not isinstance(card, dict):
        return {"ok": True, "dispatched": False, "reason": "task is not registered"}
    if card.get("transport") != TRANSPORT_CLI_QUEUE:
        return {"ok": True, "dispatched": False, "reason": "transport is not cli-queue"}
    if card.get("paused"):
        return {"ok": True, "dispatched": False, "reason": "task paused"}
    thread = card.get("ownerThread")
    if not isinstance(thread, str) or not THREAD_RE.fullmatch(thread):
        return {"ok": False, "dispatched": False, "status": "invalid-owner",
                "error": "card has no exact owner thread UUID; refusing to guess"}
    paused, pause_problem = route_paused(thread)
    if pause_problem is not None:
        return {"ok": False, "dispatched": False, "status": "unknown",
                "error": f"route pause state is {pause_problem}"}
    if paused:
        return {"ok": True, "dispatched": False, "reason": "session paused by interrupt"}
    queue, qproblem = read_queue(board_file)
    if queue is None:
        return {"ok": False, "dispatched": False, "status": "unknown",
                "error": f"queue state is {qproblem}: {board_file}"}
    entry = _queue_entry(queue, task_id)
    claims = _normalize_claims(entry, now)
    eligible = []
    for event in pending_events(card):
        if event.get("phaseId") and _phase_event_stale(card, event):
            # Mechanical staleness: never dispatch evidence for an older phase/candidate.
            continue
        claim = claims.get(event.get("id"))
        if not isinstance(claim, dict):
            eligible.append(event)
            continue
        status = claim.get("status")
        if status == "failed" and int(claim.get("attempts") or 0) < MAX_TRANSPORT_RETRIES:
            eligible.append(event)
        # queued/inflight/uncertain/failed-final are not automatically resent
    if not eligible:
        return {"ok": True, "dispatched": False, "reason": "no dispatchable events",
                "queue": queue_view(board_file, task_id)}
    text, included = build_packet(card, eligible)
    if not included:
        return {"ok": True, "dispatched": False, "reason": "packet did not fit"}
    packet_id = uuid.uuid4().hex
    requested = [event["id"] for event in included]
    claimed = _claim_queue(board_file, task_id, requested, now, packet_id)
    if claimed is None:
        return {"ok": True, "dispatched": False, "reason": "queue claim lock held; retry next tick"}
    if not claimed:
        return {"ok": True, "dispatched": False, "reason": "events already claimed or queued"}
    if set(claimed) != set(requested):
        included = [event for event in included if event["id"] in claimed]
        text, included = build_packet(card, included)
        if not included:
            return {"ok": True, "dispatched": False, "reason": "claim changed; retry next tick"}
    argv = [card.get("codexBin") or DEFAULT_CODEX_BIN, "--disable", "daemon_auto_start", "queue",
            "--thread", thread, "--message", text]
    try:
        argv[0] = _resolve_codex_bin(argv[0])
    except ValueError as exc:
        return {"ok": False, "dispatched": False, "status": "invalid-binary", "error": str(exc)}
    result = (cli_runner or _run_queue_cli)(argv, timeout)
    if result.get("status") in ("queued", "uncertain"):
        # Size only: the card may have reached the owner (uncertain included).
        record_codex_io(Path(board_file).parent / "tasks" / task_id, "queue",
                        _packet_bytes(text), kind="packet")
    _finish_queue(board_file, task_id, [event["id"] for event in included], result, now, packet_id)
    status = result.get("status", "failed")
    return {"ok": status == "queued", "dispatched": True, "taskId": task_id,
            "thread": thread, "packetId": packet_id, "status": status,
            "eventIds": [event["id"] for event in included],
            "exitCode": result.get("exitCode"), "timedOut": bool(result.get("timedOut")),
            "error": result.get("error"),
            "receipt": {"argv0": result.get("argv0"), "exitCode": result.get("exitCode"),
                        "timedOut": bool(result.get("timedOut")),
                        "outputSha256": result.get("outputSha256"),
                        "outputExcerpt": result.get("outputExcerpt")},
            "queue": queue_view(board_file, task_id),
            "limitations": [QUEUE_LIMITATION]}
