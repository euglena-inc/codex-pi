#!/usr/bin/env python3
"""Bounded hook presence and session closeout (0.8.8).

Two jobs, both read-only apart from the closeout files this module owns:

* ``presence_for_thread`` / ``compact_digest`` build the short digest a Codex hook returns as
  ``hookSpecificOutput.additionalContext``, so work awaiting a main decision and the current phase
  pins are present at a boundary instead of being remembered by the model.
* ``prepare_closeout`` writes the outcome closeout a session would otherwise have to remember, as a
  preparation that never files and never accepts.

Every value this module cannot read renders as ``unknown``; unknown is never replaced by zero.
Nothing here invokes the Codex CLI, starts a model or a daemon, or changes decisions, ownership or
hook trust. Design: ``docs/design/hook-presence-and-closeout.md``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parent

MAX_CONTEXT_BYTES = 1_600
MAX_TASKS = 6
MAX_EVENTS = 6
MAX_STORE_BYTES = 8_000_000
MAX_CLOSEOUT_BYTES = 64_000
AWAITING_KINDS = ("review_required", "phase_blocked", "codex_takeover_required")
HEADER = "Codex-Pi presence (read-only):"
FOOTER = "read-only presence; never acceptance; unknown is not zero"
ID_RE = re.compile(r"[A-Za-z0-9._-]+")


def _text(value, limit: int = 160) -> str:
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _hex12(value) -> str:
    text = str(value or "").strip().lower()
    if len(text) >= 12 and re.fullmatch(r"[0-9a-f]+", text[:12]):
        return text[:12]
    return "unknown"


def _safe_id(value: str, limit: int = 80) -> str:
    text = _text(value, 200)
    cleaned = "".join(char if (char.isalnum() or char in "._-") else "-" for char in text)
    cleaned = cleaned.strip("-.") or "session"
    return cleaned[:limit]


# ---------------------------------------------------------------------------
# bounded reads
# ---------------------------------------------------------------------------

def _read_board(board_file: Path):
    try:
        from pi_store import read_board
        board, problem = read_board(Path(board_file))
    except Exception as exc:  # noqa: BLE001 - a hook must never crash on a store read
        return None, f"unreadable ({type(exc).__name__})"
    if board is None:
        return None, str(problem or "unreadable")
    return board, None


def _cards_for_thread(board: dict, thread: str) -> list:
    cards = board.get("cards") or {}
    selected = [card for card in cards.values()
                if isinstance(card, dict) and str(card.get("ownerThread")) == str(thread)]
    return sorted(selected, key=lambda card: str(card.get("taskId")))


# ---------------------------------------------------------------------------
# digest
# ---------------------------------------------------------------------------

def task_pin(card: dict) -> str:
    """One bounded pin line for a task: state, phase identity and ownership facts."""
    pi = card.get("pi") if isinstance(card.get("pi"), dict) else {}
    phase = card.get("phase") if isinstance(card.get("phase"), dict) else {}
    round_value = pi.get("round")
    round_text = str(round_value) if isinstance(round_value, int) and not isinstance(
        round_value, bool) else "unknown"
    owner = takeover = failed = "unknown"
    try:
        from pi_takeover import review_policy
        policy = review_policy(card)
        owner = _text(policy.get("implementationOwner"), 20) or "unknown"
        takeover = str(bool(policy.get("takeoverRequired")))
        count = policy.get("failedDeliveries")
        failed = str(count) if isinstance(count, int) and not isinstance(count, bool) else "unknown"
    except Exception:  # noqa: BLE001 - an unavailable policy stays unknown, never zero
        pass
    pending = len([event for event in card.get("events", [])
                   if isinstance(event, dict) and not event.get("handled")])
    return ("task={task} state={state} round={round} phase={phase} contract={contract} "
            "candidate={candidate} owner={owner} takeover={takeover} failedDeliveries={failed} "
            "pending={pending}").format(
        task=_safe_id(str(card.get("taskId")), 60), state=_text(pi.get("state"), 24) or "unknown",
        round=round_text, phase=_safe_id(str(phase.get("phaseId") or "unknown"), 40),
        contract=_hex12(phase.get("contractHash")),
        candidate=_hex12(phase.get("candidateHead") or phase.get("candidate")),
        owner=owner, takeover=takeover, failed=failed, pending=pending)


def _awaiting_lines(cards: list) -> tuple:
    lines, hidden = [], 0
    for card in cards:
        for event in card.get("events", []):
            if not isinstance(event, dict) or event.get("handled"):
                continue
            if event.get("kind") not in AWAITING_KINDS:
                continue
            if len(lines) >= MAX_EVENTS:
                hidden += 1
                continue
            candidate = event.get("candidate") if isinstance(event.get("candidate"), dict) else {}
            lines.append("awaiting-main decision: task={} event={} round={} head={} phase={}".format(
                _safe_id(str(card.get("taskId")), 60), _text(event.get("kind"), 32),
                event.get("round") if event.get("round") is not None else "unknown",
                _hex12(candidate.get("head")),
                _safe_id(str(event.get("phaseId") or "unknown"), 40)))
    return lines, hidden


def _transport_lines(thread: str) -> list:
    try:
        from pi_board import route_summary
        summary = route_summary(thread)
    except Exception:  # noqa: BLE001 - transport evidence is additive only
        return []
    if not isinstance(summary, dict):
        return []
    lines = [_text(line, 300) for line in (summary.get("lines") or [])][:MAX_EVENTS]
    if summary.get("paused"):
        lines = [line for line in lines if "route pause" not in line]
        lines.insert(0, "route pause is active after an interrupt; explicit resume is required")
    return lines


def _digest(cards, problem, truncated) -> dict:
    return {"lines": [line for line in cards if line], "problem": problem,
            "truncated": bool(truncated)}


def presence_for_thread(thread: str, board_file=None) -> dict:
    """Bounded presence for one owner thread; a session with no route reports nothing invented."""
    routed = True
    if board_file is None:
        try:
            from pi_store import read_route
            route, problem = read_route(thread)
        except Exception as exc:  # noqa: BLE001
            return {"lines": [], "truncated": False, "problem": f"route is unreadable ({type(exc).__name__})"}
        if route is None:
            return {"lines": [], "truncated": False, "problem": str(problem or "missing")}
        board_file = route.get("boardPath")
        routed = bool(board_file)
    if not routed or not board_file:
        return {"lines": [], "truncated": False, "problem": "not-routed"}
    board, problem = _read_board(Path(board_file))
    if board is None:
        return {"lines": [f"board state is {problem}"], "truncated": False, "problem": problem}
    cards = _cards_for_thread(board, thread)
    awaiting, hidden = _awaiting_lines(cards)
    pins = [task_pin(card) for card in cards[:MAX_TASKS]]
    lines = awaiting + pins
    truncated = bool(hidden or len(cards) > MAX_TASKS)
    if hidden:
        lines.append(f"(+{hidden} more awaiting a decision)")
    if len(cards) > MAX_TASKS:
        lines.append(f"(+{len(cards) - MAX_TASKS} more tasks on this session)")
    return {"lines": lines, "truncated": truncated, "problem": None,
            "hidden": int(hidden) + max(0, len(cards) - MAX_TASKS)}


def compact_digest(thread: str, board_file=None) -> dict:
    """Presence plus transport recovery evidence, de-duplicated, for a compaction summary."""
    digest = presence_for_thread(thread, board_file=board_file)
    if digest.get("problem") in ("missing", "not-routed"):
        return digest
    seen = {line for line in digest["lines"]}
    extra = [line for line in _transport_lines(thread) if line not in seen
             and not any(line.startswith(prefix) for prefix in ("route pause",)
                         if any("route pause" in known for known in seen))]
    return {"lines": digest["lines"] + extra, "truncated": digest["truncated"],
            "problem": digest.get("problem"), "hidden": digest.get("hidden", 0)}


def render_context(digest: dict, header: str = HEADER, footer: str = FOOTER,
                   limit: int = MAX_CONTEXT_BYTES) -> tuple:
    """Render a digest inside a byte budget, always keeping the footer and a truncation marker."""
    lines = [line for line in (digest.get("lines") or []) if line]
    hidden = int(digest.get("hidden") or 0) if digest.get("truncated") else 0
    if digest.get("truncated") and not hidden:
        hidden = 1
    body = list(lines)
    dropped = 0
    prefix = f"{header}\n" if header else ""

    def render(items, dropped_count):
        marker = f"\u2026(+{dropped_count + hidden} more)" if (dropped_count or hidden) else ""
        parts = [prefix]
        parts.append("\n".join(items) if items else "nothing awaiting a decision")
        if marker:
            parts.append("\n" + marker)
        parts.append("\n" + footer)
        return "".join(parts)

    text = render(body, dropped)
    while len(text.encode("utf-8")) > limit:
        if body:
            body.pop()
            dropped += 1
            text = render(body, dropped)
        else:
            text = f"{prefix}\u2026(+{dropped + hidden} lines)\n{footer}"
            break
    return text, bool(dropped or hidden)


# ---------------------------------------------------------------------------
# session closeout
# ---------------------------------------------------------------------------

def _accepted_state(cards: list):
    """Only an exact accepted delivery decision can raise the prepared status."""
    for card in cards:
        policy_latched = ((card.get("codex") or {}).get("takeover") or {}).get("required")
        for event in reversed(card.get("events", []) or []):
            if not isinstance(event, dict) or not event.get("handled"):
                continue
            if event.get("kind") not in ("review_required", "phase_blocked"):
                continue
            if event.get("decision") != "accept":
                continue
            head = _hex12(event.get("reviewedHead"))
            if head == "unknown":
                candidate = event.get("candidate")
                head = _hex12(candidate.get("head") if isinstance(candidate, dict) else None)
            if head != "unknown" and not policy_latched:
                return "accepted", event.get("reviewedHead") or (event.get("candidate") or {}).get("head")
    return "unknown", None


def _member_rows(cards: list) -> list:
    rows = []
    for card in cards:
        rounds = sorted({event.get("round") for event in card.get("events", []) or []
                         if isinstance(event, dict) and isinstance(event.get("round"), int)
                         and not isinstance(event.get("round"), bool) and event.get("round") >= 1})
        rows.append({"task": str(card.get("taskId")), "rounds": rounds})
    return rows


def closeout_paths(repo, thread: str, task=None) -> dict:
    from pi_store import board_file_for_repo
    _root, common, board_file = board_file_for_repo(Path(repo))
    sessions = Path(common) / "codex-pi" / "sessions"
    safe = _safe_id(thread, 60)
    if task:
        safe = f"{safe}.{_safe_id(task, 40)}"
    return {"common": common, "boardFile": board_file,
            "path": sessions / f"{safe}.closeout.json",
            "recordPath": sessions / f"{safe}.closeout.record.json"}


def prepare_closeout(thread: str, board_file=None, repo=None, now=None,
                     apply: bool = False, outcome_id=None, task=None) -> dict:
    """Prepare (and only with ``apply`` file) the outcome observation for one session."""
    now = time.time() if now is None else now
    if repo is None:
        return {"ok": False, "problem": "no-repo", "filed": False, "changed": False}
    paths = closeout_paths(repo, thread, task=task)
    board_file = board_file if board_file is not None else paths["boardFile"]
    board, problem = _read_board(Path(board_file))
    if board is None:
        return {"ok": False, "problem": f"board state is {problem}", "filed": False,
                "changed": False}
    cards = _cards_for_thread(board, thread)
    if task:
        cards = [card for card in cards if str(card.get("taskId")) == str(task)]
    if not cards:
        return {"ok": False, "problem": "no-tasks", "filed": False, "changed": False}
    status, candidate = _accepted_state(cards)
    members = _member_rows(cards)
    refs = sorted({f"codex-pi/tasks/{row['task']}/task.json" for row in members})[:4]
    started = min([now] + [float(card.get("createdAt")) for card in cards
                           if isinstance(card.get("createdAt"), (int, float))])
    payload = {"status": status, "candidate": candidate, "members": members,
               "evidenceRefs": refs or ["codex-pi/board.json"], "startedAt": started,
               "finishedAt": now, "workComplete": False}
    prepared_note = ("prepared by the Codex-Pi SessionEnd hook; never acceptance, ownership "
                     "or permission")
    id_source = _safe_id(thread, 24).replace("-", "")
    outcome = outcome_id or f"PI-PRESENCE-{id_source[:12]}"
    record = {"schemaVersion": 1, "session": _text(thread, 200), "generatedAt": now,
              "outcomeId": outcome, "outcome": payload, "note": prepared_note,
              "roundSource": "retained board delivery events only; a pruned history can under-report",
              "recordCommand": ("python3 {} record-outcome --repo {} --outcome {} "
                                "--record-file <record>").format(RUNTIME_DIR / "pi_board.py",
                                                                 Path(repo), outcome)}
    path, record_path = paths["path"], paths["recordPath"]
    changed = False
    kept_existing = False
    if path.exists():
        existing = None
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        if not isinstance(existing, dict) or existing.get("outcome") != payload:
            kept_existing = True
        record["preparedBy"] = "session-end-hook"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        changed = True
    if not record_path.exists() or kept_existing:
        record_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        changed = changed or not kept_existing
    result = {"ok": True, "path": str(path), "recordPath": str(record_path), "outcomeId": outcome,
              "outcome": payload, "filed": False, "changed": changed,
              "recordCommand": ("python3 {} record-outcome --repo {} --outcome {} --record-file {}"
                                .format(RUNTIME_DIR / "pi_board.py", Path(repo), outcome,
                                        record_path))}
    if kept_existing:
        result["problem"] = "kept-existing-closeout"
    if apply:
        try:
            from pi_outcome import record_outcome
            filed = record_outcome(Path(repo), paths["common"], outcome, payload)
        except Exception as exc:  # noqa: BLE001 - a failed filing stays visible, never silent
            result["ok"] = False
            result["problem"] = f"filing failed: {type(exc).__name__}: {_text(exc, 160)}"
            return result
        result["filed"] = bool(filed.get("ok", True))
        result["revision"] = filed.get("revision")
        result["idempotent"] = filed.get("idempotent")
    return result


def main() -> int:  # pragma: no cover - the module is called by pi_handoff and pi_board
    raise SystemExit("pi_presence.py is a library; use pi_handoff.py hook or pi_board.py closeout")


if __name__ == "__main__":  # pragma: no cover
    main()
