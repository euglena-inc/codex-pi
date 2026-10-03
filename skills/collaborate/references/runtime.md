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

Pi runs `pi -p --mode json --no-extensions --no-approve -e <tools>/pi_worker.ts ...`. The runtime writes `rounds/N/worker.json` (task, round, worktree, forbidden and allowed roots, bash default and ceiling, check cap, checks and tools dirs, Python, phase flag, settle quota path, contract text, and for new rounds `deadlinePath` plus compact `acceptanceItems`) and passes its path in `CODEX_PI_WORKER_CONFIG`. The extension registers Pi's native `createCodemodeExtension({models:false, mode:"on"})` first and writes `worker.ready` only after that registration returned; a round that ends, or runs a tool, without it fails as `WORKER_EXTENSION_NOT_LOADED`.

- **Guard:** every tool call is checked. A path in the main checkout or another registered worktree is blocked (`forbidden-path`, symlinks resolved); `write`/`edit` outside the round worktree, tools and checks dirs is blocked (`outside-worktree`); anything undecidable is blocked. Blocks go to `worker-blocks.jsonl`. `bash` gets a default timeout and is clamped to the ceiling (the phase command cap, else one hour). `codemode` is allowed only at the top level; its source is never scanned for paths, but every nested `tools.*` call is checked with the same guard (Pi marks it `parentToolCallId`), and malformed/unsupported first-line `// @options` or a nested `codemode` call is blocked before execution. The effective deadline is clamped to the command cap with a bounded default, and the output budget is bounded.
- **Tools:** `check` runs the frozen `pi_check.py` (receipts bind command, revision, exit and log hash; a failure returns `log_tail`, at most 20 lines and 2000 bytes). Before spawning it re-reads the round's real `deadlineAt` and computes one final effective window from the requested/contract cap, the remaining time minus a fixed 60-second wrap-up reserve, and a verifiable codemode outer deadline minus its cleanup grace. A requirement (the larger finite call/contract estimate, else the requested/contract cap) that exceeds the window is refused with a structured `ok:false` (`requiredSeconds`/`allowedSeconds`, `receipt:null`, no spawn); the admitted helper timeout is the window itself and keeps fractional seconds. A nested check inside codemode counts that script's outer deadline (an unverifiable one is refused with a direct-check hint). Executed checks report `elapsedSeconds`, `allowedSeconds` and the adopted `estimateSource`. A command that matches a contract item with `targetedCommand` is a full acceptance check: without explicit `final:true` on a clean worktree it is refused and the response names the targeted command; targeted runs never cover the formal item. `progress` and `readiness` call the frozen `pi_task.py`. All three declare `outputSchema` and return `structuredContent` with an explicit `ok`/error result in addition to their text and `details`; a failed check still writes its immutable receipt, and callers must inspect the semantic failure. `codemode` scripts can call these tools and the built-in read/write/bash tools; `store`/`load` keep only small JSON state and only a successful script commits it.
- **Prompt:** `brief.md` is the prompt plus a short fixed header. The contract is the system prompt section `codex_pi_worker` every run and `contract.md` every round.
- **Settle:** in a phase task, when only evidence is missing, the extension continues once per phase (the quota file under `settle/`).

## Phase contracts

A complete phase is frozen with `--contract-file` (schema and operations: [phase autonomy validation](../../../docs/validation/phase-autonomy-o1-20260926.md)): goal, result, baseline, scope, design ref and hash, acceptance items with real commands, budgets, repair and escalation boundary. The project PLAN stays authoritative.

```sh
python3 .../pi_task.py readiness --repo REPO --task TASK [--round N]
python3 .../pi_task.py phase-status --repo REPO --task TASK
python3 .../pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision accept --reviewed-head FULL_SHA --phase PHASE --contract-hash HASH
```

Progress is Pi's self-report, never acceptance. Readiness compares the contract with real receipts bound to the candidate; a missing, failed, skipped or unknown item is never ready. Prefer an item's `targetedCommand` for cheap local repair, commit the fixed candidate, then run the full command with `final:true` on the clean tree; a targeted receipt never covers the item, and no PASS is cached. Ordinary progress stays on the board; only a sustained unrepaired check failure may enqueue, at most twice per phase. `accept` binds phase, contract, candidate and the live round state, and refuses on any mismatch. A phase command timeout must not exceed `commandTimeoutSeconds`; declared `resourceLimits` stop only the owned process group on a known breach, and an incomplete measurement stays unknown.

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

## Bounded check concurrency

`checkExecution` is an optional phase-level object: `maxConcurrent` (1..4), `cpuSlots`, `memoryMiB`. Per-item `checkResources` may declare `parallelSafe`, `cpuSlots`, `memoryMiB`, and `exclusiveKeys`. Metadata follows normalized command argv, not the call's id. Conflicting declarations are refused.

New workers serialize checks by default. Parallel admission requires complete CPU/memory estimates and a configured memory pool; unknown commands or pressure run exclusively. FIFO waiting consumes the original deadline; final cleanliness, pressure and budget are checked again before spawn. Results include `queueSeconds`, `resourceMode`, `pressureState`, `pressureSynthetic` and a pool snapshot. Oversized requirements refuse without a child/receipt. CPU slots and memory estimates are soft per-worker bounds, not host-wide hard limits; other applications, arbitrary bash and detached processes remain outside them.

Use native codemode `Promise.allSettled` only for declared independent checks; do not change `maxWorkers` to speed local checks. Validate 1/2 lanes before 4. `CODEX_PI_PRESSURE_SNAPSHOT` is a synthetic probe override, marked in results; ordinary production checks should not set it.

## Network policy and diagnostics

Optional project config `network` has exactly `proxyUrl` and `diagnostics`. `proxyUrl` must be a credential-free `http(s)://host:port` URL with a root path and no query or fragment; validation runs before any task side effect and a rejected value is never echoed. `diagnostics` is boolean (default false). Omission preserves inherited proxy environment behavior; there is no automatic discovery, retry or route switch.

`start` freezes the project policy in `task.json`; `continue` applies only that frozen policy, so later project edits affect only new tasks. A task without the key (old evidence) keeps inherited behavior and its frozen helpers. Explicit routing overrides `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and their lowercase forms on the Pi child only, so child tool processes inherit it; `NO_PROXY`/`no_proxy` exclusions stay untouched. The supervisor and Codex queue transport keep their own environment.

With `diagnostics: true` the supervisor appends one `--import` option (percent-encoded `file://` URL) to the Pi child's existing `NODE_OPTIONS` and sets `CODEX_PI_NETWORK_DIAG_FILE`, `CODEX_PI_NETWORK_DIAG_SCOPE` and `CODEX_PI_NETWORK_DIAG_SUPERVISOR`. The frozen `pi_network_diagnostics.mjs` observes undici diagnostics channels and appends bounded redacted JSONL to `rounds/N/round.network.jsonl` (relative time/duration, phase, safe class, allowlisted code, CONNECT status and a `primary` flag). Only the supervisor's direct Pi child writes, so a round has one writer and a hard 64 KiB aggregate bound; inherited tool children keep the proxy policy but never append. Classification scans a bounded error/cause chain, ranks explicit proxy evidence above ambiguous abort codes, accepts proxy evidence only from usable messages, treats only an explicit `AbortError` as ordinary cleanup, and leaves unknown evidence `unknown`. It never stores URLs, hostnames, headers, bodies, prompts or raw exception text; write failures are harmless. The reader trusts only a bounded UTF-8 prefix of allowlist-valid records: missing, oversized, truncated, non-UTF-8 or forged content is `unreadable` with empty counts and forged values are never echoed. `status`/`result` include a compact `network` block with sanitized proxy mode/source/origin and the sidecar reference/counts; an untrusted or missing sidecar is unknown, not healthy.

Remediation: a pre-TLS reset points to transport; a CONNECT 503 identifies proxy tunnel failure. Validate or explicitly switch the user-configured route and never promise permanent repair. Preserve the session and resume explicitly after recovery. Native Pi retry configuration is separate and is not modified by this plugin.
