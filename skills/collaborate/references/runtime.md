# Runtime commands

Usage only. Rules, model policy and review boundaries are in [the Skill](../SKILL.md); event handling is in [handoff](handoff.md). Replace example paths and the owner UUID before running; never interpolate user text into shell commands. All commands print one line of compact JSON.

```sh
python3 /abs/plugin/runtime/pi_task.py project --repo /abs/repo
python3 /abs/plugin/runtime/pi_task.py start --repo /abs/repo --task TASK-1 --worktree /abs/wt --prompt-file /abs/brief.md [--contract-file /abs/phase.json] [--read-only]
python3 /abs/plugin/runtime/pi_board.py register --repo /abs/repo --task TASK-1 --thread OWNER_UUID --transport cli-queue --title 'Task title' --goal 'Reviewable result'
```

Start, then register immediately (registration also catches an already finished task). The default transport is `offline`; `cli-queue` needs a local Codex CLI with `queue`, the desktop app and the exact owner task UUID (`--codex-bin /abs/codex` picks an executable). Never guess an owner or start another process to deliver. `PI_BIN` selects the Pi executable. Project config `.agents/codex-pi.json`: schemaVersion 1, `model`, `thinking`, `constraints`, `checks`, `maxWorkers`, `timeoutSeconds`; use an isolated worktree, reserved for the task's lifetime.

## Reading state

```sh
python3 .../pi_task.py status --repo REPO --task TASK [--round N]   # fast read-only snapshot
python3 .../pi_task.py result --repo REPO --task TASK [--round N] [--full]
python3 .../pi_board.py show --repo REPO --task TASK               # board card, pending events, queue
```

`result` is compact by default: state, execution, candidate head, usage totals, check lines (`ID exit=N run=.. pass=.. fail=.. skip=..`, or `ID missing`/`ID unknown`), Pi's final text (at most 1200 characters) and notes. `--full` returns the previous complete shape (per-round evidence paths, `summary`, `summaryText`). Collect `result` once per terminal round; raw logs, receipts and the native session stay under the Git common directory `codex-pi/tasks/`. `show` gives the absolute evidence paths a delivery card leaves out.

## Continue, cancel, upgrade

```sh
python3 .../pi_task.py continue --repo REPO --task TASK --prompt-file /abs/repair.md [--contract-file /abs/phase.json]
python3 .../pi_task.py cancel --repo REPO --task TASK
python3 .../pi_task.py upgrade --repo REPO --task TASK
```

`continue` preserves the pinned session and worktree in a new immutable round; a paused task refuses it until explicit resume. `cancel` signals only the owned worker group; verify it ended before repair. `upgrade` is described in [handoff](handoff.md).

## Phase contracts

A complete phase is frozen with `--contract-file` (schema and operations: [phase autonomy validation](../../../docs/validation/phase-autonomy-o1-20260926.md)): goal, result, baseline, scope, design ref and hash, acceptance items with real commands, budgets, repair and escalation boundary. The project PLAN stays authoritative.

```sh
python3 .../pi_task.py progress --repo WT --task TASK --round N --activity implementing --step '...' --completed-criteria ITEM --next '...' --evidence-ref PATH
python3 .../pi_task.py readiness --repo REPO --task TASK --round N
python3 .../pi_task.py phase-status --repo REPO --task TASK
python3 .../pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision accept --reviewed-head FULL_SHA --phase PHASE --contract-hash HASH
```

Progress is Pi's self-report: never a receipt, never acceptance. Readiness compares the contract with real `pi_check` receipts bound to the candidate; a missing, failed, skipped or unknown item is never ready. Ordinary progress stays on the board; only an anomaly (a sustained unrepaired check failure) may enqueue, at most twice per phase. `accept` binds phase, contract, candidate and the live round state, and refuses on any mismatch.

## Checks and resource protection

The brief gives each task's frozen `toolsDir` and the round's `checksDir`; run from the Pi worktree:

```sh
python3 /abs/task/tools/pi_check.py --output-dir /abs/round/round.checks --id affected-tests --timeout-seconds 600 -- make test
python3 /abs/task/tools/pi_check.py --output-dir ... --id evidence --timeout-seconds 180 --watch-path /abs/evidence --max-bytes 104857600 -- python3 real_check.py
python3 /abs/task/tools/pi_copy.py /abs/source /abs/new-destination --max-bytes 104857600
```

Receipts bind command, revision, exit and log hash; failed, skipped, interrupted, unknown or zero-test attempts are never a pass. A phase command timeout must not exceed the contract's `commandTimeoutSeconds`. Declared `resourceLimits` and `--watch-path` guards stop only the owned process group on a known breach; an incomplete measurement stays unknown. Owned temporary bash probes have a 600-second ceiling. `pi_copy` keeps symlinks and refuses existing destinations. Details and history: [validation notes](../../../docs/validation/command-protection-20261001.md).

## Decisions

```sh
python3 .../pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision changes_requested --failure-kind quality --note '...'
```

`--failure-kind external` requires a note naming the unlock condition. Counting rules are in the Skill.
