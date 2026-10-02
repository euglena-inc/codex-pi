"""Board storage layer: bounded JSON files, locks, monitor records, owner routes and
route pause. No event or queue logic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from pi_core import LockHeld, TASK_RE, atomic, canonical_root, git_common_dir, lock_fd


SCHEMA_VERSION = 1
BOARD_DIR = "codex-pi"
BOARD_FILE = "board.json"
BOARD_LOCK = "board.lock"
MONITOR_LOG = "board-monitor.log"
MONITOR_FILE = "board.monitor.json"
MONITOR_LOCK = "board.monitor.lock"
ROUTE_DIR = "routes"
ROUTE_SCHEMA_VERSION = 1
MAX_BOARD_BYTES = 262_144
MAX_MONITOR_LOG_BYTES = 65_536
MAX_MONITOR_BYTES = 65_536
MAX_HANDLED_EVENTS = 20
MAX_HANDLED_IDS = 1000
MAX_PENDING_DISPLAY = 50
MAX_SUMMARY = 300
MAX_NOTE = 300
MAX_MONITORS = 200
MONITOR_LEASE_SECONDS = 90.0
GIT_TIMEOUT_SECONDS = 10.0
TRANSPORT_OFFLINE = "offline"
TRANSPORT_CLI_QUEUE = "cli-queue"
TRANSPORTS = (TRANSPORT_OFFLINE, TRANSPORT_CLI_QUEUE)
EVENT_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
FULL_OID_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
THREAD_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
DECISIONS = {"accept": "accepted", "reject": "rejected",
             "changes_requested": "changes_requested", "resolve": "resolved"}
REVIEW_KINDS = ("review_required",)


class BoardOverflow(ValueError):
    """The bounded board snapshot cannot hold the pending work."""


# ---------------------------------------------------------------------------
# small bounded helpers
# ---------------------------------------------------------------------------

def _text(value, limit: int) -> str:
    text = str(value).replace("\x00", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _read_bounded_json(path, limit: int):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > limit:
        return None, "oversized"
    try:
        return json.loads(raw.decode("utf-8")), None
    except ValueError:
        return None, "invalid"


def _trim_lines(path: Path, keep_bytes: int) -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > keep_bytes * 2:
                stream.seek(size - keep_bytes * 2)
            raw = stream.read()
    except OSError:
        return
    tail = raw[-keep_bytes:]
    cut = tail.find(b"\n")
    if cut >= 0:
        tail = tail[cut + 1:]
    try:
        path.write_bytes(tail)
    except OSError:
        pass


def handoff_root() -> Path:
    override = os.environ.get("CODEX_PI_HANDOFF_ROOT")
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        return root
    home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    return home / "codex-pi" / "handoffs"


def thread_key(thread: str) -> str:
    return hashlib.sha256(str(thread).encode("utf-8")).hexdigest()


def _validate_thread(value, required: bool = True):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError("an exact owner desktop thread UUID is required for cli-queue")
        return None
    value = str(value).strip()
    if not THREAD_RE.fullmatch(value):
        raise ValueError("owner thread must be an exact UUID (8-4-4-4-12 hex); "
                         "fuzzy or mismatched owners are never routed")
    return value.lower()


def _validate_transport(value) -> str:
    if value not in TRANSPORTS:
        raise ValueError(f"transport must be one of {', '.join(TRANSPORTS)}")
    return value


def board_file_for_common(common) -> Path:
    return Path(common) / BOARD_DIR / BOARD_FILE


def board_file_for_repo(repo):
    root = canonical_root(Path(repo))
    common = git_common_dir(root)
    return root, common, board_file_for_common(common)


def monitor_lease_seconds() -> float:
    raw = os.environ.get("CODEX_PI_MONITOR_LEASE_SECONDS")
    try:
        value = float(raw) if raw else MONITOR_LEASE_SECONDS
    except (TypeError, ValueError):
        value = MONITOR_LEASE_SECONDS
    return value if 1.0 <= value <= 86400 else MONITOR_LEASE_SECONDS


def validate_board(data) -> str | None:
    if not isinstance(data, dict):
        return "board is not a JSON object"
    if data.get("schemaVersion") != SCHEMA_VERSION:
        return f"board schemaVersion must be {SCHEMA_VERSION}"
    revision = data.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return "board revision is invalid"
    cards = data.get("cards")
    if not isinstance(cards, dict):
        return "board cards is not an object"
    for task_id, card in cards.items():
        if not isinstance(task_id, str) or not TASK_RE.fullmatch(task_id):
            return "board contains an unsafe task id"
        if not isinstance(card, dict):
            return f"card {task_id} is not an object"
        if card.get("taskId") != task_id:
            return f"card {task_id} identity mismatch"
        if not isinstance(card.get("events", []), list):
            return f"card {task_id} events are not a list"
    return None


def read_board(path):
    data, problem = _read_bounded_json(path, MAX_BOARD_BYTES)
    if problem is not None:
        return None, problem
    problem = validate_board(data)
    if problem:
        return None, problem
    return data, None


def _serialized(board) -> bytes:
    return (json.dumps(board, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _shrink_handled(board) -> None:
    """Aggressive handled-history pruning only; never touches unhandled work."""
    for card in (board.get("cards") or {}).values():
        if not isinstance(card, dict):
            continue
        card["events"] = [event for event in card.get("events", [])
                          if isinstance(event, dict) and not event.get("handled")]
        handled = card.get("handled")
        if isinstance(handled, dict) and len(handled) > MAX_HANDLED_IDS:
            ordered = sorted(handled.items(), key=lambda item: (item[1] or {}).get("at") or 0)
            card["handled"] = dict(ordered[-MAX_HANDLED_IDS:])


def _write_board(board_file, board) -> None:
    """Refuse a write that would exceed the bounded snapshot; never drop pending."""
    if len(_serialized(board)) > MAX_BOARD_BYTES:
        _shrink_handled(board)
        if len(_serialized(board)) > MAX_BOARD_BYTES:
            raise BoardOverflow(
                f"board snapshot would exceed MAX_BOARD_BYTES={MAX_BOARD_BYTES}; refusing the "
                f"write instead of silently dropping unhandled events or decisions: {board_file}")
    atomic(board_file, board)


# ---------------------------------------------------------------------------
# monitor lease/freshness (separate from the semantic board revision)
# ---------------------------------------------------------------------------

def monitor_paths(board_file):
    directory = Path(board_file).parent
    return directory / MONITOR_FILE, directory / MONITOR_LOCK


def read_monitors(board_file):
    path, _lock = monitor_paths(board_file)
    if not path.exists():
        return None, "missing"
    data, problem = _read_bounded_json(path, MAX_MONITOR_BYTES)
    if problem is not None:
        return None, problem
    if not isinstance(data, dict) or data.get("schemaVersion") != 1 \
            or not isinstance(data.get("tasks"), dict):
        return None, "invalid"
    return data, None


def monitor_for(monitors, task_id):
    if not isinstance(monitors, dict):
        return None
    record = (monitors.get("tasks") or {}).get(task_id)
    return record if isinstance(record, dict) else None


def write_monitor_record(board_file, task_id: str, record: dict, now: float) -> bool:
    """Persist one bounded monitor lease entry without touching board revision."""
    path, lock = monitor_paths(board_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = lock_fd(lock, blocking=False)
    except (LockHeld, OSError):
        return False
    try:
        data, problem = _read_bounded_json(path, MAX_MONITOR_BYTES)
        if problem is not None or not isinstance(data, dict):
            data = {"schemaVersion": 1, "tasks": {}}
        tasks = data.setdefault("tasks", {})
        entry = dict(record)
        entry["task"] = task_id
        entry["refreshedAt"] = now
        entry["updatedAt"] = now
        tasks[task_id] = entry
        if len(tasks) > MAX_MONITORS:
            ordered = sorted(tasks.items(), key=lambda item: (item[1] or {}).get("updatedAt") or 0)
            data["tasks"] = dict(ordered[-MAX_MONITORS:])
        data["schemaVersion"] = 1
        if len(_serialized(data)) > MAX_MONITOR_BYTES:
            return False
        atomic(path, data)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def record_monitor_error(task: dict, message: str) -> None:
    """Bounded monitor-error evidence: log plus a visible unhealthy lease record."""
    common_raw = task.get("commonDir") if isinstance(task, dict) else None
    if not isinstance(common_raw, str) or not common_raw.strip():
        return
    directory = Path(common_raw) / BOARD_DIR
    path = directory / MONITOR_LOG
    task_id = task.get("task") if isinstance(task, dict) else None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"schemaVersion": 1, "at": time.time(), "task": task_id,
                           "error": _text(message, 300)}, ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
        if path.stat().st_size > MAX_MONITOR_LOG_BYTES:
            _trim_lines(path, MAX_MONITOR_LOG_BYTES // 2)
    except OSError:
        pass
    try:
        board_file = board_file_for_common(Path(common_raw))
        monitors, _problem = read_monitors(board_file)
        previous = monitor_for(monitors, task_id)
        record = dict(previous) if isinstance(previous, dict) else {}
        record.update({"task": task_id, "healthy": False, "error": _text(message, 300),
                       "source": "monitor", "pid": os.getpid()})
        write_monitor_record(board_file, task_id, record, time.time())
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# route registration / pause (short recovery evidence)
# ---------------------------------------------------------------------------

def route_paths(thread: str):
    directory = handoff_root() / ROUTE_DIR
    key = thread_key(thread)
    return directory / f"{key}.json", directory / f"{key}.paused.json"


def register_route(thread: str, board_file, task_ids, transport: str, now: float) -> dict:
    path, _pause = route_paths(thread)
    path.parent.mkdir(parents=True, exist_ok=True)
    route = {"schemaVersion": ROUTE_SCHEMA_VERSION, "thread": thread,
             "boardPath": str(board_file), "taskIds": list(task_ids),
             "transport": transport, "createdAt": now, "updatedAt": now}
    atomic(path, route)
    return route


def read_route(thread: str):
    path, _pause = route_paths(thread)
    if not path.exists():
        return None, "missing"
    data, problem = _read_bounded_json(path, 16_384)
    if problem is not None:
        return None, problem
    if not isinstance(data, dict) or data.get("schemaVersion") != ROUTE_SCHEMA_VERSION \
            or data.get("thread") != thread or not isinstance(data.get("taskIds"), list):
        return None, "invalid"
    return data, None


def pause_route(thread: str, reason: str = "user interrupted this Codex session", now=None) -> dict:
    now = time.time() if now is None else now
    thread = _validate_thread(thread)
    path, pause = route_paths(thread)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic(pause, {"schemaVersion": 1, "thread": thread, "pausedAt": now,
                   "reason": _text(reason, 200), "source": "interrupt"})
    return {"ok": True, "thread": thread, "pausedAt": now}


def route_paused(thread: str):
    _path, pause = route_paths(thread)
    if not pause.exists():
        return False, None
    data, problem = _read_bounded_json(pause, 4096)
    if problem is not None:
        return False, problem
    if not isinstance(data, dict) or data.get("thread") != thread:
        return False, "invalid"
    return True, None


def resume_route(thread: str) -> dict:
    thread = _validate_thread(thread)
    _path, pause = route_paths(thread)
    try:
        pause.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(f"could not clear route pause {pause}: {exc}") from None
    return {"ok": True, "thread": thread, "routePauseCleared": True}
