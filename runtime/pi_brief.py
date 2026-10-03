"""Round inputs: the brief, the full worker contract text and ``worker.json`` for the
in-process worker extension.
"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import time
from pathlib import Path

from pi_core import (
    MAX_PROMPT_BYTES,
    READ_ONLY_TOOLS,
    SCHEMA_VERSION,
    WRITABLE_TOOLS,
    atomic,
    forbidden_checkouts,
)
from pi_phase import phase_path, read_phase_record, settle_quota_path


FORBIDDEN_RULE_TEXT = ("Do not reference the repository's main checkout or another worktree of this "
                       "repository in any tool call. The worker guard blocks such a call with rule "
                       "`forbidden-path`. Use only your round worktree.")
MUST_ASK_LINE = ("If the brief leaves a must-ask item open (see task packet), choose the conservative "
                 "reading. Report the open item as a spec gap.")


def compose_brief(task: dict, round_number: int, prompt: str, prior: dict | None) -> str:
    """The user prompt plus a fixed short header; the contract lives in the system prompt."""
    task_dir = Path(task["taskDir"])
    from pi_recovery import runtime_tools
    tools_dir = runtime_tools(task_dir)
    round_dir = task_dir / "rounds" / str(round_number)
    lines = ["---", "## Codex-Pi round header",
             f"task={task['task']} round={round_number}",
             "The worker contract is the system prompt section codex_pi_worker. It covers model "
             "policy, tools, boundaries and the phase contract. The same contract is also in "
             f"{round_dir / 'contract.md'}. It applies to every round of this session."]
    if round_number > 1:
        lines.append(f"Round-1 brief: {task_dir / 'rounds' / '1' / 'brief.md'}")
    lines += ["Use the native tools `check` for recorded checks, `progress` for self-reports, "
              "`readiness` for the mechanical delivery check, and `codemode` to batch independent "
              "reads and checks. Every nested call is guarded.",
              f'You may write only "{tools_dir}" and "{round_dir / "round.checks"}" outside '
              "the worktree.",
              FORBIDDEN_RULE_TEXT]
    if prior:
        lines.append(f"Previous round {prior.get('round')}: outcome={prior.get('state')} "
                     f"exit={prior.get('exitCode')} head={prior.get('endHead')}. That record is "
                     "execution evidence only; read its summary before continuing.")
    lines.append("End with a concise report. Never claim acceptance PASS.")
    return prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def check_timeout_seconds(task: dict, phase_record) -> float:
    """Per-command cap for ``check``: the phase contract's cap, else the project round timeout."""
    value = task["timeoutSeconds"]
    if isinstance(phase_record, dict):
        cap = (phase_record.get("contract") or {}).get("commandTimeoutSeconds")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) \
                and math.isfinite(float(cap)) and float(cap) > 0:
            value = cap
    return float(value)


def compose_contract(task: dict) -> str:
    """The worker contract injected into the system prompt and written to ``contract.md``."""
    read_only = bool(task["readOnly"])
    tools = READ_ONLY_TOOLS if read_only else WRITABLE_TOOLS
    task_dir = Path(task["taskDir"])
    from pi_recovery import runtime_tools
    tools_dir = runtime_tools(task_dir)
    lines = ["## Codex-Pi worker contract",
             "Do not execute the Codex CLI (`codex`). Do not launch any Codex agent. Do not "
             "call an OpenAI model through Codex. The Codex main session reviews outcomes. "
             "This Pi session implements and reports.",
             f"Model policy: this task is pinned to `{task['model']}`. Do not call, delegate "
             "to, or spawn any nested agent or model on another provider or model. Do not fall "
             "back automatically. If the pinned model is unavailable, stop and report it.",
             f"Mode: {'read-only' if read_only else 'writable'}. Allowed tools: {tools}. This "
             "is a worker guard. It is not a security sandbox and not an OS sandbox.",
             "Applicable AGENTS.md files discovered from the worktree remain authoritative. "
             "Prompt templates, skills and project extensions are disabled."]
    constraints = task.get("constraints") or []
    if constraints:
        lines += ["Project constraints (references; the runtime never executes them):"]
        lines += [f"  - {entry}" for entry in constraints]
    checks = task.get("checks") or []
    if checks:
        lines += ["Project checks (references only; never invent acceptance):"]
        lines += [f"  - {entry}" for entry in checks]
    phase_record, phase_problem = read_phase_record(task_dir)
    if isinstance(phase_record, dict):
        contract = phase_record.get("contract") or {}
        lines += ["## Phase contract (machine-readable snapshot; the main session remains the "
                  "design authority)",
                  f"phase_id={contract.get('phaseId')}",
                  f"contract_ref={phase_path(task_dir)}",
                  f"contract_sha256={phase_record.get('contractSha256')}",
                  f"baseline={contract.get('baseline')} (resolved {phase_record.get('baselineCommit')})",
                  f"design_ref={contract.get('designRef')} design_sha256={contract.get('designSha256')}",
                  f"phase_budget_seconds={contract.get('budgetSeconds')}",
                  f"scope={', '.join(contract.get('scope') or [])}"]
        if contract.get("commandTimeoutSeconds"):
            lines.append(f"command_timeout_seconds={contract.get('commandTimeoutSeconds')}")
        if contract.get("checkExecution"):
            execution = contract["checkExecution"]
            parts = []
            if execution.get("maxConcurrent") is not None:
                parts.append(f"max_concurrent={execution.get('maxConcurrent')}")
            if execution.get("cpuSlots") is not None:
                parts.append(f"cpu_slots={execution.get('cpuSlots')}")
            if execution.get("memoryMiB") is not None:
                parts.append(f"memory_mib={execution.get('memoryMiB')}")
            lines.append("check_execution: " + " ".join(parts)
                         + " (per-worker in-process permit pool; soft admission estimates, "
                           "not an OS hard limit)")
        for limit in contract.get("resourceLimits") or []:
            lines.append(f"resource_limit: path={limit.get('path')} max_bytes={limit.get('maxBytes')}")
        lines.append("acceptance_items:")
        for item in contract.get("acceptanceItems") or []:
            lines.append(f"  - id={item.get('id')} check_id={item.get('checkId')}"
                         + (f" min_run={item.get('minRun')}" if item.get('minRun') is not None else "")
                         + (" forbid_skip=true" if item.get("forbidSkip") else ""))
            lines.append(f"    command: {item.get('command')}")
            if item.get("targetedCommand"):
                lines.append(f"    targeted_command: {item.get('targetedCommand')} "
                             "(optional local repair suggestion; a targeted check never "
                             "substitutes for formal acceptance)")
            if item.get("estimatedSeconds") is not None:
                lines.append(f"    estimated_seconds: {item.get('estimatedSeconds')} "
                             "(planning estimate only; not a completion guarantee)")
            resources = item.get("checkResources")
            if resources:
                parts = [f"parallel_safe={bool(resources.get('parallelSafe'))}"]
                if resources.get("cpuSlots") is not None:
                    parts.append(f"cpu_slots={resources.get('cpuSlots')}")
                if resources.get("memoryMiB") is not None:
                    parts.append(f"memory_mib={resources.get('memoryMiB')}")
                if resources.get("exclusiveKeys"):
                    parts.append(f"exclusive_keys={','.join(resources.get('exclusiveKeys'))}")
                lines.append("    resources: " + " ".join(parts)
                             + " (soft pool estimate; a check with no declaration or "
                               "parallel_safe=false runs alone)")
            lines.append(f"    pass_condition: {item.get('passCondition')}")
            lines.append(f"    evidence: {item.get('evidence')}")
        lines.append("autonomous_repair:")
        lines += [f"  - {entry}" for entry in contract.get("autonomousRepair") or []]
        lines.append("escalate_when:")
        lines += [f"  - {entry}" for entry in contract.get("escalateWhen") or []]
        lines.append("Rules: stay inside the declared scope and the phase budget. Run every "
                     "acceptance check through the `check` tool with the item's exact command, "
                     "so the receipt binds the candidate. If an item declares targetedCommand, "
                     "run that command for local repair first, then commit the fixed candidate. "
                     "The item's formal command then needs final:true on the clean tree. A "
                     "targeted check never substitutes for formal acceptance. When the round "
                     "ends with only evidence missing, you may be asked once to continue in "
                     "this same session.")
    elif phase_problem not in (None, "absent"):
        lines.append(f"Phase contract state is {phase_problem}. Treat phase-wide readiness as "
                     "unknown. Report that state instead of inventing coverage.")
    lines += [
        "## Native tools",
        "- check(id, command, timeoutSeconds?, estimatedSeconds?, final?, watchPath?, maxBytes?): "
        "run a verification command through the task's frozen pi_check helper. The receipt "
        "binds the actual command, exit code, log hash, candidate HEAD and dirty state. A "
        "failing check keeps its receipt and returns the log tail. An admission refusal spawns "
        "no helper and writes no receipt. It returns a structured ok:false with receipt:null.",
        "- check budget: estimatedSeconds is a finite positive planning estimate; it is never a "
        "completion guarantee. The required duration is your call estimate when only it "
        "exists, the matching acceptance item's estimate when only it exists, or the larger "
        "of the two when both exist. When neither estimate exists, the required duration is "
        "the requested/contract cap. The effective window is the smallest applicable bound: "
        "the requested/contract cap, the remaining time until the real round deadline minus "
        "a fixed 60s wrap-up reserve, and, for a check inside codemode, the remaining time "
        "until the verifiable enclosing codemode deadline minus a small cleanup grace. A "
        "direct check has no codemode bound. The requirement must fit that window; otherwise "
        "the check is refused with structured requiredSeconds and allowedSeconds. The "
        "admitted helper timeout is that window itself and keeps fractional seconds. Never "
        "fabricate an estimate or lower one without evidence to fit a check, and never expand "
        "authorization to fit a check. Correct the estimate only when evidence supports it, "
        "or adjust timeoutSeconds only within the authorized cap to cover a trusted estimate. "
        "A long check is never admitted inside a shorter codemode script. Without a verifiable "
        "enclosing codemode deadline, run the check directly.",
        "- check final:true: a command whose argv equals a contract item that declares "
        "targetedCommand is a full acceptance check. It is refused unless final:true is "
        "passed on a clean worktree, and the response names the targeted command. "
        "targetedCommand is optional; commands matching other contract items do not need "
        "final:true. A targeted check never substitutes for formal acceptance. Run cheap "
        "targeted checks first and reserve time for the full checks and wrap-up.",
        "- progress(activity, step?, next?, blocker?, completedCriteria?, evidenceRefs?): "
        "record a short structured self-report. Progress is self-reported; it is never "
        "acceptance.",
        "- readiness(): read-only mechanical review of the contract against the recorded "
        "receipts. Readiness is evidence review; it is never acceptance.",
        "- codemode(code): run JavaScript in the QuickJS sandbox. A `tools.<name>(args)` call "
        "goes through the same guarded tools. Every nested call passes the same guard, and a "
        "blocked call rejects. Await every nested call. Batch only independent reads and "
        "checks with `Promise.allSettled`, and filter large output before it reaches you. A "
        "fulfilled Promise only means that the call resolved; inspect every structured "
        "result. A failed check still resolves with exit/timeout data, and resolve is not "
        "success. Parallel checks need independent resources and unique ids. Serialize "
        "dependent writes and commits. Only a successful script keeps its `store`/`load` "
        "values; completed calls are not undone. Codemode does not make the shell guard an OS "
        "sandbox.",
        "Every bash call has a finite timeout. The worker fills in a default, and a ceiling "
        "clamps larger requested values. Close fixtures with try/finally. A catch-and-print is "
        "not verification.",
        f'Only "{tools_dir}" and this round\'s checks directory may be written outside '
        "the worktree.",
        FORBIDDEN_RULE_TEXT,
        MUST_ASK_LINE,
        "End with a concise report of changes and evidence. Missing, failed, skipped and "
        "unknown evidence is not a pass. Never claim acceptance PASS. A zero exit code only "
        "proves that execution finished."]
    return "\n".join(lines) + "\n"


def write_brief(path: Path, text: str) -> str:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(path, 0o444)
    return hashlib.sha256(path.read_bytes()).hexdigest()


BASH_DEFAULT_TIMEOUT_SECONDS = 600.0
BASH_CEILING_SECONDS = 3600.0
WORKER_READY_FILE = "worker.ready"
WORKER_CONFIG_FILE = "worker.json"


def write_worker_config(task_dir: Path, round_number: int, task: dict, contract_text: str) -> Path:
    """Write ``rounds/N/worker.json`` for the in-process worker extension."""
    round_dir = task_dir / "rounds" / str(round_number)
    from pi_recovery import runtime_tools
    tools_dir = runtime_tools(task_dir)
    forbidden, allowed = forbidden_checkouts(Path(task["worktree"]), task_dir, round_number)
    allowed = [str(tools_dir) if root == str(task_dir / "tools") else root for root in allowed]
    phase_record, _problem = read_phase_record(task_dir)
    phase = isinstance(phase_record, dict)
    ceiling = BASH_CEILING_SECONDS
    if phase:
        cap = (phase_record.get("contract") or {}).get("commandTimeoutSeconds")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) \
                and math.isfinite(float(cap)) and float(cap) > 0:
            ceiling = float(cap)
    config = {
        "schemaVersion": 1, "task": task["task"], "round": round_number,
        "repo": task["repo"], "worktree": task["worktree"],
        "forbiddenRoots": forbidden, "allowedRoots": allowed,
        "bashDefaultTimeoutSeconds": min(BASH_DEFAULT_TIMEOUT_SECONDS, ceiling),
        "bashCeilingSeconds": ceiling,
        "checkTimeoutSeconds": check_timeout_seconds(task, phase_record),
        "checksDir": str(round_dir / "round.checks"), "toolsDir": str(tools_dir),
        "python": sys.executable, "phase": phase,
        "settleQuotaPath": str(settle_quota_path(
            task_dir, (phase_record.get("contract") or {}).get("phaseId"))) if phase else None,
        "roundDir": str(round_dir), "contract": contract_text,
        # New-round budget and targeted-repair metadata: absence keeps the older
        # frozen worker behavior, presence enables deadline admission in the
        # check tool. The real deadline is re-read from round.state.json.
        "deadlinePath": str(round_dir / "round.state.json"),
        "acceptanceItems": acceptance_items(phase_record),
    }
    # Present only when the phase declared it, so an older worker.json shape and
    # its hash stay unchanged; an absent pool means one check at a time.
    check_execution = (phase_record.get("contract") or {}).get("checkExecution") \
        if isinstance(phase_record, dict) else None
    if check_execution:
        config["checkExecution"] = dict(check_execution)
    path = round_dir / WORKER_CONFIG_FILE
    atomic(path, config)
    return path


def acceptance_items(phase_record) -> list:
    """Compact per-item check metadata for the worker's budget admission path."""
    items = []
    if isinstance(phase_record, dict):
        for item in (phase_record.get("contract") or {}).get("acceptanceItems") or []:
            entry = {"id": item.get("id"), "checkId": item.get("checkId"),
                     "command": item.get("command")}
            if item.get("targetedCommand"):
                entry["targetedCommand"] = item["targetedCommand"]
            if item.get("estimatedSeconds") is not None:
                entry["estimatedSeconds"] = float(item["estimatedSeconds"])
            resources = item.get("checkResources")
            if isinstance(resources, dict):
                compact = dict(resources)
                if isinstance(compact.get("exclusiveKeys"), list):
                    compact["exclusiveKeys"] = list(compact["exclusiveKeys"])
                entry["checkResources"] = compact
            items.append(entry)
    return items


def ensure_round_inputs(task_dir: Path, round_number: int, prompt: str, task: dict,
                        prior: dict | None, pi_version: str = "unknown") -> Path:
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_BYTES} bytes")
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    round_dir = task_dir / "rounds" / str(round_number)
    round_dir.mkdir(parents=True)
    (round_dir / "brief.md").parent.mkdir(parents=True, exist_ok=True)
    try:
        if (round_dir / "brief.md").exists():
            raise ValueError(f"round {round_number} already has a brief; never overwrite evidence")
        contract_text = compose_contract(task)
        digest = write_brief(round_dir / "brief.md", compose_brief(task, round_number, prompt, prior))
        write_brief(round_dir / "contract.md", contract_text)
        (round_dir / "round.checks").mkdir(exist_ok=True)
        write_worker_config(task_dir, round_number, task, contract_text)
    except FileExistsError:
        raise ValueError(f"round {round_number} already has a brief; never overwrite evidence") from None
    atomic(round_dir / "round.state.json",
           {"schemaVersion": SCHEMA_VERSION, "round": round_number, "state": "starting",
            "startedAt": time.time(), "exitCode": None, "timedOut": False, "cancelled": False,
            "briefSha256": digest, "taskDir": str(task_dir)})
    with (round_dir / "round.meta").open("a", encoding="utf-8") as meta:
        meta.write(f"task={task['task']} round={round_number} model={task['model']} "
                   f"thinking={task['thinking']}\nworktree={task['worktree']}\n"
                   f"start={time.time()}\nbrief_sha256={digest}\npi_version={pi_version}\n")
    return round_dir
