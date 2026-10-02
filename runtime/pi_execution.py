"""Pi terminal evidence, independent of the process exit code.

Inspect a bounded tail, never a tool's isError flag or an earlier assistant answer.
The original stream and state are not rewritten when inspecting a frozen round.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MAX_TERMINAL_BYTES = 1_048_576
# Pi's terminal agent_end can repeat the entire transcript in one JSONL record.
# Keep the evidence window fixed, but permit one bounded aggregate record so it
# cannot evict the preceding message_end/turn_end evidence from that window.
MAX_AGGREGATE_EVENT_BYTES = 2 * MAX_TERMINAL_BYTES


def _terminal_lines(stream, size: int):
    """Read a bounded tail of complete JSONL events, newest first.

    One oversized record is tolerated only when it is a complete agent_end
    aggregate. Other oversized or malformed terminal records remain explicit
    invalid evidence and therefore cannot make an earlier assistant answer pass.
    """
    cursor = size
    selected = []
    evidence_bytes = 0
    while cursor > 0 and evidence_bytes < MAX_TERMINAL_BYTES:
        line_end = cursor
        stream.seek(line_end - 1)
        if stream.read(1) == b"\n":
            line_end -= 1
        if line_end <= 0:
            break

        # Find this line's start with bounded look-behind. A record beyond the
        # aggregate cap is not inspected or trusted.
        scan_start = max(0, line_end - MAX_AGGREGATE_EVENT_BYTES - 1)
        stream.seek(scan_start)
        preceding = stream.read(line_end - scan_start)
        newline = preceding.rfind(b"\n")
        if newline < 0 and scan_start > 0:
            selected.append((None, b"", True, scan_start))
            break
        line_start = scan_start + newline + 1 if newline >= 0 else 0
        line_size = line_end - line_start
        if line_size <= 0:
            cursor = line_start
            continue

        stream.seek(line_start)
        raw = stream.read(line_size)
        invalid = False
        event = None
        if line_size > MAX_TERMINAL_BYTES:
            # The 1 MiB evidence window remains unchanged. Only Pi's duplicate
            # terminal envelope may exceed it, and only up to a fixed bound.
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict) or parsed.get("type") != "agent_end":
                    invalid = True
                else:
                    event = parsed
            except (ValueError, UnicodeDecodeError):
                invalid = True
            if invalid:
                selected.append((None, raw, True, line_start))
                break
        else:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("not an object")
                event = parsed
            except (ValueError, UnicodeDecodeError):
                invalid = True
        selected.append((event, raw, invalid, line_start))
        if line_size <= MAX_TERMINAL_BYTES:
            evidence_bytes += line_size
        cursor = line_start
    selected.reverse()
    return selected


def terminal_evidence(log: Path) -> dict:
    try:
        with Path(log).open("rb") as stream:
            size = stream.seek(0, 2)
            selected = _terminal_lines(stream, size)
    except OSError as exc:
        return {"status": "unknown", "reason": "stream_unreadable", "error": str(exc)[:300]}
    offset = selected[0][3] if selected else size
    evidence = {"sourceBytes": size, "tailOffset": offset,
                "tailSha256": hashlib.sha256(b"".join(row[1] for row in selected)).hexdigest()}
    message, message_line, invalid_line = None, -1, -1
    activity_line,error_line,stream_error=-1,-1,None
    for index, (event, _raw, invalid, _line_start) in enumerate(selected):
        if invalid:
            invalid_line = index
            continue
        if event is None:
            invalid_line = index
            continue
        kind=event.get('type')
        if kind in ('agent_start','turn_start','tool_execution_start','tool_execution_end'):
            activity_line=index
        if kind in ('error','fatal_error'):
            error_line=index;stream_error=str(event.get('error') or event.get('message') or kind)[:300]
        if kind in ("message_end",'turn_end'):
            value = event.get("message")
            if isinstance(value, dict) and value.get("role") == "assistant":
                message, message_line = value, index
        elif event.get("type") == "agent_end":
            # Pi's agent_end may be the only completed-message envelope.
            messages = event.get("messages")
            if isinstance(messages, list):
                assistants = [m for m in messages if isinstance(m, dict)
                              and m.get("role") == "assistant"]
                if assistants:
                    message, message_line = assistants[-1], index
    if error_line>message_line:
        return {**evidence,'status':'failed','reason':'stream_error','error':stream_error,'finalText':''}
    if message is None or invalid_line > message_line or activity_line>message_line:
        return {**evidence, "status": "unknown", "reason": "terminal_message_unverified",
                "error": "No complete final assistant message in the bounded stream tail"}
    stop = message.get("stopReason")
    error = message.get("errorMessage")
    text = "\n".join(p["text"] for p in message.get("content") or []
                     if isinstance(p, dict) and p.get("type") == "text"
                     and isinstance(p.get("text"), str))
    if stop in ("error", "aborted") or error:
        return {**evidence, "status": "failed", "reason": "provider_error" if stop == "error"
                or error else "assistant_aborted", "stopReason": stop,
                "error": str(error or stop)[:300], "finalText": ""}
    if stop != "stop":
        return {**evidence, "status": "failed", "reason": "incomplete_assistant",
                "stopReason": stop, "error": f"Last assistant stopReason is {stop!r}",
                "finalText": ""}
    return {**evidence, "status": "completed", "reason": None,
            "stopReason": stop, "error": None, "finalText": text[:1200]}


def effective_execution(state: dict, log: Path) -> tuple[str | None, dict | None]:
    recorded = state.get("state")
    # Timeout, cancellation, ownership and nonzero process failures retain priority.
    if recorded != "completed" or state.get("exitCode") != 0:
        return recorded, state.get("executionEvidence")
    evidence = terminal_evidence(log)
    return evidence["status"], evidence
