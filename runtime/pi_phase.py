#!/usr/bin/env python3
"""Phase contract validation shared by the Codex-Pi runtime helpers.

A phase contract is a small machine-readable snapshot of the main session's
authorized design for one complete, independently reviewable phase. It is
stored under the task evidence directory as ``phase.json`` (contract plus its
frozen digest) and referenced by the round brief. This module only validates
shape, required fields, reference hashes and version consistency. It never
judges semantic design quality, never executes the declared check commands and
never claims acceptance.

The same module also holds the phase run-time side: phase files and budget, structured
progress reads, declared resource limits, the readiness evidence snapshot, the
settle-continue view and the post-round classification. Check-receipt scanning is in
``pi_evidence``.

No Codex CLI or model is invoked here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

from pi_core import (
    ACTIVE_STATES,
    FULL_OID_RE,
    TERMINAL_STATES,
    atomic,
    board_pause_active,
    git,
    inside,
    lock_is_held,
    read_json,
    terminate,
)
from pi_evidence import _head_probe, _number, _read_bounded_json, _scan_checks, normalize_candidate
from pi_size import measure


PHASE_SCHEMA_VERSION = 1
PHASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_CONTRACT_BYTES = 262_144
MAX_TEXT = 4_000
MAX_LIST_TEXT = 1_000
MAX_ITEMS = 100
MAX_SCOPE = 100
MAX_LIMITS = 50
MAX_BUDGET_SECONDS = 2_592_000.0
MAX_COMMAND_TIMEOUT_SECONDS = 604_800.0
MAX_DESIGN_BYTES = 10_485_760

CONTRACT_KEYS = (
    "schemaVersion", "phaseId", "goal", "result", "baseline", "scope", "designRef",
    "designSha256", "acceptanceItems", "budgetSeconds", "commandTimeoutSeconds",
    "resourceLimits", "autonomousRepair", "escalateWhen", "preconditions", "nextPhaseRef",
)
ITEM_KEYS = ("id", "description", "checkId", "command", "passCondition", "evidence",
             "minRun", "forbidSkip", "targetedCommand", "estimatedSeconds")


def _text(value, limit: int, label: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"phase contract {label} must be a non-empty string")
    text = value.replace("\x00", " ").strip()
    if len(text) > limit:
        raise ValueError(f"phase contract {label} exceeds {limit} characters")
    return text


def _text_list(value, label: str, required: bool = True, limit: int = MAX_LIST_TEXT):
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"phase contract {label} must be a "
                         f"{'non-empty ' if required else ''}list of non-empty strings")
    result = []
    for entry in value:
        result.append(_text(entry, limit, f"{label} entry"))
    return result


def _safe_relative(value, label: str) -> str:
    text = _text(value, 500, label)
    if text.startswith("/") or "\\" in text:
        raise ValueError(f"phase contract {label} must be a relative POSIX path: {text!r}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts or not parts:
        raise ValueError(f"phase contract {label} uses traversal or is empty: {text!r}")
    return "/".join(parts)


def _safe_scope(value, label: str) -> str:
    text = _text(value, 500, label)
    if text == ".":
        return "."
    return _safe_relative(text, label)


def _inside(path: Path, root: Path) -> bool:
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def design_digest(root: Path, ref: str) -> str:
    """Digest the referenced design file, refusing traversal and oversized files."""
    relative = _safe_relative(ref, "designRef")
    candidate = Path(root).expanduser() / relative
    if not _inside(candidate, Path(root)):
        raise ValueError(f"phase contract designRef escapes the repository: {ref!r}")
    if not candidate.is_file():
        raise ValueError(f"phase contract designRef does not exist: {candidate}")
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise ValueError(f"phase contract designRef is unreadable: {candidate}: {exc}") from None
    if size > MAX_DESIGN_BYTES:
        raise ValueError(f"phase contract designRef exceeds {MAX_DESIGN_BYTES} bytes: {candidate}")
    return hashlib.sha256(candidate.read_bytes()).hexdigest()


def validate_contract(data, root, check_design: bool = True) -> dict:
    """Validate one contract object and return a normalized copy.

    Only machine-checkable shape/identity/version facts are enforced. Declared
    check commands are references; this module never runs them and a valid
    contract is never evidence of quality or acceptance.
    """
    if not isinstance(data, dict):
        raise ValueError("phase contract must be a JSON object")
    unknown = sorted(set(data) - set(CONTRACT_KEYS))
    if unknown:
        raise ValueError(f"phase contract has unsupported keys {unknown}; allowed: "
                         f"{list(CONTRACT_KEYS)}")
    if data.get("schemaVersion") != PHASE_SCHEMA_VERSION:
        raise ValueError(f"phase contract schemaVersion must be {PHASE_SCHEMA_VERSION}, "
                         f"got {data.get('schemaVersion')!r}")
    phase_id = data.get("phaseId")
    if not isinstance(phase_id, str) or not PHASE_ID_RE.fullmatch(phase_id):
        raise ValueError("phase contract phaseId must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    goal = _text(data.get("goal"), MAX_TEXT, "goal")
    result = _text(data.get("result"), MAX_TEXT, "result")
    baseline = _text(data.get("baseline"), 500, "baseline")
    scope = [_safe_scope(entry, "scope entry") for entry in _text_list(data.get("scope"), "scope")]
    if len(scope) > MAX_SCOPE:
        raise ValueError(f"phase contract scope exceeds {MAX_SCOPE} entries")
    design_ref = _safe_relative(data.get("designRef"), "designRef")
    design_sha = data.get("designSha256")
    if not isinstance(design_sha, str) or not SHA256_RE.fullmatch(design_sha):
        raise ValueError("phase contract designSha256 must be a 64-character lowercase hex digest")
    if check_design:
        actual = design_digest(Path(root), design_ref)
        if actual != design_sha:
            raise ValueError(f"phase contract designSha256 {design_sha} does not match the "
                             f"current design file digest {actual} for {design_ref}")
    items = data.get("acceptanceItems")
    if not isinstance(items, list) or not items:
        raise ValueError("phase contract acceptanceItems must be a non-empty list")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"phase contract acceptanceItems exceeds {MAX_ITEMS} entries")
    normalized_items = []
    seen_ids = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"phase contract acceptanceItems[{index}] must be an object")
        unknown_item = sorted(set(item) - set(ITEM_KEYS))
        if unknown_item:
            raise ValueError(f"phase contract acceptanceItems[{index}] has unsupported keys "
                             f"{unknown_item}; allowed: {list(ITEM_KEYS)}")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not PHASE_ID_RE.fullmatch(item_id):
            raise ValueError(f"phase contract acceptanceItems[{index}].id must match "
                             "[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
        if item_id in seen_ids:
            raise ValueError(f"phase contract acceptance item id {item_id!r} is duplicated")
        seen_ids.add(item_id)
        check_id = item.get("checkId") or item_id
        if not isinstance(check_id, str) or not PHASE_ID_RE.fullmatch(check_id):
            raise ValueError(f"phase contract acceptanceItems[{index}].checkId must match "
                             "[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
        min_run = item.get("minRun")
        if min_run is not None and (isinstance(min_run, bool) or not isinstance(min_run, int)
                                    or min_run < 0 or min_run > 10 ** 9):
            raise ValueError(f"phase contract acceptanceItems[{index}].minRun must be a "
                             "non-negative integer")
        forbid_skip = item.get("forbidSkip", False)
        if not isinstance(forbid_skip, bool):
            raise ValueError(f"phase contract acceptanceItems[{index}].forbidSkip must be a boolean")
        targeted = item.get("targetedCommand")
        if targeted is not None:
            targeted = _text(targeted, MAX_LIST_TEXT,
                             f"acceptanceItems[{index}].targetedCommand")
        estimate = item.get("estimatedSeconds")
        if estimate is not None:
            if isinstance(estimate, bool) or not isinstance(estimate, (int, float)) \
                    or not math.isfinite(float(estimate)) \
                    or not 0 < float(estimate) <= MAX_COMMAND_TIMEOUT_SECONDS:
                raise ValueError(f"phase contract acceptanceItems[{index}].estimatedSeconds must be "
                                 "a finite positive number at most "
                                 f"{int(MAX_COMMAND_TIMEOUT_SECONDS)}")
            estimate = float(estimate)
        normalized = {
            "id": item_id,
            "checkId": check_id,
            "description": _text(item.get("description"), MAX_TEXT,
                                 f"acceptanceItems[{index}].description"),
            "command": _text(item.get("command"), MAX_LIST_TEXT,
                             f"acceptanceItems[{index}].command"),
            "passCondition": _text(item.get("passCondition"), MAX_LIST_TEXT,
                                   f"acceptanceItems[{index}].passCondition"),
            "evidence": _text(item.get("evidence"), MAX_LIST_TEXT,
                              f"acceptanceItems[{index}].evidence"),
            "minRun": min_run,
            "forbidSkip": forbid_skip,
        }
        # Optional fields stay absent when undeclared so the canonical hash of an
        # older contract does not change under this runtime.
        if targeted is not None:
            normalized["targetedCommand"] = targeted
        if estimate is not None:
            normalized["estimatedSeconds"] = estimate
        normalized_items.append(normalized)
    # One normalized argv must never carry contradictory budget/targeted advice:
    # the check path matches commands by argv, not by id, so ambiguity is rejected
    # at contract freeze time instead of guessed at execution time.
    canonical_argv = {}
    for index, item in enumerate(normalized_items):
        try:
            argv = tuple(shlex.split(item["command"], posix=True))
        except ValueError:
            continue
        metadata = (item.get("targetedCommand"), item.get("estimatedSeconds"))
        previous = canonical_argv.get(argv)
        if previous is not None and previous[1] != metadata:
            raise ValueError(f"phase contract acceptanceItems[{previous[0]}] and "
                             f"acceptanceItems[{index}] normalize to the same command with "
                             "contradictory targetedCommand/estimatedSeconds metadata")
        canonical_argv[argv] = (index, metadata)
    budget = data.get("budgetSeconds")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) \
            or not math.isfinite(float(budget)) or not 0 < float(budget) <= MAX_BUDGET_SECONDS:
        raise ValueError(f"phase contract budgetSeconds must be positive and at most "
                         f"{int(MAX_BUDGET_SECONDS)}")
    command_timeout = data.get("commandTimeoutSeconds")
    if isinstance(command_timeout, bool) or not isinstance(command_timeout, (int, float)) \
            or not math.isfinite(float(command_timeout)) \
            or not 0 < float(command_timeout) <= MAX_COMMAND_TIMEOUT_SECONDS:
        raise ValueError("phase contract commandTimeoutSeconds must be present, positive and at "
                         f"most {int(MAX_COMMAND_TIMEOUT_SECONDS)}")
    limits = data.get("resourceLimits")
    if not isinstance(limits, list) or len(limits) > MAX_LIMITS:
        raise ValueError(f"phase contract resourceLimits must be declared as a list of at most "
                         f"{MAX_LIMITS} entries (an empty list explicitly declares none)")
    normalized_limits = []
    for index, entry in enumerate(limits):
        if not isinstance(entry, dict) or set(entry) != {"path", "maxBytes"}:
            raise ValueError(f"phase contract resourceLimits[{index}] must contain exactly "
                             "path and maxBytes")
        path = _text(entry.get("path"), 1_000, f"resourceLimits[{index}].path")
        max_bytes = entry.get("maxBytes")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError(f"phase contract resourceLimits[{index}].maxBytes must be a "
                             "positive integer")
        normalized_limits.append({"path": path, "maxBytes": max_bytes})
    next_phase = data.get("nextPhaseRef")
    if next_phase is not None:
        if not isinstance(next_phase, str) or not PHASE_ID_RE.fullmatch(next_phase):
            raise ValueError("phase contract nextPhaseRef must be a safe phase id")
    return {
        "schemaVersion": PHASE_SCHEMA_VERSION,
        "phaseId": phase_id,
        "goal": goal,
        "result": result,
        "baseline": baseline,
        "scope": scope,
        "designRef": design_ref,
        "designSha256": design_sha,
        "acceptanceItems": normalized_items,
        "budgetSeconds": float(budget),
        "commandTimeoutSeconds": float(command_timeout),
        "resourceLimits": normalized_limits,
        "autonomousRepair": _text_list(data.get("autonomousRepair"), "autonomousRepair"),
        "escalateWhen": _text_list(data.get("escalateWhen"), "escalateWhen"),
        "preconditions": _text_list(data.get("preconditions"), "preconditions", required=False),
        "nextPhaseRef": next_phase,
    }


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def contract_hash(contract: dict) -> str:
    return hashlib.sha256(canonical_json(contract)).hexdigest()


def load_contract(path) -> dict:
    """Bounded JSON read; returns the raw object, not a validated contract."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"phase contract file is unreadable: {path}: {exc}") from None
    if len(raw) > MAX_CONTRACT_BYTES:
        raise ValueError(f"phase contract file exceeds {MAX_CONTRACT_BYTES} bytes: {path}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ValueError(f"phase contract file is not valid JSON: {path}: {exc}") from None
    return data


def contract_view(contract: dict, limit: int = 20) -> dict:
    """Compact bounded projection for status/board output."""
    items = contract.get("acceptanceItems") or []
    return {
        "schemaVersion": contract.get("schemaVersion"),
        "phaseId": contract.get("phaseId"),
        "goal": contract.get("goal"),
        "result": contract.get("result"),
        "baseline": contract.get("baseline"),
        "scope": (contract.get("scope") or [])[:50],
        "designRef": contract.get("designRef"),
        "designSha256": contract.get("designSha256"),
        "budgetSeconds": contract.get("budgetSeconds"),
        "commandTimeoutSeconds": contract.get("commandTimeoutSeconds"),
        "resourceLimits": (contract.get("resourceLimits") or [])[:20],
        "autonomousRepair": (contract.get("autonomousRepair") or [])[:20],
        "escalateWhen": (contract.get("escalateWhen") or [])[:20],
        "nextPhaseRef": contract.get("nextPhaseRef"),
        "acceptanceItems": [{
            "id": item.get("id"), "checkId": item.get("checkId"),
            "description": item.get("description"), "command": item.get("command"),
            "passCondition": item.get("passCondition"), "evidence": item.get("evidence"),
            "minRun": item.get("minRun"), "forbidSkip": item.get("forbidSkip"),
            "targetedCommand": item.get("targetedCommand"),
            "estimatedSeconds": item.get("estimatedSeconds"),
        } for item in items[:limit]],
        "acceptanceItemCount": len(items),
    }


PHASE_FILE = "phase.json"
PHASE_STATE_FILE = "phase.state.json"
PROGRESS_FILE = "progress.json"
PROGRESS_LOCK = ".progress.lock"
PROGRESS_ACTIVITIES = ("implementing", "checking", "repairing", "blocked")
READINESS_FILE = "readiness.json"
MAX_PROGRESS_BYTES = 65_536
MAX_PROGRESS_TEXT = 500
MAX_PROGRESS_ITEMS = 100
MAX_PROGRESS_REFS = 20
MAX_READINESS_ITEMS = 100
MAX_VERIFY_LOG_BYTES = 33_554_432
MAX_VERIFY_TOTAL_BYTES = 67_108_864
MAX_SCOPE_DIFF_FILES = 600
MIN_SETTLE_SECONDS = 60.0
RESOURCE_STATE_FILE = "resource.state.json"
RESOURCE_SCAN_SECONDS = 15.0
RESOURCE_SCAN_BUDGET_SECONDS = 10.0
RESOURCE_MAX_SECONDS_PER_LIMIT = 5.0
RESOURCE_MAX_LIMIT_ENTRIES = 64
RESOURCE_UNKNOWN_SECONDS = 120.0


# ---------------------------------------------------------------------------
# phase contract, structured progress, readiness and the settle-continue view
# ---------------------------------------------------------------------------

def phase_path(task_dir: Path) -> Path:
    return task_dir / PHASE_FILE


def phase_state_path(task_dir: Path) -> Path:
    return task_dir / PHASE_STATE_FILE


def read_phase_record(task_dir: Path):
    """Read the frozen phase contract record; returns (record, problem).

    The persisted normalized contract must hash to the stored digest, and the
    phase-state anchor, when present, must name the same revision. Malformed or
    contradictory records are rejected here so no consumer projects a weaker
    contract under the same apparent identity.
    """
    path = phase_path(task_dir)
    if not path.exists():
        return None, "absent"
    data = read_json(path, None)
    if not isinstance(data, dict) or data.get("schemaVersion") != 1 \
            or not isinstance(data.get("contract"), dict) \
            or not isinstance(data.get("contractSha256"), str) \
            or not FULL_OID_RE.fullmatch(data["contractSha256"]):
        return None, "unrecognized"
    try:
        actual = contract_hash(data["contract"])
    except (TypeError, ValueError):
        return None, "malformed-contract"
    if actual != data["contractSha256"]:
        return None, "digest-mismatch"
    state = read_phase_state(task_dir)
    state_sha = state.get("contractSha256") if isinstance(state, dict) else None
    if isinstance(state_sha, str) and state_sha != data["contractSha256"]:
        return None, "state-mismatch"
    return data, None


def write_phase_record(task_dir: Path, record: dict) -> None:
    atomic(phase_path(task_dir), record)


def read_phase_state(task_dir: Path) -> dict:
    data = read_json(phase_state_path(task_dir), None)
    return data if isinstance(data, dict) else {}


def write_phase_state(task_dir: Path, state: dict) -> None:
    atomic(phase_state_path(task_dir), state)


def _resolve_baseline(worktree: Path, baseline: str) -> str:
    try:
        resolved = git(worktree, "rev-parse", "--verify", "--quiet", f"{baseline}^{{commit}}")
    except subprocess.CalledProcessError:
        raise ValueError(f"phase contract baseline {baseline!r} is not a commit in the worktree") from None
    if not FULL_OID_RE.fullmatch(resolved):
        raise ValueError(f"phase contract baseline {baseline!r} resolved to an unexpected object id")
    return resolved.lower()


def _limit_target(worktree: Path, declared: str):
    """Resolve one declared resource path inside the worktree without symlinks.

    Returns ``(absolute_path, None)`` or ``(None, problem)``. Traversal and any
    symlink component are rejected so a later no-follow measurement cannot be
    redirected outside the task worktree.
    """
    if not isinstance(declared, str) or not declared.strip():
        return None, "resource limit path is empty"
    base = Path(os.path.normpath(str(Path(worktree).resolve())))
    candidate = Path(os.path.expanduser(declared.strip()))
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = Path(os.path.normpath(str(candidate)))
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        return None, f"resource limit path {declared!r} is outside the task worktree"
    if ".." in relative.parts:
        return None, f"resource limit path {declared!r} escapes the task worktree"
    current = base
    for part in relative.parts:
        current = current / part
        if os.path.islink(current):
            return None, f"resource limit path {declared!r} uses a symlink component"
    return candidate, None


def validate_resource_limits(contract: dict, worktree: Path) -> None:
    """Reject a phase contract whose declared resource paths can leave the worktree."""
    for index, limit in enumerate(contract.get("resourceLimits") or []):
        _target, problem = _limit_target(worktree, limit.get("path"))
        if problem is not None:
            raise ValueError(f"phase contract resourceLimits[{index}]: {problem}")


def _resource_scan_seconds() -> float:
    raw = os.environ.get("CODEX_PI_RESOURCE_SCAN_SECONDS")
    try:
        value = float(raw) if raw else RESOURCE_SCAN_SECONDS
    except (TypeError, ValueError):
        value = RESOURCE_SCAN_SECONDS
    return value if 0.05 <= value <= 60.0 else RESOURCE_SCAN_SECONDS


def _resource_unknown_seconds() -> float:
    raw = os.environ.get("CODEX_PI_RESOURCE_UNKNOWN_SECONDS")
    try:
        value = float(raw) if raw else RESOURCE_UNKNOWN_SECONDS
    except (TypeError, ValueError):
        value = RESOURCE_UNKNOWN_SECONDS
    return value if 0.1 <= value <= 1800.0 else RESOURCE_UNKNOWN_SECONDS


def _resource_scan_budget_seconds() -> float:
    raw = os.environ.get("CODEX_PI_RESOURCE_SCAN_BUDGET_SECONDS")
    try:
        value = float(raw) if raw else RESOURCE_SCAN_BUDGET_SECONDS
    except (TypeError, ValueError):
        value = RESOURCE_SCAN_BUDGET_SECONDS
    return value if 0.05 <= value <= 60.0 else RESOURCE_SCAN_BUDGET_SECONDS


def _resource_limits_signature(limits) -> str:
    """Canonical digest of the declared paths and caps for evidence binding."""
    canonical = []
    for entry in limits or []:
        if not isinstance(entry, dict):
            continue
        canonical.append({"path": entry.get("path"), "maxBytes": entry.get("maxBytes")})
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _sanitize_resource_state(value, path_limit: int = 400):
    """Bound one persisted resource observation for status/readiness consumers."""
    if not isinstance(value, dict):
        return None
    allowed_status = ("none", "ok", "unknown", "breached", "escalated")
    status = value.get("status")
    if not isinstance(status, str) or status not in allowed_status:
        status = "unknown"
    reason = value.get("reason")
    write_error = value.get("writeError")
    round_value = value.get("round")
    out = {"schemaVersion": 1, "status": status,
           "reason": reason[:300] if isinstance(reason, str) else None,
           "updatedAt": _number(value.get("updatedAt")),
           "finalScannedAt": _number(value.get("finalScannedAt")),
           "round": round_value if isinstance(round_value, int)
           and not isinstance(round_value, bool) and 0 <= round_value <= 10 ** 9 else None,
           "phaseId": value.get("phaseId") if isinstance(value.get("phaseId"), str) else None,
           "contractSha256": value.get("contractSha256")
           if isinstance(value.get("contractSha256"), str)
           and FULL_OID_RE.fullmatch(value["contractSha256"]) else None,
           "limitsSignature": value.get("limitsSignature")
           if isinstance(value.get("limitsSignature"), str)
           and FULL_OID_RE.fullmatch(value["limitsSignature"]) else None,
           "writeError": write_error[:300] if isinstance(write_error, str) else None,
           "limits": []}
    limits = value.get("limits")
    if isinstance(limits, list):
        for entry in limits[:RESOURCE_MAX_LIMIT_ENTRIES]:
            if not isinstance(entry, dict):
                continue
            observed = entry.get("observedBytes")
            max_bytes = entry.get("maxBytes")
            scans = entry.get("scans")
            out["limits"].append({
                "declared": entry.get("declared")[:path_limit]
                if isinstance(entry.get("declared"), str) else None,
                "path": entry.get("path")[:path_limit]
                if isinstance(entry.get("path"), str) else None,
                "maxBytes": max_bytes if isinstance(max_bytes, int)
                and not isinstance(max_bytes, bool) and max_bytes > 0 else None,
                "observedBytes": observed if isinstance(observed, int)
                and not isinstance(observed, bool) and observed >= 0 else None,
                "breached": bool(entry.get("breached")),
                "breachBasis": entry.get("breachBasis")
                if entry.get("breachBasis") in ("complete", "partial lower bound") else None,
                "unknown": bool(entry.get("unknown")),
                "escalated": bool(entry.get("escalated")),
                "complete": entry.get("complete") if isinstance(entry.get("complete"), bool) else None,
                "scans": scans if isinstance(scans, int) and not isinstance(scans, bool)
                and 0 <= scans <= 10 ** 9 else None,
                "reason": entry.get("reason")[:300]
                if isinstance(entry.get("reason"), str) else None,
            })
    return out


def evaluate_resource_evidence(round_dir: Path, contract: dict, round_number: int,
                               contract_sha):
    """The single fail-closed verdict for declared phase resource limits.

    Returns ``None`` only when the contract intentionally declares no limits.
    Missing, corrupt, stale, contradictory or final-incomplete evidence is
    ``unknown``; the effective status is recomputed from the final per-limit
    observations so a stale or hand-edited summary cannot force a pass.
    """
    limits = contract.get("resourceLimits") or []
    if not limits:
        return None
    expected_signature = _resource_limits_signature(limits)
    path = round_dir / RESOURCE_STATE_FILE
    state = None
    problem = None
    if not path.is_file():
        problem = "declared resource limits have no persisted observation"
    else:
        state = _sanitize_resource_state(read_json(path, None))
        if state is None:
            problem = "persisted resource observation is malformed"
    if problem is None:
        if state.get("round") != round_number:
            problem = "persisted resource observation belongs to another round"
        elif state.get("phaseId") != contract.get("phaseId"):
            problem = "persisted resource observation belongs to another phase"
        elif state.get("contractSha256") != contract_sha:
            problem = "persisted resource observation belongs to another contract revision"
        elif state.get("limitsSignature") != expected_signature:
            problem = "persisted resource observation does not match the declared limits"
        elif state.get("finalScannedAt") is None:
            problem = "declared resource limits have no completed final observation"
    if problem is not None:
        return {"status": "unknown", "reason": problem,
                "limits": (state or {}).get("limits") or [],
                "finalScannedAt": (state or {}).get("finalScannedAt"),
                "updatedAt": (state or {}).get("updatedAt"),
                "writeError": (state or {}).get("writeError")}
    declared = [{"path": entry.get("path"), "maxBytes": entry.get("maxBytes")}
                for entry in limits]
    persisted = state.get("limits") or []
    if len(persisted) != len(declared):
        return {"status": "unknown",
                "reason": "persisted resource observation has a different limit count",
                "limits": persisted, "finalScannedAt": state.get("finalScannedAt"),
                "updatedAt": state.get("updatedAt"), "writeError": state.get("writeError")}
    breached = escalated = unknown = False
    reason = None
    for index, expected in enumerate(declared):
        entry = persisted[index]
        if entry.get("declared") != expected["path"] \
                or entry.get("maxBytes") != expected["maxBytes"]:
            return {"status": "unknown",
                    "reason": "persisted resource observation contradicts the declared limits",
                    "limits": persisted, "finalScannedAt": state.get("finalScannedAt"),
                    "updatedAt": state.get("updatedAt"), "writeError": state.get("writeError")}
        observed = entry.get("observedBytes")
        if entry.get("breached") or (isinstance(observed, int)
                                     and isinstance(expected["maxBytes"], int)
                                     and observed > expected["maxBytes"]):
            breached = True
            reason = reason or entry.get("reason")
        elif entry.get("escalated"):
            escalated = True
            reason = reason or entry.get("reason")
        elif entry.get("unknown") or entry.get("complete") is not True:
            unknown = True
            reason = reason or entry.get("reason")
        elif not isinstance(observed, int) or isinstance(observed, bool) or observed < 0:
            unknown = True
            reason = reason or "completed resource observation has no valid measured byte count"
        elif not isinstance(entry.get("scans"), int) or entry.get("scans", 0) < 1:
            unknown = True
            reason = reason or "completed resource observation has no scan evidence"
    effective = ("breached" if breached else "escalated" if escalated
                 else "unknown" if unknown else "ok")
    if state.get("status") != effective:
        return {"status": "unknown",
                "reason": "persisted resource status contradicts its limit evidence",
                "limits": persisted, "finalScannedAt": state.get("finalScannedAt"),
                "updatedAt": state.get("updatedAt"), "writeError": state.get("writeError")}
    return {"status": effective, "reason": reason or state.get("reason"),
            "limits": persisted, "finalScannedAt": state.get("finalScannedAt"),
            "updatedAt": state.get("updatedAt"), "writeError": state.get("writeError"),
            "limitsSignature": expected_signature}


class PhaseResourceMonitor:
    """Durable per-round observer for the contract's declared resource limits.

    Measurements are bounded and never follow symlinks. A known overage,
    including a partial lower bound already over the cap, or an unknown
    measurement that persists past the escalation window returns a stop reason;
    the supervisor then stops only its own Pi process group. Incomplete or
    unreadable evidence stays unknown and is never treated as under budget.
    """

    def __init__(self, round_dir: Path, worktree: Path, limits: list,
                 phase_id=None, contract_sha256=None, round_number=None,
                 unknown_seconds: float = RESOURCE_UNKNOWN_SECONDS,
                 scan_budget_seconds: float | None = None):
        self.worktree = Path(worktree)
        self.unknown_seconds = float(unknown_seconds)
        self.scan_budget_seconds = float(scan_budget_seconds
                                         or _resource_scan_budget_seconds())
        self.path = round_dir / RESOURCE_STATE_FILE
        self._stop_reason = None
        self._write_error = None
        entries = []
        for limit in limits:
            declared = limit.get("path") if isinstance(limit, dict) else None
            max_bytes = limit.get("maxBytes") if isinstance(limit, dict) else None
            target, problem = _limit_target(self.worktree, declared)
            entries.append({
                "declared": declared,
                "path": str(target) if target is not None else None,
                "maxBytes": max_bytes, "observedBytes": None, "breached": False,
                "breachBasis": None, "unknown": False, "unknownSince": None,
                "escalated": False, "scans": 0, "unknownScans": 0,
                "complete": None, "reason": problem,
            })
        self._state = {"schemaVersion": 1, "phaseId": phase_id,
                       "contractSha256": contract_sha256, "round": round_number,
                       "limitsSignature": _resource_limits_signature(limits),
                       "status": "ok" if entries else "none", "reason": None,
                       "updatedAt": time.time(), "finalScannedAt": None,
                       "writeError": None, "limits": entries}
        self._write()

    def _write(self) -> bool:
        try:
            atomic(self.path, self._state)
            self._write_error = None
            self._state["writeError"] = None
            return True
        except OSError as exc:
            self._write_error = f"{type(exc).__name__}: {exc}"[:300]
            self._state["writeError"] = self._write_error
            return False

    def _mark_unknown(self, entry: dict, now: float, reason: str) -> None:
        entry["unknown"] = True
        entry["complete"] = False
        entry["unknownScans"] = (entry.get("unknownScans") or 0) + 1
        if entry.get("unknownSince") is None:
            entry["unknownSince"] = now
        if not entry.get("breached"):
            entry["reason"] = reason
        if now - entry["unknownSince"] >= self.unknown_seconds:
            entry["escalated"] = True
            entry["reason"] = (
                f"resource measurement stayed unknown for "
                f"{now - entry['unknownSince']:.1f}s; the declared cap cannot be verified")

    def scan(self, final: bool = False):
        now = time.time()
        started = time.monotonic()
        for entry in self._state["limits"]:
            remaining = self.scan_budget_seconds - (time.monotonic() - started)
            if remaining <= 0.05:
                self._mark_unknown(entry, now,
                                   "not visited within the bounded resource scan budget")
                continue
            entry["scans"] += 1
            target, problem = _limit_target(self.worktree, entry.get("declared"))
            if problem is not None:
                entry["path"] = None
                self._mark_unknown(entry, now, problem)
                continue
            entry["path"] = str(target)
            try:
                measurement = measure(
                    target, max_seconds=max(0.05,
                                            min(RESOURCE_MAX_SECONDS_PER_LIMIT, remaining)))
            except Exception as exc:  # noqa: BLE001 - unknown must never strand the round
                self._mark_unknown(
                    entry, now, f"measurement failed: {type(exc).__name__}: {exc}"[:300])
                continue
            observed = measurement.get("bytes")
            if isinstance(observed, int) and not isinstance(observed, bool):
                if entry.get("observedBytes") is None or observed > entry["observedBytes"]:
                    entry["observedBytes"] = observed
                if observed > entry["maxBytes"]:
                    entry["breached"] = True
                    entry["breachBasis"] = ("complete" if measurement.get("complete")
                                            else "partial lower bound")
                    entry["reason"] = (
                        f"observed at least {observed} bytes under {entry['declared']} exceed "
                        f"the declared max_bytes {entry['maxBytes']}"
                        + ("" if measurement.get("complete") else " (partial lower bound)"))
            missing = (not measurement.get("exists")
                       and measurement.get("reason") == "path does not exist")
            unknown = bool(measurement.get("unknown")) and not missing
            if missing and entry.get("observedBytes") is None:
                # A declared path that does not exist yet is a known zero, not
                # an unmeasured cap.
                entry["observedBytes"] = 0
            if unknown:
                self._mark_unknown(
                    entry, now, measurement.get("reason") or "measurement is incomplete")
            else:
                entry["unknown"] = False
                entry["unknownSince"] = None
                entry["complete"] = bool(measurement.get("complete")) or missing
                if not entry.get("breached") and not entry.get("escalated"):
                    entry["reason"] = None
            if entry.get("breached"):
                # A known overage must stop the owned Pi group promptly; later
                # declarations keep their previous (or unknown) evidence.
                self._summarize(now)
                if final:
                    self._state["finalScannedAt"] = now
                self._write()
                return self._stop_reason
        self._summarize(now)
        if final:
            self._state["finalScannedAt"] = now
        self._write()
        return self._stop_reason

    def _summarize(self, now: float) -> None:
        limits = self._state["limits"]
        if not limits:
            self._state.update(status="none", reason=None)
        elif any(entry.get("breached") for entry in limits):
            self._state["status"] = "breached"
        elif any(entry.get("escalated") for entry in limits):
            self._state["status"] = "escalated"
        elif any(entry.get("unknown") for entry in limits):
            self._state["status"] = "unknown"
        else:
            self._state["status"] = "ok"
        self._state["updatedAt"] = now
        reasons = [entry.get("reason") for entry in limits if entry.get("reason")]
        self._state["reason"] = reasons[0] if reasons else None
        if self._stop_reason is None:
            if self._state["status"] == "breached":
                self._stop_reason = (self._state["reason"]
                                     or "declared phase resource limit exceeded")
            elif self._state["status"] == "escalated":
                self._stop_reason = (self._state["reason"]
                                     or "declared phase resource measurement is unknown")

    def state(self) -> dict:
        return self._state

    def final_scan(self):
        return self.scan(final=True)

    def snapshot(self) -> dict:
        return _sanitize_resource_state(self._state) or {}


def install_phase_contract(task_dir: Path, raw, root: Path, worktree: Path,
                           prior: dict | None = None) -> dict:
    """Validate, freeze and record one phase contract plus its budget anchor.

    ``prior`` is the existing phase record when this is a revision/repair. A
    new phaseId starts a fresh budget anchor; a same-phase revision keeps the
    original start time (the total phase budget is never silently reset).
    """
    contract = validate_contract(raw, root)
    validate_resource_limits(contract, worktree)
    digest = contract_hash(contract)
    record = {"schemaVersion": 1, "contract": contract, "contractSha256": digest,
              "baselineCommit": _resolve_baseline(worktree, contract["baseline"]),
              "createdAt": time.time()}
    write_phase_record(task_dir, record)
    previous = read_phase_state(task_dir)
    same_phase = isinstance(prior, dict) and isinstance(prior.get("contract"), dict) \
        and prior["contract"].get("phaseId") == contract["phaseId"]
    if same_phase and isinstance(previous.get("startedAt"), (int, float)):
        started = previous["startedAt"]
    else:
        started = record["createdAt"]
    state = {"schemaVersion": 1, "phaseId": contract["phaseId"],
             "contractSha256": digest, "startedAt": started,
             "budgetSeconds": contract["budgetSeconds"],
             "deadlineAt": started + contract["budgetSeconds"],
             "rounds": previous.get("rounds") if same_phase and isinstance(previous.get("rounds"), list) else [],
             "updatedAt": time.time()}
    write_phase_state(task_dir, state)
    return record


def phase_budget(task_dir: Path, record: dict | None = None) -> dict:
    """Bounded phase budget projection; unknown values stay None, never zero."""
    state = read_phase_state(task_dir)
    now = time.time()
    started = state.get("startedAt")
    budget = state.get("budgetSeconds")
    deadline = state.get("deadlineAt")
    source = "phase.state"
    if not (isinstance(started, (int, float)) and not isinstance(started, bool)
            and isinstance(budget, (int, float)) and not isinstance(budget, bool)
            and budget > 0):
        if isinstance(record, dict) and isinstance(record.get("contract"), dict):
            started = record.get("createdAt")
            budget = record["contract"].get("budgetSeconds")
            deadline = started + budget if isinstance(started, (int, float)) \
                and isinstance(budget, (int, float)) else None
            source = "phase.json fallback"
        else:
            return {"startedAt": None, "budgetSeconds": None, "deadlineAt": None,
                    "remainingSeconds": None, "exhausted": None, "source": "none"}
    if not isinstance(deadline, (int, float)):
        deadline = started + budget
    remaining = deadline - now
    return {"startedAt": started, "budgetSeconds": budget, "deadlineAt": deadline,
            "remainingSeconds": max(0.0, remaining), "exhausted": remaining <= 0,
            "source": source, "now": now}


def _progress_text(value, label: str, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"progress {label} must be a string")
    text = value.replace("\x00", " ").strip()
    if len(text) > MAX_PROGRESS_TEXT:
        raise ValueError(f"progress {label} exceeds {MAX_PROGRESS_TEXT} characters")
    if required and not text:
        raise ValueError(f"progress {label} must not be empty")
    return text


def read_progress(round_dir: Path):
    path = round_dir / PROGRESS_FILE
    if not path.exists():
        return None
    data, problem = _read_bounded_json(path, MAX_PROGRESS_BYTES)
    if problem is not None or not isinstance(data, dict) or data.get("schemaVersion") != 1:
        return None
    return {
        "schemaVersion": 1, "task": data.get("task"), "phaseId": data.get("phaseId"),
        "contractSha256": data.get("contractSha256"), "round": data.get("round"),
        "briefSha256": data.get("briefSha256"), "step": data.get("step"),
        "activity": data.get("activity"), "completedCriteria": data.get("completedCriteria") or [],
        "next": data.get("next"), "blocker": data.get("blocker"),
        "evidenceRefs": data.get("evidenceRefs") or [], "updatedAt": data.get("updatedAt"),
        "updateCount": data.get("updateCount"), "reportedBy": data.get("reportedBy", "pi"),
        "verified": False,
    }


def evaluate_count_rules(spec: dict, counts):
    """Conjunction of every declared count rule for one acceptance item.

    Returns ``(status, reason)`` with status None when all declared rules pass.
    Missing counts/fields are unknown, skip>0 is skipped and run<minRun is
    failed; declaring two rules never skips either one.
    """
    if spec.get("forbidSkip"):
        if not isinstance(counts, dict) or "skip" not in counts:
            return ("unknown", "skip-freedom cannot be verified: the receipt has no parseable "
                               "test counts (declare forbidSkip only for count-emitting runners)")
        if counts.get("skip"):
            return ("skipped", f"test counts report {counts.get('skip')} skips")
    if spec.get("minRun") is not None:
        if not isinstance(counts, dict) or "run" not in counts:
            return ("unknown", "minRun cannot be verified: the receipt has no parseable test "
                               "counts")
        if counts.get("run", 0) < spec["minRun"]:
            return ("failed", f"test counts report fewer than minRun={spec['minRun']} runs")
    return (None, None)


def verify_receipt_log(checks_dir: Path, item: dict, budget: dict | None = None):
    """True/False/None log hash verification with a bounded read; None stays unknown.

    ``budget`` is an optional per-snapshot byte allowance: once it would be
    exceeded the result is None and ``budget['exceeded']`` is set, so the caller
    can fail closed instead of hashing unbounded logs.
    """
    resolved = checks_dir.resolve()
    log_path = checks_dir / item["log"]
    if item.get("_safe_log") is not None:
        log_path = item["_safe_log"]
    if not inside(log_path, resolved):
        return None
    try:
        size = log_path.stat().st_size
    except OSError:
        return None
    if size > MAX_VERIFY_LOG_BYTES:
        return None
    if budget is not None:
        used = budget.get("bytesUsed", 0)
        if used + size > MAX_VERIFY_TOTAL_BYTES:
            budget["exceeded"] = True
            return None
        budget["bytesUsed"] = used + size
    digest = hashlib.sha256()
    try:
        with log_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest() == item["logSha256"]


def _scope_allows(path: str, scope) -> bool:
    for entry in scope or []:
        if entry == ".":
            return True
        if path == entry or path.startswith(entry + "/"):
            return True
    return False


def _stop_bounded_process(proc: subprocess.Popen) -> None:
    """Stop an owned diff reader without failing on an already-reaped leader.

    Darwin can raise EPERM from ``killpg`` when the group leader is a zombie;
    reaping the child and re-checking the group keeps this bounded stop safe
    for the scope guard and other short-lived readers.
    """
    if proc.poll() is not None:
        proc.wait()
        return
    try:
        terminate(proc)
    except PermissionError:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()


def _scope_check(task_dir: Path, record: dict, candidate: str | None) -> dict:
    result = {"status": "unknown", "baselineCommit": record.get("baselineCommit"),
              "changedFiles": [], "outOfScope": [], "reason": None}
    baseline = record.get("baselineCommit")
    if not isinstance(baseline, str) or candidate is None:
        result["reason"] = "baseline or candidate unknown"
        return result
    task = read_json(task_dir / "task.json", {}) or {}
    worktree = Path(task.get("worktree", "."))
    argv = ["git", "-C", str(worktree), "diff", "--name-only", f"{baseline}..{candidate}"]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    except OSError as exc:
        result["reason"] = f"git diff unavailable: {exc}"
        return result
    files = []
    overflow = threading.Event()

    def reader():
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                path = line.strip()
                if not path:
                    continue
                if len(files) >= MAX_SCOPE_DIFF_FILES:
                    overflow.set()
                    continue
                files.append(path)
        except (OSError, ValueError):
            overflow.set()

    thread = threading.Thread(target=reader, daemon=True)

    def close_pipe() -> None:
        stream = proc.stdout
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    try:
        thread.start()
        thread.join(timeout=10)
        if thread.is_alive():
            _stop_bounded_process(proc)
            result["reason"] = "git diff did not finish within the bounded timeout"
            return result
        if overflow.is_set():
            _stop_bounded_process(proc)
            result.update({
                "changedFiles": files[:100], "status": "unknown",
                "reason": f"the change set exceeds the bounded scope check "
                          f"(>= {MAX_SCOPE_DIFF_FILES} files); scope is unknown, so readiness is blocked "
                          "instead of checking only a sorted prefix"})
            return result
        try:
            returncode = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _stop_bounded_process(proc)
            result["reason"] = "git diff did not finish within the bounded timeout"
            return result
        if returncode != 0:
            result["reason"] = f"git diff exited {returncode}"
            return result
        scope = (record.get("contract") or {}).get("scope") or []
        out = [path for path in files if not _scope_allows(path, scope)]
        result.update({"changedFiles": files[:100], "outOfScope": out[:100],
                       "status": "violation" if out else "ok"})
        return result
    finally:
        close_pipe()


def evaluate_execution_gate(state: dict):
    """Execution facts for one round: ``(ok, status, reason)``.

    Only a normally completed round with ``exitCode == 0``,
    ``cancelled is False`` and ``timedOut is False`` is ``ok``. Known abnormal
    outcomes are ``failed``; missing or contradictory values are ``unknown``.
    This is a single gate consumed by the normalized readiness snapshot.
    """
    raw_state = state.get("state")
    exit_code = state.get("exitCode")
    cancelled = state.get("cancelled")
    timed_out = state.get("timedOut")
    if raw_state != "completed":
        if raw_state in TERMINAL_STATES:
            return (False, "failed",
                    f"round ended as {raw_state}; only a normally completed round can be "
                    "delivery-ready")
        return False, "not_terminal", f"round state {raw_state!r} is not terminal"
    if timed_out is True:
        return False, "failed", "round wrapper timed out"
    if cancelled is True:
        return False, "failed", "round was cancelled"
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return False, "unknown", "round exit code is missing or contradictory"
    if exit_code != 0:
        return False, "failed", f"round exited {exit_code}"
    if timed_out is not False or cancelled is not False:
        return False, "unknown", "round cancelled/timed_out flags are missing or contradictory"
    return True, "ok", None


def _receipt_contract_violation(spec: dict, contract: dict, item: dict):
    """Validate one receipt against the item's declared command and timeout cap.

    Returns ``(status, reason)`` when the receipt cannot cover the item: a known
    command or timeout mismatch is ``failed``; missing, malformed or contradictory
    identity/timing evidence stays ``unknown``. ``(None, None)`` means both the
    command identity and the wrapper timing bound were verified.
    """
    declared = spec.get("command")
    if not isinstance(declared, str) or not declared.strip():
        return ("unknown", "acceptance item command is missing or malformed")
    try:
        expected = shlex.split(declared, posix=True)
    except ValueError:
        return ("unknown", "acceptance item command cannot be parsed")
    got = item.get("argv")
    if got is None:
        return ("unknown", "receipt has no parseable argv identity")
    if got != expected:
        return ("failed", "receipt argv does not match the declared acceptance command")
    limit = contract.get("commandTimeoutSeconds")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) \
            or not math.isfinite(float(limit)) or float(limit) <= 0:
        return ("unknown", "contract command timeout is missing or malformed")
    limit = float(limit)
    started = item.get("startedAt")
    deadline = item.get("deadlineAt")
    if started is None or deadline is None:
        return ("unknown", "receipt has no complete wrapper timing evidence")
    if deadline < started:
        return ("unknown", "receipt wrapper timing is contradictory")
    wrapper = deadline - started
    if wrapper > limit + 1e-6:
        return ("failed", f"receipt wrapper deadline {wrapper:g}s exceeds the declared command "
                          f"timeout {limit:g}s")
    return (None, None)


def evaluate_acceptance_items(contract: dict, candidate_head, checks: dict, checks_dir: Path):
    """The single receipt-validity evaluation shared by every consumer.

    Returns ``(items, coverage, gaps, budget)``. Each item status is one of
    ``covered``/``failed``/``skipped``/``missing``/``unknown``; ``covered`` means
    every declared rule passed *and* the candidate-bound receipt log hash was
    verified. Receipt metadata alone is never a verified result.
    """
    receipts = checks.get("receipts") or {}
    parsed = receipts.get("recent") or []
    by_id = {}
    for item in parsed:
        if isinstance(item, dict):
            by_id.setdefault(item.get("id"), []).append(item)
    budget = {"bytesUsed": 0, "exceeded": False}
    items = []
    coverage = {"required": 0, "covered": 0, "failed": 0, "missing": 0, "unknown": 0, "skipped": 0}
    for spec in contract.get("acceptanceItems") or []:
        check_id = spec.get("checkId") or spec.get("id")
        attempts = sorted(by_id.get(check_id, []),
                          key=lambda item: item.get("endedAt") or item.get("startedAt") or 0)
        entry = {"id": spec.get("id"), "checkId": check_id, "status": "missing",
                 "reason": "no receipt for this check id", "receiptRef": None, "logRef": None,
                 "attempts": len(attempts), "logVerified": None,
                 "evidenceLevel": "missing"}
        applicable = [attempt for attempt in attempts
                      if candidate_head is not None and attempt.get("head") == candidate_head
                      and attempt.get("dirty") is False]
        if attempts and not applicable:
            newest = attempts[-1]
            reason = "receipts are not bound to the candidate commit"
            if newest.get("dirty"):
                reason = "newest attempt ran with a dirty tree and cannot bind content"
            elif newest.get("head") is None:
                reason = "newest attempt has no recorded commit"
            entry["reason"] = reason
            entry["receiptRef"] = str(checks_dir / newest["receipt"])
            entry["logRef"] = str(checks_dir / newest["log"])
            entry["evidenceLevel"] = "receipt_metadata_only"
        elif applicable:
            newest = applicable[-1]
            entry["receiptRef"] = str(checks_dir / newest["receipt"])
            entry["logRef"] = str(checks_dir / newest["log"])
            entry["evidenceLevel"] = "receipt_metadata"
            counts = newest.get("testCounts")
            entry["exitCode"] = newest.get("exitCode")
            entry["testCounts"] = counts if isinstance(counts, dict) else None
            contract_status, contract_reason = _receipt_contract_violation(spec, contract, newest)
            if contract_status is not None:
                entry.update(status=contract_status, reason=contract_reason,
                             evidenceLevel="receipt_metadata_only")
            elif newest.get("timedOut"):
                entry.update(status="failed", reason="check timed out")
            elif newest.get("cancelled"):
                entry.update(status="failed", reason="check was cancelled")
            elif newest.get("exitCode") is None:
                entry.update(status="unknown", reason="receipt has no exit code")
            elif newest.get("exitCode") != 0:
                entry.update(status="failed", reason=f"exit {newest.get('exitCode')}")
            elif counts and counts.get("fail"):
                entry.update(status="failed",
                             reason=f"test counts report {counts.get('fail')} failures")
            else:
                count_status, count_reason = evaluate_count_rules(spec, counts)
                if count_status is not None:
                    entry.update(status=count_status, reason=count_reason)
                else:
                    verified = verify_receipt_log(checks_dir, newest, budget)
                    entry["logVerified"] = verified
                    if verified is True:
                        entry.update(status="covered",
                                     reason="candidate-bound receipt with verified log hash and "
                                            "satisfied count rules",
                                     evidenceLevel="verified_result")
                    elif budget.get("exceeded"):
                        entry.update(status="unknown",
                                     reason="log verification budget exceeded; the result stays "
                                            "unverified")
                    elif verified is False:
                        entry.update(status="unknown",
                                     reason="log hash mismatch; the result is not verified")
                    else:
                        entry.update(status="unknown",
                                     reason="log missing, unreadable or oversized; the result is "
                                            "not verified")
        items.append(entry)
        coverage[entry["status"]] = coverage.get(entry["status"], 0) + 1
    coverage["required"] = len(items)
    gaps = [{"id": item["id"], "checkId": item["checkId"], "status": item["status"],
             "reason": item["reason"], "receiptRef": item["receiptRef"]}
            for item in items if item["status"] != "covered"]
    return items[:MAX_READINESS_ITEMS], coverage, gaps[:MAX_READINESS_ITEMS], budget


def build_phase_snapshot(task_dir: Path, task: dict, round_number: int, *, state: dict,
                         checks: dict, candidate: dict) -> dict:
    """One normalized phase evidence snapshot consumed by board, events,
    readiness and the accept gate.

    It carries the candidate identity, per-item evidence grades, scope facts,
    execution facts and the conjunctive mechanical delivery readiness. It never
    judges design quality and never claims GPT acceptance.
    """
    record, problem = read_phase_record(task_dir)
    if not isinstance(record, dict):
        return {"schemaVersion": 1, "round": round_number, "status": "no_contract",
                "reason": problem, "candidate": candidate}
    contract = record.get("contract") or {}
    round_dir = task_dir / "rounds" / str(round_number)
    checks_dir = Path(checks.get("dir") or (round_dir / "round.checks"))
    from pi_execution import effective_execution
    effective,execution_evidence=effective_execution(state,round_dir/'round.jsonl')
    state=dict(state,state=effective,executionEvidence=execution_evidence)
    raw_state = state.get("state")
    candidate_head = candidate.get("head") if candidate.get("status") == "known" else None
    items, coverage, gaps, budget = evaluate_acceptance_items(contract, candidate_head,
                                                              checks, checks_dir)
    scope = _scope_check(task_dir, record, candidate_head)
    writer_free = (not lock_is_held(task_dir / ".task.lock")
                   and not lock_is_held(task_dir / ".supervisor.lock"))
    progress_record = read_progress(round_dir)
    resource = evaluate_resource_evidence(round_dir, contract, round_number,
                                          record.get("contractSha256"))
    resource_status = (resource or {}).get("status")
    receipts = checks.get("receipts") or {}
    scan_partial = bool(checks.get("partial") or receipts.get("truncated")
                        or receipts.get("partial"))
    notes = []
    execution_ok, execution_status, execution_reason = evaluate_execution_gate(state)
    status, reason = "unknown", None
    if candidate.get("status") != "known":
        status, reason = "unknown", candidate.get("reason")
    elif resource_status in ("breached", "escalated"):
        status = "not_ready"
        reason = (f"declared phase resource limit is {resource_status}: "
                  f"{(resource or {}).get('reason') or 'resource evidence is not under budget'}")
    elif execution_status != "ok":
        status = "not_ready" if execution_status in ("failed", "not_terminal") else "unknown"
        reason = execution_reason
    elif scope.get("status") == "violation":
        status, reason = "not_ready", "files outside the declared phase scope"
    elif any(item["status"] in ("failed", "skipped", "unknown", "missing") for item in items):
        status, reason = "not_ready", "required acceptance evidence is not fully covered"
    elif scope.get("status") == "unknown":
        status, reason = "unknown", scope.get("reason") or "scope check is unknown"
    elif resource_status == "unknown":
        status = "not_ready"
        reason = ("declared phase resource measurement is unknown: "
                  f"{(resource or {}).get('reason') or 'an incomplete measurement is never under budget'}")
    elif scan_partial:
        status, reason = "unknown", "the bounded check scan was partial or truncated"
    else:
        status, reason = "ready", "all required evidence is covered for the candidate"
    if resource_status in ("breached", "escalated", "unknown"):
        notes.append(f"phase resource evidence is {resource_status}: "
                     f"{(resource or {}).get('reason') or 'not verified under budget'}")
    if execution_status != "ok":
        notes.append(f"execution gate: {execution_reason}")
    if scope.get("status") == "violation":
        gaps.append({"id": "scope", "checkId": None, "status": "violation",
                     "reason": "files outside the declared phase scope: "
                     + ", ".join(scope.get("outOfScope") or [])[:300], "receiptRef": None})
    ready_for_review = status == "ready" and writer_free
    if status == "ready" and not writer_free:
        notes.append("coverage is complete but a writer lock is still held; the final supervisor "
                     "tick clears the lock before main review")
    readiness = {"status": status, "reason": reason, "coverage": coverage, "gaps": gaps,
                 "scope": scope.get("status"), "writerFree": writer_free,
                 "readyForReview": ready_for_review, "generatedAt": time.time(),
                 "checkedItems": len(items),
                 "resource": {"status": resource_status,
                              "reason": (resource or {}).get("reason")},
                 "execution": {"ok": execution_ok, "status": execution_status,
                               "reason": execution_reason}}
    return {
        "schemaVersion": 1,
        "round": round_number,
        "phaseId": contract.get("phaseId"),
        "contractSha256": record.get("contractSha256"),
        "candidate": candidate,
        "execution": {"state": raw_state, "exitCode": state.get("exitCode"),
                      "cancelled": state.get("cancelled") if isinstance(
                          state.get("cancelled"), bool) else None,
                      "timedOut": state.get("timedOut") if isinstance(
                          state.get("timedOut"), bool) else None,
                      "terminal": raw_state in TERMINAL_STATES,
                      "completed": execution_ok,
                      "gate": {"ok": execution_ok, "status": execution_status,
                               "reason": execution_reason}},
        "checksDir": str(checks_dir),
        "scanPartial": scan_partial,
        "resource": resource,
        "verificationBudget": budget,
        "items": items,
        "coverage": coverage,
        "gaps": gaps,
        "scope": scope,
        "readiness": readiness,
        "evidenceLevels": {
            "piSelfReport": {"present": progress_record is not None,
                             "activity": (progress_record or {}).get("activity"),
                             "verified": False},
            "processActivity": {"verified": False,
                                "note": "process/file activity is never evidence"},
            "receiptMetadata": {"total": receipts.get("total"),
                                "scanned": receipts.get("scanned"),
                                "failedAttempts": receipts.get("failedAttempts"),
                                "verified": False},
            "verifiedResults": {"items": [item["id"] for item in items
                                          if item["status"] == "covered"],
                                "count": coverage.get("covered", 0)},
            "deliveryReadiness": status,
            "gptAcceptance": False,
        },
        "notes": notes,
        "acceptance": "not_verified",
        "computedAt": time.time(),
    }


def build_readiness(task_dir: Path, task: dict, round_number: int) -> dict:
    """Readiness view built from the same normalized snapshot the board uses."""
    round_dir = task_dir / "rounds" / str(round_number)
    state = read_json(round_dir / "round.state.json", {}) or {}
    from pi_execution import effective_execution
    from pi_summary import read_meta
    writer_free = (not lock_is_held(task_dir / ".task.lock")
                   and not lock_is_held(task_dir / ".supervisor.lock"))
    effective, execution_evidence = effective_execution(
        state, round_dir / "round.jsonl",
        terminal_meta=read_meta(round_dir / "round.meta") if writer_free else None)
    state = dict(state, state=effective, executionEvidence=execution_evidence)
    raw_state = state.get("state")
    current_head, head_problem = None, None
    if raw_state in ACTIVE_STATES:
        worktree = task.get("worktree")
        if isinstance(worktree, str) and worktree.strip():
            current_head, head_problem = _head_probe(Path(worktree))
        else:
            head_problem = "no frozen worktree"
    candidate = normalize_candidate({"state": raw_state,
                                     "endHead": state.get("endHead") or state.get("head"),
                                     "currentHead": current_head})
    checks = _scan_checks(round_dir / "round.checks")
    snapshot = build_phase_snapshot(task_dir, task, round_number, state=state, checks=checks,
                                    candidate=candidate)
    if snapshot.get("status") == "no_contract":
        writer_free = (not lock_is_held(task_dir / ".task.lock")
                       and not lock_is_held(task_dir / ".supervisor.lock"))
        return {"schemaVersion": 1, "command": "readiness", "task": task.get("task"),
                "round": round_number, "phaseId": None, "contractSha256": None,
                "candidate": None, "candidateSource": candidate,
                "status": "no_contract",
                "execution": {"state": raw_state, "exitCode": state.get("exitCode"),
                              "timedOut": bool(state.get("timedOut")),
                              "cancelled": bool(state.get("cancelled"))},
                "items": [], "gaps": [],
                "coverage": {"required": 0, "covered": 0, "failed": 0, "missing": 0,
                             "unknown": 0, "skipped": 0},
                "scope": {"status": "unknown", "baselineCommit": None, "changedFiles": [],
                          "outOfScope": [], "reason": "no phase contract"},
                "budget": phase_budget(task_dir), "writerFree": writer_free,
                "checksDir": str(round_dir / "round.checks"), "acceptance": "not_verified",
                "generatedAt": time.time(), "readyForReview": False,
                "readinessReason": snapshot.get("reason"),
                "evidenceLevels": {"gptAcceptance": False},
                "settle": {"eligible": False, "reason": "no_contract", "missing": []},
                "notes": [f"no phase contract is installed ({snapshot.get('reason')}); "
                          "brief-only review applies"]}
    result = {
        "schemaVersion": 1, "command": "readiness", "task": task.get("task"),
        "round": round_number, "phaseId": snapshot.get("phaseId"),
        "contractSha256": snapshot.get("contractSha256"),
        "candidate": snapshot["candidate"].get("head"),
        "candidateSource": snapshot["candidate"],
        "status": snapshot["readiness"]["status"],
        "execution": snapshot["execution"],
        "items": snapshot["items"], "gaps": snapshot["gaps"], "coverage": snapshot["coverage"],
        "scope": snapshot["scope"], "budget": phase_budget(task_dir),
        "resource": snapshot.get("resource"),
        "writerFree": snapshot["readiness"]["writerFree"],
        "checksDir": snapshot["checksDir"], "acceptance": "not_verified",
        "generatedAt": snapshot["readiness"]["generatedAt"],
        "readyForReview": snapshot["readiness"]["readyForReview"],
        "readinessReason": snapshot["readiness"]["reason"],
        "evidenceLevels": snapshot["evidenceLevels"],
        "snapshot": snapshot,
        "notes": list(snapshot["notes"]) + [
            "readiness is built from the same normalized snapshot the board consumes; it is a "
            "mechanical delivery check, never acceptance"],
    }
    if head_problem is not None:
        result["notes"].append(f"active HEAD probe failed ({head_problem}); the candidate is "
                               "unknown")
    result["settle"] = settle_view(task, task_dir, result)
    return result


def classify_readiness(readiness: dict, record: dict) -> dict:
    """Post-round classification of one normally completed round (facts only, no side effects)."""
    contract = record.get("contract") or {}
    base = {"phaseId": contract.get("phaseId"), "contractSha256": record.get("contractSha256")}
    if readiness.get("status") == "ready":
        return {**base, "action": "review", "reason": "ready"}
    resource = readiness.get("resource") or {}
    if resource.get("status") == "breached":
        return {**base, "action": "escalate", "reason": "resource_breached"}
    if resource.get("status") in ("unknown", "escalated"):
        return {**base, "action": "escalate", "reason": "resource_unknown"}
    if (readiness.get("scope") or {}).get("status") == "violation":
        return {**base, "action": "escalate", "reason": "scope_violation"}
    statuses = {item.get("status") for item in readiness.get("items") or []}
    if statuses & {"failed", "skipped", "unknown"}:
        return {**base, "action": "escalate", "reason": "required_check_failed"}
    if "missing" in statuses:
        return {**base, "action": "escalate", "reason": "missing_checks"}
    return {**base, "action": "escalate", "reason": "unknown_delivery_state"}


def settle_view(task: dict, task_dir: Path, readiness: dict) -> dict:
    """Whether the worker extension may continue once at settle time.

    Eligible only when every gap is missing evidence (no failure, skip or unknown),
    scope and resources are fine, budget remains, no pause is active and the
    one-per-phase quota file does not exist yet. The extension claims the quota.
    """
    def no(reason: str) -> dict:
        return {"eligible": False, "reason": reason, "missing": []}

    phase_id = readiness.get("phaseId")
    if not phase_id:
        return no("no_contract")
    resource = readiness.get("resource") or {}
    if resource.get("status") in ("breached", "unknown", "escalated"):
        return no("resource_" + str(resource.get("status")))
    if (readiness.get("scope") or {}).get("status") == "violation":
        return no("scope_violation")
    items = readiness.get("items") or []
    if any(item.get("status") in ("failed", "skipped", "unknown") for item in items):
        return no("required_check_not_missing")
    missing = [item for item in items if item.get("status") == "missing"]
    if not missing:
        return no("nothing_missing")
    remaining = (readiness.get("budget") or {}).get("remainingSeconds")
    if remaining is None or remaining < MIN_SETTLE_SECONDS:
        return no("budget_exhausted")
    paused, _reason = board_pause_active(task)
    if paused:
        return no("paused")
    if settle_quota_path(task_dir, phase_id).exists():
        return no("quota_used")
    record, _problem = read_phase_record(task_dir)
    specs = {entry.get("id"): entry for entry in
             ((record or {}).get("contract") or {}).get("acceptanceItems") or []}
    return {"eligible": True, "reason": "missing_checks",
            "missing": [{"id": item.get("id"),
                         "command": (specs.get(item.get("id")) or {}).get("command"),
                         "passCondition": (specs.get(item.get("id")) or {}).get("passCondition")}
                        for item in missing[:20]]}


def _phase_acceptance_on_board(main_task: dict, record: dict, latest_head):
    """Read the board decision authority for the current phase (read-only)."""
    common = main_task.get("commonDir")
    task_id = main_task.get("task")
    contract = record.get("contract") or {}
    board_path = Path(common) / "codex-pi" / "board.json" if isinstance(common, str) else None
    if board_path is None or not board_path.exists():
        return None, "board state is missing; the phase gate needs the recorded decision authority"
    from pi_store import read_board
    board,_problem = read_board(board_path)
    if not isinstance(board, dict):
        return None, "board state is unreadable; refusing to cross phases"
    card = (board.get("cards") or {}).get(task_id)
    if not isinstance(card, dict):
        return None, f"task {task_id!r} is not registered on the board; refusing to cross phases"
    info = card.get("phase") if isinstance(card.get("phase"), dict) else {}
    if info.get("phaseId") != contract.get("phaseId"):
        return None, "board phase identity does not match the installed contract"
    if info.get("contractHash") != record.get("contractSha256"):
        return None, "board contract revision does not match the installed contract"
    if info.get("status") != "accepted":
        return None, f"previous phase status is {info.get('status')!r}, not accepted"
    accepted_head = info.get("acceptedHead")
    if not isinstance(latest_head, str) or accepted_head != latest_head.lower():
        return None, ("the previous phase was accepted for a different candidate "
                      f"({accepted_head!r} != {latest_head!r})")
    return info, None


def settle_quota_path(task_dir: Path, phase_id) -> Path:
    """One-shot settle-continue claim file for a phase (created exclusively by the extension)."""
    digest = hashlib.sha256(str(phase_id).encode("utf-8")).hexdigest()[:24]
    return task_dir / "settle" / f"{digest}.claim"
