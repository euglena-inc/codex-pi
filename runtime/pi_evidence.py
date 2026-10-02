"""Bounded read-only evidence scanning: check receipts, running markers, log tails and the
normalized candidate identity. No writes and no acceptance decisions.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

from pi_core import ACTIVE_STATES, FULL_OID_RE, TERMINAL_STATES, inside
from pi_size import sanitize_snapshot


def normalize_candidate(status: dict) -> dict:
    """Single candidate-identity normalization used by every consumer.

    Terminal rounds use the exact ``endHead``; active rounds use the bounded
    ``currentHead`` probe only. A missing probe or unknown ownership never falls
    back to the round-start commit, because that would present old evidence as
    current. Returns ``{"status": "known"|"unknown", "head", "source",
    "reason"}``.
    """
    state = status.get("state")
    if state in TERMINAL_STATES:
        head = status.get("endHead")
        if isinstance(head, str) and head:
            return {"status": "known", "head": head.lower(), "source": "endHead",
                    "reason": None}
        return {"status": "unknown", "head": None, "source": None,
                "reason": "terminal round has no exact endHead"}
    if state in ACTIVE_STATES:
        head = status.get("currentHead")
        if isinstance(head, str) and head:
            return {"status": "known", "head": head.lower(), "source": "currentHead",
                    "reason": None}
        return {"status": "unknown", "head": None, "source": None,
                "reason": "active HEAD probe unavailable; the round-start commit is not a "
                          "candidate"}
    return {"status": "unknown", "head": None, "source": None,
            "reason": f"ownership state {state!r} has no verified candidate"}


def _safe_argv(value):
    """Bounded argv list from a receipt; malformed values become unknown (None)."""
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        return None
    argv = []
    for entry in value:
        if not isinstance(entry, str) or not entry or len(entry) > 4096:
            return None
        argv.append(entry)
    return argv


# ---------------------------------------------------------------------------
# bounded read-only status (no summaries, no transcript scans, no mutation)
# ---------------------------------------------------------------------------

STATUS_MAX_DIR_ENTRIES = 512
STATUS_MAX_RECEIPTS = 200
STATUS_MAX_FILE_BYTES = 2_000_000
STATUS_MAX_MARKER_BYTES = 16_384
STATUS_TAIL_BYTES = 8192
STATUS_TAIL_LINES = 20
STATUS_MAX_LINE = 400
CHECK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
LOG_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
VALID_COUNT_KEYS = ("run", "pass", "fail", "skip")
VALID_COUNT_FORMATS = ("go_verbose_top_level", "python_unittest_summary")
MAX_COUNT_VALUE = 10 ** 12


def _mtime(path: Path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


MAX_PID = 2 ** 31 - 1


def _pid_value(value):
    """Keep only a plausible POSIX pid; corrupt huge integers are unknown."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_PID:
        return None
    return value


def _clip(text, limit: int = STATUS_MAX_LINE) -> str:
    text = str(text).replace("\x00", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _safe_basename(value):
    """Bounded single-component file name; never a path, dotfile or separator."""
    if not isinstance(value, str) or not value or len(value) > 255:
        return None
    if value in (".", "..") or value.startswith(".") or "/" in value or "\\" in value \
            or "\x00" in value:
        return None
    return value


def _read_bounded_json(path: Path, limit: int):
    """Read at most ``limit`` bytes and parse JSON; never parse unbounded data."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        return None, "unreadable"
    if len(raw) > limit:
        return None, "oversized"
    try:
        return json.loads(raw.decode("utf-8")), None
    except ValueError:
        return None, "invalid"


def _sanitize_counts(value):
    """Keep only the fixed numeric counters and the known format marker."""
    if not isinstance(value, dict):
        return None
    counts = {}
    for key in VALID_COUNT_KEYS:
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= MAX_COUNT_VALUE:
            counts[key] = item
    if not counts:
        return None
    fmt = value.get("format")
    if isinstance(fmt, str) and fmt in VALID_COUNT_FORMATS:
        counts["format"] = fmt
    return counts


def _pid_running(pid) -> bool:
    pid = _pid_value(pid)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OverflowError:
        return False
    except PermissionError:
        return True


def _tail_evidence(path: Path):
    """Bounded tail read: never the full log or transcript."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"path": str(path), "error": _clip(exc, 200)}
    try:
        with path.open("rb") as stream:
            if size > STATUS_TAIL_BYTES:
                stream.seek(size - STATUS_TAIL_BYTES)
            raw = stream.read(STATUS_TAIL_BYTES)
    except OSError as exc:
        return {"path": str(path), "bytes": size, "error": _clip(exc, 200)}
    lines = raw.decode("utf-8", errors="replace").splitlines()[-STATUS_TAIL_LINES:]
    return {"path": str(path), "bytes": size, "mtime": _mtime(path),
            "tail": [_clip(line) for line in lines],
            "truncated": size > STATUS_TAIL_BYTES}


def _safe_receipt(path: Path):
    """Small safe receipt metadata; rejects unrelated JSON and unbounded strings."""
    data, problem = _read_bounded_json(path, STATUS_MAX_FILE_BYTES)
    if problem is not None:
        return None, f"{problem} receipt"
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None, "unrecognized receipt shape"
    check_id = data.get("id")
    log_name = data.get("log")
    digest = data.get("log_sha256")
    if not isinstance(check_id, str) or not CHECK_ID_RE.fullmatch(check_id):
        return None, "unsafe receipt id"
    if not isinstance(log_name, str) or not log_name.endswith(".log") \
            or _safe_basename(log_name) is None:
        return None, "unsafe receipt log identity"
    if not isinstance(digest, str) or not LOG_DIGEST_RE.fullmatch(digest):
        return None, "unsafe receipt log hash"
    code = data.get("exit_code")
    if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
        return None, "unsafe receipt exit code"
    failed = ((code is not None and code != 0) or bool(data.get("timed_out"))
              or bool(data.get("cancelled")))
    head = data.get("head")
    if head is not None and (not isinstance(head, str) or not FULL_OID_RE.fullmatch(head)):
        head = None
    dirty = data.get("dirty")
    if not isinstance(dirty, bool):
        dirty = None
    return {"id": check_id, "exitCode": code, "timedOut": bool(data.get("timed_out")),
            "cancelled": bool(data.get("cancelled")), "failed": failed,
            "testCounts": _sanitize_counts(data.get("test_counts")),
            "startedAt": _number(data.get("started_at")), "endedAt": _number(data.get("ended_at")),
            "deadlineAt": _number(data.get("deadline_at")),
            "argv": _safe_argv(data.get("argv")),
            "log": log_name, "logSha256": digest, "receipt": path.name, "head": head,
            "dirty": dirty,
            "resourceLimit": sanitize_snapshot(data.get("resource_limit") or data.get("resourceLimit"))}, None


def _redact_receipt_metadata(checks: dict) -> dict:
    """Status-safe copy: internal command identity and wrapper timing are never returned."""
    try:
        public = json.loads(json.dumps(checks))
    except (TypeError, ValueError):
        return {"dir": checks.get("dir"), "exists": checks.get("exists"),
                "partial": checks.get("partial"), "running": None,
                "receipts": {"recent": []}, "resourceGuard": {},
                "note": "receipt metadata is not serializable"}
    receipts = public.get("receipts")
    if isinstance(receipts, dict):
        for value in receipts.values():
            for item in (value if isinstance(value, list) else [value]):
                if isinstance(item, dict):
                    item.pop("argv", None)
                    item.pop("deadlineAt", None)
    return public


def _scan_checks(checks_dir: Path) -> dict:
    result = {"dir": str(checks_dir), "exists": checks_dir.is_dir(), "entries": 0,
              "partial": False, "running": None, "unreceiptedLog": None, "ignored": [],
              "ignoredCount": 0,
              "receipts": {"total": 0, "scanned": 0, "truncated": False, "partial": False,
                           "failedAttempts": 0, "latest": None, "latestFailed": None,
                           "latestSuccessful": None, "failedRecent": [], "unknownExitRecent": [],
                           "recent": []},
              "resourceGuard": {"breached": False, "unknown": False, "breaches": [],
                                "unknownScans": [], "running": None, "latestReceipt": None,
                                "note": "local no-follow byte guard; unknown is not verified and "
                                        "is never under budget"}}

    def note_ignored(name: str, reason: str) -> None:
        result["ignoredCount"] += 1
        if len(result["ignored"]) < 20:
            result["ignored"].append({"name": name, "reason": reason})

    if not result["exists"]:
        return result
    try:
        entries = []
        for index, entry in enumerate(checks_dir.iterdir()):
            if index >= STATUS_MAX_DIR_ENTRIES:
                result["partial"] = True
                break
            if entry.is_file():
                entries.append(entry)
    except OSError:
        return result
    result["entries"] = len(entries)
    result["receipts"]["partial"] = result["partial"]
    markers = [path for path in entries if path.name.endswith(".running")]
    receipt_files = [path for path in entries if path.name.endswith(".json")]
    logs = [path for path in entries if path.name.endswith(".log")]
    resolved = checks_dir.resolve()

    # Valid receipts first: only proven receipts may suppress a log candidate or
    # supersede a stale running marker. Unrelated or corrupt .json stays visible
    # as ignored evidence and never hides an unreceipted log.
    parsed = []
    for receipt in sorted(receipt_files, key=_mtime, reverse=True)[:STATUS_MAX_RECEIPTS]:
        item, problem = _safe_receipt(receipt)
        if item is None:
            note_ignored(receipt.name, problem)
            continue
        parsed.append(item)
    result["receipts"]["total"] = len(receipt_files)
    result["receipts"]["scanned"] = len(parsed)
    result["receipts"]["truncated"] = len(receipt_files) > STATUS_MAX_RECEIPTS
    result["receipts"]["recent"] = parsed
    result["receipts"]["failedAttempts"] = sum(1 for item in parsed if item["failed"])
    valid_receipt_stems = {Path(item["receipt"]).stem for item in parsed}

    def order(item):
        return item.get("endedAt") or item.get("startedAt") or 0

    result["receipts"]["latest"] = max(parsed, key=order, default=None)
    result["receipts"]["latestFailed"] = max((item for item in parsed if item["failed"]),
                                              key=order, default=None)
    result["receipts"]["latestSuccessful"] = max(
        (item for item in parsed if not item["failed"] and item["exitCode"] == 0),
        key=order, default=None)
    result["receipts"]["failedRecent"] = sorted(
        (item for item in parsed if item["failed"]), key=order, reverse=True)[:10]
    result["receipts"]["unknownExitRecent"] = sorted(
        (item for item in parsed if item["exitCode"] is None), key=order, reverse=True)[:10]

    active_marker_stems = set()
    for marker in sorted(markers, key=_mtime, reverse=True):
        stem = marker.name[: -len(".running")]
        if stem in valid_receipt_stems:
            note_ignored(marker.name, "stale running marker superseded by a valid receipt")
            continue
        data, problem = _read_bounded_json(marker, STATUS_MAX_MARKER_BYTES)
        started = _number(data.get("started_at")) if isinstance(data, dict) else None
        deadline = _number(data.get("deadline_at")) if isinstance(data, dict) else None
        timeout = _number(data.get("timeout_seconds")) if isinstance(data, dict) else None
        marker_id = data.get("id") if isinstance(data, dict) else None
        log_name = data.get("log") if isinstance(data, dict) else None
        pid = data.get("pid") if isinstance(data, dict) else None
        if problem is not None or started is None or deadline is None or deadline < started \
                or timeout is None or not 0 < timeout <= 604800 \
                or not isinstance(marker_id, str) or not CHECK_ID_RE.fullmatch(marker_id) \
                or not isinstance(log_name, str) or not log_name.endswith(".log") \
                or _safe_basename(log_name) is None:
            note_ignored(marker.name, f"{problem or 'invalid'} running marker")
            continue
        log_path = checks_dir / log_name
        if not inside(log_path, resolved):
            note_ignored(marker.name, "unsafe running marker log identity")
            continue
        now = time.time()
        if result["running"] is None:
            result["running"] = {
                "marker": marker.name, "id": marker_id,
                "pid": _pid_value(pid),
                "pidAlive": _pid_running(pid), "startedAt": started, "deadlineAt": deadline,
                "elapsedMs": int(max(0.0, min(now - started, 10 ** 9)) * 1000), "timeoutSeconds": timeout,
                "deadlinePassed": bool(now > deadline),
                "deadlineScope": "wrapper timeout only; never an inner command deadline",
                "log": log_name, "logEvidence": _tail_evidence(log_path),
                "resourceLimit": sanitize_snapshot(data.get("resource_limit") or data.get("resourceLimit")),
                "uncertain": True,
                "note": "a running marker proves a wrapper attempt started; a live pid is not progress"}
        active_marker_stems.add(stem)

    for log in sorted(logs, key=_mtime, reverse=True):
        if log.stem in valid_receipt_stems or log.stem in active_marker_stems:
            continue
        result["unreceiptedLog"] = {
            "log": log.name, "uncertain": True,
            "reason": "no validated receipt and no live running marker; quiet output is not failure",
            "recordedAt": _mtime(log), "logEvidence": _tail_evidence(log),
            "note": "unreceipted-log candidate only: not proof of an active, hung or failed check"}
        break

    # Local no-follow resource-guard snapshot. Unknown is never "under budget";
    # a breach is an observation, never a pass or acceptance.
    guard = {"breached": False, "unknown": False, "breaches": [], "unknownScans": [],
             "running": None, "latestReceipt": None,
             "note": "local no-follow byte guard; unknown is not verified and is never under budget"}

    def add_guard(source: str, name, snapshot) -> None:
        if not isinstance(snapshot, dict):
            return
        entry = {"source": source, "name": name, "path": snapshot.get("path"),
                 "maxBytes": snapshot.get("maxBytes"), "observedBytes": snapshot.get("observedBytes"),
                 "breached": bool(snapshot.get("breached")), "unknown": bool(snapshot.get("unknown")),
                 "breachBasis": snapshot.get("breachBasis"),
                 "reason": snapshot.get("reason")}
        if entry["breached"]:
            guard["breached"] = True
            if len(guard["breaches"]) < 5:
                guard["breaches"].append(entry)
        if entry["unknown"]:
            guard["unknown"] = True
            if len(guard["unknownScans"]) < 5:
                guard["unknownScans"].append(entry)

    if isinstance(result["running"], dict):
        add_guard("running", result["running"].get("marker"), result["running"].get("resourceLimit"))
        if isinstance(result["running"].get("resourceLimit"), dict):
            guard["running"] = result["running"]["resourceLimit"]
    for item in parsed:
        add_guard("receipt", item.get("receipt"), item.get("resourceLimit"))
    if isinstance(result["receipts"].get("latest"), dict) \
            and isinstance(result["receipts"]["latest"].get("resourceLimit"), dict):
        guard["latestReceipt"] = result["receipts"]["latest"]["resourceLimit"]
    result["resourceGuard"] = guard
    return result


def _head_probe(worktree: Path):
    """Bounded read-only current HEAD probe for an active round."""
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=10, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return None, f"git rev-parse exited {proc.returncode}"
    raw = proc.stdout.strip()
    if not FULL_OID_RE.fullmatch(raw):
        return None, "git returned an unexpected object id"
    return raw.lower(), None


def probe_worktree_head(worktree: Path):
    """Public bounded worktree HEAD probe for the board/accept gate."""
    return _head_probe(worktree)


def acceptance_line(check_id, status, exit_code=None, counts=None) -> str:
    """One compact evidence line shared by the delivery card and ``result``.

    ``<id> exit=<n> run=.. pass=.. fail=.. skip=..`` for a check with a known
    exit code; ``<id> missing`` / ``<id> unknown`` when there is no usable
    receipt. A known non-covered status is appended in brackets.
    """
    check_id = str(check_id)[:60]
    if status == "missing":
        return f"{check_id} missing"
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return f"{check_id} {status if status in ('failed', 'skipped') else 'unknown'}"
    parts = [check_id, f"exit={exit_code}"]
    if isinstance(counts, dict):
        for key in VALID_COUNT_KEYS:
            value = counts.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                parts.append(f"{key}={value}")
    if status in ("failed", "skipped"):
        parts.append(f"[{status}]")
    return " ".join(parts)
