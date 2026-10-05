#!/usr/bin/env python3
"""Short Codex hooks for the Codex-Pi cli-queue route (0.8.8).

Delivery of Pi results is done by the board queue (``pi_board.py``); there is no
Stop-hook delivery any more. The hooks that remain are deliberately small:

* ``Interrupt`` persists a route pause for the interrupted session. Only an
  explicit ``pi_board.py resume`` clears it; ordinary prompts never do.
* ``SessionStart`` / ``UserPromptSubmit`` return bounded read-only presence as
  ``hookSpecificOutput.additionalContext``: work awaiting a main decision, the
  phase pins of every task on this session, and cli-queue transport evidence.
* ``PreCompact`` returns the same pins so an automatic compaction keeps the
  contract hash and candidate head instead of losing them.
* ``SessionEnd`` prepares the outcome closeout for this session and reports the
  path; it never files a record and never implies acceptance.

This module never invokes the Codex CLI and never starts a model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

MAX_EVENT_BYTES = 1_000_000
MAX_MESSAGE_BYTES = 900


def bounded(text, limit: int = MAX_MESSAGE_BYTES) -> str:
    text = str(text).replace("\x00", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _queue_recovery_lines(session: str) -> list:
    """Bounded cli-queue/monitor recovery evidence for one routed thread."""
    try:
        from pi_board import route_summary
        summary = route_summary(session)
    except Exception:  # noqa: BLE001 - recovery must never crash the hook
        return []
    if not summary:
        return []
    lines = []
    if summary.get("paused"):
        lines.append("route pause is active after an interrupt; explicit resume is required "
                     "(nothing resumes automatically)")
    lines.extend(summary.get("lines") or [])
    return [bounded(line, 400) for line in lines[:10]]


def handle_presence(event_name: str, session: str) -> dict:
    """Presence digest for a boundary event; silent when this session has no Pi work."""
    try:
        from pi_presence import compact_digest, render_context
        digest = compact_digest(session)
    except Exception:  # noqa: BLE001 - presence must never break a session
        return {}
    if digest.get("problem") in ("missing", "not-routed") or not digest.get("lines"):
        return {}
    text, _truncated = render_context(digest)
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}


def handle_compaction(session: str) -> dict:
    """Carry the pins into a compaction summary; never blocks compaction."""
    return handle_presence("PreCompact", session)


def handle_closeout(session: str, cwd=None) -> dict:
    """Prepare the closeout for this session. Preparation only, never filing."""
    repo = Path(cwd) if isinstance(cwd, str) and cwd.strip() else None
    if repo is None:
        return {"systemMessage": bounded("codex-pi closeout skipped: the hook carried no "
                                        "repository path; run pi_board.py closeout --repo <path> "
                                        "--session <uuid>")}
    try:
        from pi_presence import prepare_closeout
        result = prepare_closeout(session, repo=repo)
    except Exception as exc:  # noqa: BLE001 - a broken digest can never block a session end
        return {"systemMessage": bounded(f"codex-pi closeout preparation failed safely: {exc}")}
    if not result.get("ok"):
        return {"systemMessage": bounded(f"codex-pi closeout not prepared: {result.get('problem')}; "
                                        "never acceptance")}
    return {"systemMessage": bounded(f"codex-pi closeout prepared: {result['path']}; "
                                    f"outcome id {result['outcomeId']}; not acceptance")}


def handle_interrupt(session: str) -> dict:
    try:
        from pi_store import pause_route
        pause_route(session, "user interrupted this Codex session")
    except Exception:  # noqa: BLE001 - the hook must never crash on this
        pass
    return {}


def validate_session_id(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("session id must be a non-empty string")
    value = value.strip()
    if len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("session id must be at most 200 printable characters")
    return value


def dispatch_hook(event: dict) -> dict:
    name = event.get("hook_event_name")
    session = validate_session_id(event.get("session_id"))
    if name == "Interrupt":
        return handle_interrupt(session)
    if name in ("SessionStart", "UserPromptSubmit"):
        return handle_presence(name, session)
    if name == "PreCompact":
        return handle_compaction(session)
    if name == "SessionEnd":
        return handle_closeout(session, event.get("cwd"))
    return {}


def cmd_hook(_args) -> int:
    raw = sys.stdin.read(MAX_EVENT_BYTES)
    try:
        event = json.loads(raw)
    except ValueError:
        print(json.dumps({"systemMessage": "codex-pi hook: input was not valid JSON"}))
        return 0
    if not isinstance(event, dict):
        print(json.dumps({"systemMessage": "codex-pi hook: input was not a JSON object"}))
        return 0
    try:
        output = dispatch_hook(event)
    except Exception as exc:  # never crash the host hook; fail safe and bounded
        output = {"systemMessage": bounded(f"codex-pi hook failed safely: {exc}")}
    print(json.dumps(output, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi_handoff.py",
        description="Codex hook entry for the Codex-Pi cli-queue route (Interrupt pause, "
                    "SessionStart/UserPromptSubmit presence, PreCompact pins, SessionEnd "
                    "closeout). Never invokes the Codex CLI or a model.")
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook", help="read one hook event from stdin")
    hook.set_defaults(func=cmd_hook)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
