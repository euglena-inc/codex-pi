#!/usr/bin/env python3
"""Compare private, normalized Codex-Pi and Sol-Luna run records offline."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROUTES = ("codex-pi", "sol-luna")
OUTCOMES = {"accepted", "rejected", "blocked", "incomplete"}
GROUP_FIELDS = ("project", "pair", "case", "baseline", "acceptanceKey")
SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
COUNT_FIELDS = ("inputTokens", "cachedInputTokens", "outputTokens")
METRIC_FIELDS = ("input", "uncachedInput", "output", "total")
MAX_COUNTER = (1 << 63) - 1


class RecordError(ValueError):
    """An input record is invalid; its private contents are never included."""


class DuplicateJsonKey(ValueError):
    """Duplicate object keys make a JSON record ambiguous."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey
        result[key] = value
    return result


def _nonempty_string(record: dict[str, Any], field: str, label: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{label}: {field} must be a nonempty string")
    return value


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def validate_record(value: Any, label: str) -> tuple[tuple[str, ...], dict[str, Any]]:
    if not isinstance(value, dict):
        raise RecordError(f"{label}: record must be a JSON object")

    version = value.get("schemaVersion")
    if type(version) is not int or version != 1:
        raise RecordError(f"{label}: schemaVersion must be integer 1")

    identity = tuple(_nonempty_string(value, key, label) for key in GROUP_FIELDS)
    if not _is_sha(value["baseline"]):
        raise RecordError(f"{label}: baseline must be a 40- or 64-character lowercase hexadecimal SHA")

    route = _nonempty_string(value, "route", label)
    if route not in ROUTES:
        raise RecordError(f"{label}: route must be codex-pi or sol-luna")

    outcome = _nonempty_string(value, "outcome", label)
    if outcome not in OUTCOMES:
        raise RecordError(f"{label}: outcome must be accepted, rejected, blocked, or incomplete")

    verified = value.get("modelVerified")
    if type(verified) is not bool:
        raise RecordError(f"{label}: modelVerified must be a boolean")

    candidate = value.get("candidate")
    if outcome == "accepted" and not _is_sha(candidate):
        raise RecordError(f"{label}: accepted outcome requires a 40- or 64-character lowercase candidate SHA")
    if candidate is not None and not _is_sha(candidate):
        raise RecordError(f"{label}: candidate must be null or a 40- or 64-character lowercase SHA")

    if "solUsage" not in value:
        raise RecordError(f"{label}: solUsage is required (use null when unknown)")
    usage = value["solUsage"]
    if usage is not None:
        if not isinstance(usage, dict):
            raise RecordError(f"{label}: solUsage must be null or an object")
        complete = usage.get("complete")
        if type(complete) is not bool:
            raise RecordError(f"{label}: solUsage.complete must be a boolean")
        basis = usage.get("basis")
        if basis is not None and (not isinstance(basis, str) or not basis.strip()):
            raise RecordError(f"{label}: solUsage.basis must be a nonempty string when supplied")
        if complete and basis != "exclusive-requests":
            raise RecordError(f"{label}: complete solUsage requires basis 'exclusive-requests'")
        for field in COUNT_FIELDS:
            if field not in usage:
                continue
            count = usage[field]
            if type(count) is not int or count < 0:
                raise RecordError(f"{label}: solUsage.{field} must be a nonnegative integer")
            if count > MAX_COUNTER:
                raise RecordError(
                    f"{label}: solUsage.{field} must be a nonnegative integer no greater than "
                    f"{MAX_COUNTER} (int64 maximum)")
        cached = usage.get("cachedInputTokens")
        if cached is not None:
            if "inputTokens" not in usage:
                raise RecordError(f"{label}: solUsage.cachedInputTokens requires inputTokens to check its upper bound")
            if cached > usage["inputTokens"]:
                raise RecordError(f"{label}: solUsage.cachedInputTokens cannot exceed inputTokens")

    return identity, value


def _strict_json_constant(_: str) -> None:
    raise ValueError("non-standard JSON constant")


def read_records(paths: list[str]) -> dict[tuple[str, ...], dict[str, dict[str, Any]]]:
    groups: dict[tuple[str, ...], dict[str, dict[str, Any]]] = {}
    for file_index, file_name in enumerate(paths, start=1):
        try:
            data = json.loads(Path(file_name).read_text(encoding="utf-8"),
                              parse_constant=_strict_json_constant,
                              object_pairs_hook=_object_without_duplicate_keys)
        except DuplicateJsonKey:
            raise RecordError(f"input {file_index}: duplicate JSON object key is not allowed") from None
        except json.JSONDecodeError as exc:
            raise RecordError(f"input {file_index}: invalid JSON at line {exc.lineno}, column {exc.colno}") from None
        except ValueError:
            raise RecordError(f"input {file_index}: invalid JSON") from None
        except (OSError, UnicodeError, RecursionError):
            raise RecordError(f"input {file_index}: cannot read valid UTF-8 JSON") from None

        records = data if isinstance(data, list) else [data]
        for record_index, record in enumerate(records, start=1):
            label = f"input {file_index} record {record_index}"
            identity, valid = validate_record(record, label)
            route = valid["route"]
            route_rows = groups.setdefault(identity, {})
            if route in route_rows:
                raise RecordError(f"{label}: duplicate {route} record for the same comparison group")
            route_rows[route] = valid
    return groups


def _route_view(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {
            "status": "missing",
            "reason": ["route record is missing"],
            "outcome": None,
            "modelVerified": None,
            "usage": {
                "status": "unknown",
                "basis": None,
                "inputTokens": None,
                "cachedInputTokens": None,
                "uncachedInputTokens": None,
                "outputTokens": None,
                "totalTokens": None,
            },
        }

    reasons: list[str] = []
    if record["outcome"] != "accepted":
        reasons.append(f"outcome is {record['outcome']}")
    if not record["modelVerified"]:
        reasons.append("model is unverified")

    raw_usage = record["solUsage"]
    if raw_usage is None:
        reasons.append("solUsage is unknown")
        usage_status = "unknown"
        basis = None
        input_tokens = cached_tokens = output_tokens = None
    else:
        usage_status = "complete" if raw_usage["complete"] else "incomplete"
        basis = raw_usage.get("basis")
        input_tokens = raw_usage.get("inputTokens")
        cached_tokens = raw_usage.get("cachedInputTokens")
        output_tokens = raw_usage.get("outputTokens")
        if not raw_usage["complete"]:
            reasons.append("solUsage is incomplete")
        elif basis != "exclusive-requests":
            # Validation currently makes this unreachable, keeping eligibility explicit.
            reasons.append("solUsage is not exclusive to this run")
        if input_tokens is None:
            reasons.append("inputTokens is unknown")
        if output_tokens is None:
            reasons.append("outputTokens is unknown")

    uncached_tokens = (input_tokens - cached_tokens
                       if input_tokens is not None and cached_tokens is not None else None)
    total_tokens = (input_tokens + output_tokens
                    if input_tokens is not None and output_tokens is not None else None)
    eligible = not reasons
    return {
        "status": "eligible" if eligible else "ineligible",
        "reason": reasons or None,
        "outcome": record["outcome"],
        "modelVerified": record["modelVerified"],
        "usage": {
            "status": usage_status,
            "basis": basis,
            "inputTokens": input_tokens,
            "cachedInputTokens": cached_tokens,
            "uncachedInputTokens": uncached_tokens,
            "outputTokens": output_tokens,
            "totalTokens": total_tokens,
        },
    }


def _savings(pi_value: int | None, luna_value: int | None) -> tuple[float | None, str | None]:
    if pi_value is None or luna_value is None:
        return None, "metric is unknown for one or both routes"
    if pi_value == 0:
        return None, "codex-pi baseline for this metric is zero"
    return 100 * (pi_value - luna_value) / pi_value, None


def compare_group(identity: tuple[str, ...], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    project, pair, case, baseline, acceptance_key = identity
    routes = {route: _route_view(records.get(route)) for route in ROUTES}
    route_reasons = []
    for route in ROUTES:
        view = routes[route]
        if view["status"] != "eligible":
            route_reasons.extend(f"{route}: {reason}" for reason in view["reason"] or [])

    comparable = not route_reasons
    metric_reasons: dict[str, str] = {}
    savings: dict[str, float | None] = {metric: None for metric in METRIC_FIELDS}
    if comparable:
        pi_metrics = routes["codex-pi"]["usage"]
        luna_metrics = routes["sol-luna"]["usage"]
        for metric, field in (("input", "inputTokens"), ("uncachedInput", "uncachedInputTokens"),
                              ("output", "outputTokens"), ("total", "totalTokens")):
            value, reason = _savings(pi_metrics[field], luna_metrics[field])
            savings[metric] = value
            if reason:
                metric_reasons[metric] = reason

    return {
        "project": project,
        "pair": pair,
        "case": case,
        "baseline": baseline,
        "acceptanceKey": acceptance_key,
        "routes": routes,
        "comparison": {
            "comparable": comparable,
            "reason": route_reasons or None,
            "savingsPercent": savings,
            "metricReasons": metric_reasons,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", metavar="JSON_FILE",
                        help="private JSON file containing one record or an array of records")
    args = parser.parse_args(argv)
    try:
        groups = read_records(args.files)
    except RecordError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    lines = [json.dumps(compare_group(identity, records), sort_keys=True, separators=(",", ":"))
             for identity, records in sorted(groups.items())]
    if lines:
        sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
