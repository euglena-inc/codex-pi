"""Pi terminal evidence, independent of the process exit code.

Inspect a bounded tail, never a tool's isError flag or an earlier assistant answer.
The original stream and state are not rewritten when inspecting a frozen round.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MAX_TERMINAL_BYTES = 1_048_576


def terminal_evidence(log: Path) -> dict:
    try:
        with Path(log).open("rb") as stream:
            size = stream.seek(0, 2)
            offset = max(0, size - MAX_TERMINAL_BYTES)
            stream.seek(offset)
            raw = stream.read(MAX_TERMINAL_BYTES)
    except OSError as exc:
        return {"status": "unknown", "reason": "stream_unreadable", "error": str(exc)[:300]}
    evidence = {"sourceBytes": size, "tailOffset": offset,
                "tailSha256": hashlib.sha256(raw).hexdigest()}
    if offset:
        # A tail starting mid-line cannot prove that line's terminal semantics.
        raw = raw.partition(b"\n")[2]
    message, message_line, invalid_line = None, -1, -1
    activity_line,error_line,stream_error=-1,-1,None
    for index, line in enumerate(raw.splitlines()):
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            invalid_line = index
            continue
        kind=event.get('type')
        if kind in ('agent_start','turn_start','tool_execution_start'):
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
