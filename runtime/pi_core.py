"""Shared filesystem, process, lock, config and layout helpers for the Codex-Pi runtime.

The lowest layer of the task side: it imports nothing from the other task modules.
Everything above it (evidence, phase, brief, supervisor, CLI) depends on it, never the
other way round.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from pi_takeover import review_policy


SCHEMA_VERSION = 1
# The CLI entry that task-side messages and records name.
TASK_CLI = Path(__file__).with_name("pi_task.py").resolve()
DEFAULT_MODEL = "deepseek/deepseek-flash"
ALLOWED_MODELS = (DEFAULT_MODEL, "newapi/glm-5.3", "newapi/deepseek-flash", "qwen38/qwen38")
DEFAULT_THINKING = "max"
DEFAULT_TIMEOUT = 14400
MAX_TIMEOUT = 604800
MAX_PROMPT_BYTES = 2_000_000
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
NATIVE_TOOLS = "check,progress,readiness"
CODEMODE_TOOL = "codemode"
READ_ONLY_TOOLS = "read,grep,find,ls," + NATIVE_TOOLS + "," + CODEMODE_TOOL
WRITABLE_TOOLS = "read,write,edit,bash," + NATIVE_TOOLS + "," + CODEMODE_TOOL
TASK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
TERMINAL_STATES = ("completed", "failed", "timed_out", "cancelled", "interrupted")
ACTIVE_STATES = ("starting", "running")
FULL_OID_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
CONFIG_KEYS = ("schemaVersion", "model", "thinking", "constraints", "checks",
               "maxWorkers", "timeoutSeconds", "network")
NETWORK_KEYS = ("proxyUrl", "diagnostics")
# Explicit routing overrides every conflicting proxy form for the Pi child
# only. NO_PROXY/no_proxy are deliberately preserved so intentional exclusions
# keep their documented meaning.
NETWORK_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                          "ALL_PROXY", "all_proxy")
NETWORK_FILE = "round.network.jsonl"
_MAX_NETWORK_FILE_BYTES = 65536
_NETWORK_SATURATION_MARGIN = 256
NETWORK_PHASES = ("observer_ready", "request_error", "truncated", "oversized")
NETWORK_CLASSES = ("connection_reset", "connection_refused", "timeout", "dns_failure",
                   "tls_failure", "proxy_connect_failure", "post_header_error",
                   "transport_error", "abort_cleanup", "unknown")
_NETWORK_RECORD_KEYS = frozenset(("at", "dur", "phase", "class", "code", "status", "hdr",
                                  "scope", "proc", "primary"))
_NETWORK_CODE_RE = re.compile(r"[A-Za-z0-9_]{1,64}\Z")
_NETWORK_SCOPE_RE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
_NETWORK_PROC_RE = re.compile(r"[0-9a-f]{1,16}\Z")
REFERENCE_EXTENSIONS = ("md", "markdown", "txt", "json", "sh", "bash", "zsh", "py",
                        "js", "mjs", "cjs", "ts", "tsx", "yaml", "yml", "toml", "cfg", "ini")


class LockHeld(Exception):
    """Another process owns the lock."""


# ---------------------------------------------------------------------------
# small filesystem / process helpers (atomic + terminate are reused by pi_check)
# ---------------------------------------------------------------------------

def atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True,
                                   stderr=subprocess.DEVNULL).strip()


def forbidden_checkouts(worktree: Path, task_dir: Path, round_number: int) -> tuple:
    """Return ``(forbidden roots, allowed roots)`` for the command guard.

    Forbidden: the repository's primary checkout and every other registered
    worktree of the same repository. Allowed (never forbidden, even when nested
    under a forbidden root such as the main checkout's ``.git``): this round's
    worktree, the task ``tools`` directory and the round ``checks`` directory.
    """
    roots = []
    try:
        listing = git(worktree, "worktree", "list", "--porcelain")
    except (subprocess.CalledProcessError, OSError):
        listing = ""
    own = os.path.realpath(str(worktree))
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = os.path.realpath(line[len("worktree "):].strip())
            if path != own and path not in roots:
                roots.append(path)
    allowed = [own, str(task_dir / "tools"),
               str(task_dir / "rounds" / str(round_number) / "round.checks")]
    return roots, allowed


def primary_root(checkout: Path) -> Path:
    """Resolve the repository's primary (main) worktree, if any.

    Used only to reject the Git primary checkout as an implementation worktree.
    Config and state always follow the checkout supplied in --repo; the runtime
    never redirects to or loads config from another checkout automatically.
    """
    try:
        listing = git(checkout, "worktree", "list", "--porcelain")
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot resolve the primary worktree for {checkout}") from exc
    for line in listing.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):]).resolve()
    raise ValueError(f"no primary worktree found for {checkout}")


def canonical_root(repo: Path) -> Path:
    """Resolved checkout top-level of the path supplied in --repo.

    Nested paths normalize to their checkout root. The runtime deliberately does
    not redirect to the Git primary worktree: a linked project checkout keeps
    its own config and identity (for example sample-project-linked).
    """
    repo = repo.expanduser()
    if not repo.exists():
        raise ValueError(f"repository path does not exist: {repo}")
    try:
        top = git(repo if repo.is_dir() else repo.parent, "rev-parse", "--show-toplevel")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ValueError(f"not a git repository: {repo}") from exc
    return Path(top).resolve()


def git_common_dir(cwd: Path) -> Path:
    try:
        raw = git(cwd, "rev-parse", "--git-common-dir")
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot resolve git common dir for {cwd}") from exc
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def inside(path: Path, root: Path) -> bool:
    path, root = Path(path).resolve(), Path(root).resolve()
    return path == root or root in path.parents


def lock_fd(path: Path, blocking: bool = False, timeout: float = 30.0) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    if not blocking:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockHeld(f"lock is held: {path}") from None
        return fd
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise LockHeld(f"lock wait timed out: {path}") from None
            time.sleep(0.05)


def lock_is_held(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin may return EPERM for signal 0 after the group leader is reaped.
        # Verify the process table; EPERM alone never proves the group is gone.
        try:
            rows = subprocess.check_output(["ps", "-axo", "pgid=,stat="], text=True, timeout=3)
        except (subprocess.SubprocessError, OSError):
            return True
        return any(len(parts := row.split()) == 2 and parts[0] == str(pgid)
                   and not parts[1].startswith("Z") for row in rows.splitlines())


def terminate(child: subprocess.Popen) -> None:
    """Own the child's process group until every descendant is gone.

    The leader may exit on TERM while its descendants ignore it; waiting only
    for the leader leaks those writers. This is the tested group fix, kept
    intact for the detached lifecycle.
    """
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        child.wait()
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        child.poll()  # reap the leader so it does not keep an empty group visible
        if not group_alive(child.pid):
            child.wait()
            return
        time.sleep(0.05)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def looks_like_path(entry: str) -> bool:
    if entry.startswith(("/", "~", ".")) or "\\" in entry:
        return True
    parts = entry.replace("\\", "/").split("/")
    if ".." in parts:
        return True
    if " " not in entry and "/" in entry:
        return True
    return bool(re.fullmatch(r"[\w.-]+\.(" + "|".join(REFERENCE_EXTENSIONS) + r")", entry))


def validate_reference(root: Path, entry: str, kind: str) -> None:
    if not looks_like_path(entry):
        return
    if ".." in entry.replace("\\", "/").split("/"):
        raise ValueError(f"{kind} reference uses path traversal and is rejected: {entry!r}")
    candidate = Path(entry).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if not inside(candidate, root):
        raise ValueError(f"{kind} reference escapes the repository and is rejected: {entry!r}")


_PROXY_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\Z")


def normalize_proxy_url(value) -> str:
    """Validate and canonicalize one credential-free http(s) proxy URL.

    Errors never echo the supplied value: a rejected URL may carry credentials
    or private hostnames, so no error message may include any part of it.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("config network.proxyUrl must be a non-empty string")
    if value != value.strip():
        raise ValueError("config network.proxyUrl must not have surrounding whitespace")
    for character in value:
        if character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F:
            raise ValueError("config network.proxyUrl must not contain whitespace or control "
                             "characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("config network.proxyUrl must be a valid URL with an explicit numeric "
                         "port") from None
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("config network.proxyUrl must use the http or https scheme")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("config network.proxyUrl must not contain user information or "
                         "credentials")
    host = parsed.hostname
    if not host:
        raise ValueError("config network.proxyUrl must include a host")
    if port is None:
        raise ValueError("config network.proxyUrl must include an explicit port")
    if not 1 <= port <= 65535:
        raise ValueError("config network.proxyUrl port must be between 1 and 65535")
    if parsed.path not in ("", "/"):
        raise ValueError("config network.proxyUrl must not include a path")
    if parsed.query or parsed.fragment:
        raise ValueError("config network.proxyUrl must not include a query or fragment")
    if "%" in host:
        raise ValueError("config network.proxyUrl host must not use percent-encoding")
    normalized = host.lower()
    if ":" in normalized:
        try:
            if ipaddress.ip_address(normalized).version != 6:
                raise ValueError
        except ValueError:
            raise ValueError("config network.proxyUrl host is not a valid IPv6 address") from None
        authority = f"[{normalized}]"
    else:
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            if not _PROXY_HOST_RE.match(normalized):
                raise ValueError("config network.proxyUrl host is not a valid hostname or IP "
                                 "address") from None
        authority = normalized
    return f"{scheme}://{authority}:{port}"


def parse_network_policy(raw, context: str = "config network") -> dict:
    """Return ``{proxyUrl, diagnostics}`` or raise without echoing values."""
    if raw is None:
        return {"proxyUrl": None, "diagnostics": False}
    if not isinstance(raw, dict):
        raise ValueError(f"{context} must be an object with only {list(NETWORK_KEYS)}")
    unknown = sorted(set(raw) - set(NETWORK_KEYS))
    if unknown:
        raise ValueError(f"{context} has unsupported keys {unknown}; allowed: {list(NETWORK_KEYS)}")
    proxy = raw.get("proxyUrl")
    if proxy is not None:
        proxy = normalize_proxy_url(proxy)
    diagnostics = raw.get("diagnostics", False)
    if not isinstance(diagnostics, bool):
        raise ValueError(f"{context}.diagnostics must be a boolean")
    return {"proxyUrl": proxy, "diagnostics": diagnostics}


def network_policy_for_task(task: dict) -> dict:
    """Frozen task policy; an absent key is the legacy inherited behavior."""
    raw = task.get("network") if isinstance(task, dict) else None
    if raw is None:
        return {"proxyUrl": None, "diagnostics": False, "source": "legacy"}
    policy = parse_network_policy(raw, "frozen task network")
    policy["source"] = "frozen"
    return policy


def apply_network_policy(env: dict, policy: dict, diagnostics_file=None, scope: str | None = None,
                         supervisor_pid: int | None = None, preload_path=None):
    """Return ``(env, bounded record)`` for the Pi child process only.

    The supervisor and any Codex queue transport keep their original
    environment. Explicit routing overrides every conflicting proxy form and
    preserves NO_PROXY/no_proxy. Diagnostics append one ``--import`` option to
    the existing NODE_OPTIONS so unrelated options survive.
    """
    result = dict(env)
    proxy = policy.get("proxyUrl")
    proxy_present = any(result.get(key) for key in NETWORK_PROXY_ENV_KEYS)
    if proxy:
        for key in NETWORK_PROXY_ENV_KEYS:
            result[key] = proxy
    enabled = bool(policy.get("diagnostics"))
    if enabled:
        if diagnostics_file is None or not scope:
            raise ValueError("network diagnostics need a sidecar path and a bounded scope")
        if preload_path is None:
            raise ValueError("network diagnostics preload module is unavailable")
        preload = Path(preload_path)
        if not preload.is_file() or preload.is_symlink():
            raise ValueError("network diagnostics preload module is missing from the frozen tools")
        result["CODEX_PI_NETWORK_DIAG_FILE"] = str(diagnostics_file)
        result["CODEX_PI_NETWORK_DIAG_SCOPE"] = str(scope)
        if supervisor_pid is not None:
            result["CODEX_PI_NETWORK_DIAG_SUPERVISOR"] = str(int(supervisor_pid))
        existing = result.get("NODE_OPTIONS", "")
        addition = f"--import={preload.resolve().as_uri()}"
        result["NODE_OPTIONS"] = f"{existing.rstrip()} {addition}".lstrip() if existing.strip() \
            else addition
    record = {
        "proxy": {
            "mode": "explicit" if proxy else "inherited",
            "source": policy.get("source", "frozen"),
            "origin": proxy,
            "envProxyPresent": bool(proxy_present),
        },
        "diagnostics": {"enabled": enabled,
                         "file": str(diagnostics_file) if enabled else None},
    }
    return result, record


def _network_number(value, low: float, high: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and low <= value <= high)


def _valid_network_record(record) -> bool:
    """Strict allowlist validation for one sidecar record.

    The sidecar is written by the observed Pi process, so it is untrusted
    observational data: unknown keys, forged classifications, long strings and
    wrong types make the whole projection unreadable instead of being echoed.
    """
    if not isinstance(record, dict) or set(record) - _NETWORK_RECORD_KEYS:
        return False
    phase = record.get("phase")
    if not isinstance(phase, str) or phase not in NETWORK_PHASES:
        return False
    if "at" in record and record["at"] is not None \
            and not _network_number(record["at"], 0, 86400):
        return False
    if "dur" in record and record["dur"] is not None \
            and not _network_number(record["dur"], 0, 3600000):
        return False
    if "code" in record and record["code"] is not None \
            and (not isinstance(record["code"], str)
                 or not _NETWORK_CODE_RE.fullmatch(record["code"])):
        return False
    if "status" in record and record["status"] is not None \
            and (isinstance(record["status"], bool) or not isinstance(record["status"], int)
                 or not 100 <= record["status"] <= 599):
        return False
    for key in ("hdr", "primary"):
        if key in record and record[key] is not None \
                and not isinstance(record[key], bool):
            return False
    if "scope" in record and record["scope"] is not None \
            and (not isinstance(record["scope"], str)
                 or not _NETWORK_SCOPE_RE.fullmatch(record["scope"])):
        return False
    if "proc" in record and record["proc"] is not None \
            and (not isinstance(record["proc"], str)
                 or not _NETWORK_PROC_RE.fullmatch(record["proc"])):
        return False
    classification = record.get("class")
    if phase == "request_error":
        return isinstance(classification, str) and classification in NETWORK_CLASSES
    return classification is None


def read_network_sidecar(path) -> dict:
    """Bounded observational summary of one round sidecar; never raises.

    Only complete, allowlist-valid records in a bounded UTF-8 prefix are
    trusted. Missing, oversized, truncated, non-UTF-8 or forged content yields
    ``status='unreadable'`` with empty counts, so untrusted values never reach
    the public status/result projection and never look healthy.
    """
    result = {"status": "missing", "records": 0, "failures": 0, "truncated": False,
              "oversized": False, "classes": {}}
    if path is None:
        return result
    path = Path(path)
    try:
        if not path.is_file():
            return result
        size = path.stat().st_size
        with path.open("rb") as stream:
            raw = stream.read(_MAX_NETWORK_FILE_BYTES + 1)
    except OSError:
        result["status"] = "unreadable"
        return result

    def unreadable(truncated: bool = False, oversized: bool = False) -> dict:
        result["status"] = "unreadable"
        result["truncated"] = bool(truncated)
        result["oversized"] = bool(oversized)
        return result

    if len(raw) > _MAX_NETWORK_FILE_BYTES:
        return unreadable(oversized=True)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return unreadable()
    partial = bool(text) and not text.endswith("\n")
    truncated = size >= _MAX_NETWORK_FILE_BYTES - _NETWORK_SATURATION_MARGIN
    if partial:
        return unreadable(truncated=True)
    classes = {}
    for line in text.splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            return unreadable(truncated=truncated)
        if not _valid_network_record(record):
            return unreadable(truncated=truncated)
        if record["phase"] == "truncated":
            truncated = True
            continue
        result["records"] += 1
        if record["phase"] != "request_error":
            continue
        classification = record.get("class")
        if isinstance(classification, str):
            classes[classification] = classes.get(classification, 0) + 1
            if classification != "abort_cleanup":
                result["failures"] += 1
    result["classes"] = dict(sorted(classes.items(), key=lambda item: (-item[1], item[0]))[:8])
    result["truncated"] = truncated
    result["status"] = "present"
    return result


def require_allowed_model(model, context: str) -> str:
    """Allow only the explicit, locally configured worker model choices."""
    if model not in ALLOWED_MODELS:
        choices = ", ".join(repr(item) for item in ALLOWED_MODELS)
        raise ValueError(f"{context} model {model!r} is not allowed; choose one of {choices} "
                         "(no substitution or fallback is performed; existing evidence is not modified)")
    return model


def config_hint(root: Path) -> str:
    """Point at a sibling checkout holding config without loading it."""
    try:
        listing = git(root, "worktree", "list", "--porcelain")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    found = []
    for line in listing.splitlines():
        if not line.startswith("worktree "):
            continue
        candidate = Path(line[len("worktree "):]).resolve()
        if candidate != root and (candidate / ".agents" / "codex-pi.json").is_file():
            found.append(str(candidate))
    if not found:
        return ""
    return ("; config exists only in another checkout: " + ", ".join(found) +
            "; configs are never loaded across checkouts, so pass --repo inside that checkout")


def load_config(root: Path) -> dict:
    path = root / ".agents" / "codex-pi.json"
    if not path.exists():
        raise ValueError(
            f"missing project config {path}; create it as "
            '{"schemaVersion":1,"model":"deepseek/deepseek-flash","thinking":"max",'
            '"constraints":["AGENTS.md"],"checks":[],"maxWorkers":1,"timeoutSeconds":14400}'
            + config_hint(root))
    real = path.resolve()
    if not inside(real, root):
        raise ValueError(f"config path escapes the repository (symlink or traversal): {path} -> {real}")
    try:
        data = json.loads(real.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"config is not valid JSON: {real}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"config must be a JSON object: {real}")
    unknown = sorted(set(data) - set(CONFIG_KEYS))
    if unknown:
        raise ValueError(f"config has unsupported keys {unknown}; allowed: {list(CONFIG_KEYS)}")
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"config schemaVersion must be {SCHEMA_VERSION}, got {data.get('schemaVersion')!r}")

    model = data.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or not model.strip():
        raise ValueError("config model must be a non-empty string")
    require_allowed_model(model, "config")
    thinking = data.get("thinking", DEFAULT_THINKING)
    if not isinstance(thinking, str) or thinking not in THINKING_LEVELS:
        raise ValueError(f"config thinking must be one of {list(THINKING_LEVELS)}")
    constraints = data.get("constraints", [])
    checks = data.get("checks", [])
    for label, entries in (("constraints", constraints), ("checks", checks)):
        if not isinstance(entries, list) or any(not isinstance(item, str) or not item.strip() for item in entries):
            raise ValueError(f"config {label} must be an array of non-empty strings")
        for entry in entries:
            validate_reference(root, entry, label[:-1] if label.endswith("s") else label)
    max_workers = data.get("maxWorkers", 1)
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 64:
        raise ValueError("config maxWorkers must be an integer between 1 and 64")
    timeout = data.get("timeoutSeconds", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"config timeoutSeconds must be positive and at most {MAX_TIMEOUT}")
    network = parse_network_policy(data.get("network"))
    return {"path": str(real), "schemaVersion": SCHEMA_VERSION, "model": model, "thinking": thinking,
            "constraints": list(constraints), "checks": list(checks),
            "maxWorkers": max_workers, "timeoutSeconds": timeout, "network": network}


# ---------------------------------------------------------------------------
# state layout and admission
# ---------------------------------------------------------------------------

def state_root(common: Path) -> Path:
    return common / "codex-pi"


def task_dir_for(common: Path, task: str) -> Path:
    if not TASK_RE.fullmatch(task):
        raise ValueError("task must match [A-Za-z0-9][A-Za-z0-9_-]{0,99}")
    return state_root(common) / "tasks" / task


CODEX_IO_FILE = "codex-io.jsonl"


def record_codex_io(task_dir, command: str, nbytes: int, **extra) -> bool:
    """Append one size-only line (time, command, bytes) to the task's ``codex-io.jsonl``.

    Content is never recorded. The file is append-only, an existing task
    directory is required, and every failure is swallowed: accounting must never
    change a command's output or exit code.
    """
    try:
        directory = Path(task_dir)
        if not directory.is_dir():
            return False
        line = json.dumps({"at": time.time(), "command": str(command), "bytes": int(nbytes),
                           **extra}, separators=(",", ":")) + "\n"
        fd = os.open(str(directory / CODEX_IO_FILE),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001 - accounting is best effort
        return False


def require_task_arg(task: str) -> str:
    if not TASK_RE.fullmatch(task):
        raise ValueError("task must match [A-Za-z0-9][A-Za-z0-9_-]{0,99}")
    return task


def list_rounds(task_dir: Path):
    rounds_dir = task_dir / "rounds"
    result = []
    if rounds_dir.is_dir():
        for entry in rounds_dir.iterdir():
            if entry.is_dir() and entry.name.isdigit() and int(entry.name) >= 1:
                result.append((int(entry.name), entry))
    return sorted(result)


def board_pause_active(task: dict):
    """(paused, reason): board card or route interrupt pause.

    A missing board means the offline path; an unreadable board is conservative
    and blocks the script-driven continuation instead of guessing.
    """
    common = task.get("commonDir") if isinstance(task, dict) else None
    task_id = task.get("task") if isinstance(task, dict) else None
    if not isinstance(common, str) or not isinstance(task_id, str):
        return True, "task identity unavailable"
    board_path = Path(common) / "codex-pi" / "board.json"
    if not board_path.exists():
        return False, None
    from pi_store import read_board
    board,problem = read_board(board_path)
    if not isinstance(board, dict):
        return True, "board state is unreadable"
    card = (board.get("cards") or {}).get(task_id)
    if not isinstance(card, dict):
        return False, None
    policy = review_policy(card)
    if policy["takeoverRequired"]:
        return True, policy["instruction"]
    if card.get("paused"):
        return True, "board card is paused"
    thread = card.get("ownerThread")
    try:
        from pi_store import route_paused
        paused, problem = route_paused(thread) if isinstance(thread, str) else (False, None)
    except Exception as exc:  # noqa: BLE001 - conservative when pause state is unknown
        return True, f"route pause state unreadable: {type(exc).__name__}: {exc}"
    if problem is not None:
        return True, f"route pause state is {problem}"
    return bool(paused), "session route paused by interrupt" if paused else None


# ---------------------------------------------------------------------------
# brief and helper snapshot
# ---------------------------------------------------------------------------

def runtime_version() -> str:
    version_file = Path(__file__).resolve().parent / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
