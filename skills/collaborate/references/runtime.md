# Runtime commands

Usage only. Rules, model policy and review boundaries are in [the Skill](../SKILL.md); event handling is in [handoff](handoff.md). Replace example paths and the owner UUID before running; never interpolate user text into shell commands. All commands print one line of compact JSON. Pi >= 1.0.0 is required (`start` and `continue` refuse an older or unreadable Pi); 0.5.x tasks are not migrated.

```sh
python3 /abs/plugin/runtime/pi_task.py project --repo /abs/repo
python3 /abs/plugin/runtime/pi_task.py start --repo /abs/repo --task TASK-1 --worktree /abs/wt --prompt-file /abs/brief.md [--contract-file /abs/phase.json] [--read-only]
python3 /abs/plugin/runtime/pi_board.py register --repo /abs/repo --task TASK-1 --thread OWNER_UUID --transport cli-queue --title 'Task title' --goal 'Reviewable result'
```

Start, then register immediately (registration also catches an already finished task). The default transport is `offline`; `cli-queue` needs a local Codex CLI with `queue`, the desktop app and the exact owner task UUID (`--codex-bin /abs/codex` picks an executable). Never guess an owner or start another process to deliver. `PI_BIN` selects the Pi executable. Project config `.agents/codex-pi.json`: schemaVersion 1, `model`, `thinking`, `constraints`, `checks`, `maxWorkers`, `timeoutSeconds`; use an isolated worktree, reserved for the task's lifetime.

## Reading state

```sh
python3 .../pi_task.py status --repo REPO --task TASK [--round N]   # fast read-only snapshot
python3 .../pi_task.py result --repo REPO --task TASK [--round N]
python3 .../pi_board.py show --repo REPO --task TASK               # board card, pending events, queue
python3 .../pi_board.py metrics --repo REPO [--task TASK]          # usage, decisions, Codex-facing bytes
```

`result` prints state, execution, candidate head, usage totals, check lines (`ID exit=N run=.. pass=.. fail=.. skip=..`, or `ID missing`/`ID unknown`), Pi's final text (at most 1200 characters) and notes. Collect it once per terminal round; raw logs, receipts and the native session stay under the Git common directory `codex-pi/tasks/`. `show` gives the absolute evidence paths a delivery card leaves out.

## Continue and cancel

```sh
python3 .../pi_task.py continue --repo REPO --task TASK --prompt-file /abs/repair.md [--contract-file /abs/phase.json]
python3 .../pi_task.py cancel --repo REPO --task TASK
```

`continue` preserves the pinned session and worktree in a new immutable round; a paused task refuses it until explicit resume. `cancel` signals only the owned worker group; verify it ended before repair.

## The worker round

Pi runs `pi -p --mode json --no-extensions --no-approve -e <tools>/pi_worker.ts ...`. The runtime writes `rounds/N/worker.json` (task, round, worktree, forbidden and allowed roots, bash default and ceiling, check cap, checks and tools dirs, Python, phase flag, settle quota path, contract text) and passes its path in `CODEX_PI_WORKER_CONFIG`. The extension registers Pi's native `createCodemodeExtension({models:false, mode:"on"})` first and writes `worker.ready` only after that registration returned; a round that ends, or runs a tool, without it fails as `WORKER_EXTENSION_NOT_LOADED`.

- **Guard:** every tool call is checked. A path in the main checkout or another registered worktree is blocked (`forbidden-path`, symlinks resolved); `write`/`edit` outside the round worktree, tools and checks dirs is blocked (`outside-worktree`); anything undecidable is blocked. Blocks go to `worker-blocks.jsonl`. `bash` gets a default timeout and is clamped to the ceiling (the phase command cap, else one hour). `codemode` is allowed only at the top level; its source is never scanned for paths, but every nested `tools.*` call is checked with the same guard (Pi marks it `parentToolCallId`), and malformed/unsupported first-line `// @options` or a nested `codemode` call is blocked before execution. The effective deadline is clamped to the command cap with a bounded default, and the output budget is bounded.
- **Tools:** `check` runs the frozen `pi_check.py` (receipts bind command, revision, exit and log hash; a failure returns `log_tail`, at most 20 lines and 2000 bytes); `progress` and `readiness` call the frozen `pi_task.py`. All three declare `outputSchema` and return `structuredContent` with an explicit `ok`/error result in addition to their text and `details`; a failed check still writes its immutable receipt, and callers must inspect the semantic failure. `codemode` scripts can call these tools and the built-in read/write/bash tools; `store`/`load` keep only small JSON state and only a successful script commits it.
- **Prompt:** `brief.md` is the prompt plus a short fixed header. The contract is the system prompt section `codex_pi_worker` every run and `contract.md` every round.
- **Settle:** in a phase task, when only evidence is missing, the extension continues once per phase (the quota file under `settle/`).

## Phase contracts

A complete phase is frozen with `--contract-file` (schema and operations: [phase autonomy validation](../../../docs/validation/phase-autonomy-o1-20260926.md)): goal, result, baseline, scope, design ref and hash, acceptance items with real commands, budgets, repair and escalation boundary. The project PLAN stays authoritative.

```sh
python3 .../pi_task.py readiness --repo REPO --task TASK [--round N]
python3 .../pi_task.py phase-status --repo REPO --task TASK
python3 .../pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision accept --reviewed-head FULL_SHA --phase PHASE --contract-hash HASH
```

Progress is Pi's self-report, never acceptance. Readiness compares the contract with real receipts bound to the candidate; a missing, failed, skipped or unknown item is never ready. Ordinary progress stays on the board; only a sustained unrepaired check failure may enqueue, at most twice per phase. `accept` binds phase, contract, candidate and the live round state, and refuses on any mismatch. A phase command timeout must not exceed `commandTimeoutSeconds`; declared `resourceLimits` stop only the owned process group on a known breach, and an incomplete measurement stays unknown.

## Explicit 0.6 recovery

```sh
python3 .../pi_board.py recover-store --repo REPO
python3 .../pi_board.py show --repo REPO --all [--cursor LAST_TASK]
python3 .../pi_board.py events --repo REPO --task TASK [--cursor LAST_SEQ] [--pending]
python3 .../pi_board.py decisions --repo REPO --task TASK [--cursor LAST_EVENT_ID]
python3 .../pi_task.py adopt-runtime --repo REPO --task TASK --dry-run
python3 .../pi_task.py adopt-runtime --repo REPO --task TASK
```

Use the newly installed CLI for control/recovery. Store conversion requires released writers and retains original JSON, events, decisions and delivery claims. Adoption prepares a separate immutable runtime for future 0.6 rounds, never edits old tools/task/rounds, never resumes, changes model or extends budget. Then only the owner main task may resolve execution incidents, explicitly resume a paused route and `continue` the same session/worktree with remaining budget. 0.5 workers are not adopted. A provider `execution_failed` incident is resolved separately from quality review; an exit-zero error cannot be accepted as a delivery. See [recovery verification](../../../docs/validation/recovery-0.6.1-20261002.md).

## Decisions

```sh
python3 .../pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision changes_requested --failure-kind quality --note '...'
```

`--failure-kind external` requires a note naming the unlock condition. Counting rules are in the Skill.
