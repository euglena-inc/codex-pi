# Codex-Pi runtime implementation

Version 0.6.5 (`runtime/VERSION`). The 0.6.4 terminal recovery remains: bounded recovery for large terminal `agent_end` records and read-only re-evaluation of eligible legacy `unknown` rounds from their complete stream plus matching exit metadata. Sol-Luna and its offline comparison tooling are now maintained as a standalone personal skill; Pi execution behavior is unchanged by this extraction. It requires Pi >= 1.0.0. Historical task/round snapshots remain immutable; 0.5.x workers cannot adopt a new runtime.

Cross-project persistent Pi worker lifecycle for the Codex main session: **Codex main -> shell -> Python lifecycle scripts -> Pi CLI with one worker extension**. There is no MCP server and no package manifest. Python owns admission, persistence, evidence, model policy and process control. A single dependency-free TypeScript file (`runtime/pi_worker.ts`) runs inside Pi and guards and supports the worker. Current commands and rules are in [runtime guidance](../skills/collaborate/references/runtime.md), [handoff guidance](../skills/collaborate/references/handoff.md) and [the Skill](../skills/collaborate/SKILL.md).

The Pi execution path starts only Pi. The opted-in board transport invokes only `codex --disable daemon_auto_start queue` to notify the existing desktop task; it never starts another Codex model.

## Entrypoints

```sh
python3 runtime/pi_task.py project  --repo <path-inside-project>
python3 runtime/pi_task.py start    --repo <path> --task <id> --worktree <linked-checkout> --prompt-file <brief> [--contract-file <phase.json>] [--read-only]
python3 runtime/pi_task.py continue --repo <path> --task <id> --prompt-file <follow-up>
python3 runtime/pi_task.py status | result | readiness | phase-status | progress | cancel ...
python3 runtime/pi_board.py register | show | decide | pause | resume | recover | rearm | refresh | metrics ...
```

Every command prints one single-line compact JSON object and exits non-zero with an actionable stderr message on error. `PI_BIN` overrides the Pi executable (test double or explicit path only).

Task side (lower modules never import the ones above them; `pi_task.py` is the CLI entry):

| File | Role |
| --- | --- |
| `runtime/pi_core.py` | Filesystem, lock, process, config and layout helpers; shared constants |
| `runtime/pi_evidence.py` | Bounded read-only scanning of check receipts and markers; candidate identity; check lines |
| `runtime/pi_phase.py` | Phase contract schema and validation, phase files and budget, resource limits, readiness snapshot, settle view |
| `runtime/pi_brief.py` | Brief, full worker contract text, `worker.json`, round inputs |
| `runtime/pi_supervisor.py` | Detached supervisor: launch Pi for one round, watch, finish the round, phase round log |
| `runtime/pi_task.py` | Admission, worktree claims, frozen helper snapshot, status/result building, CLI |
| `runtime/pi_worker.ts` | In-process worker extension: guard, native tools `check`/`progress`/`readiness`, system prompt contract, settle continuation |
| `runtime/pi_summary.py` | Bounded round summary, check-receipt aggregation, usage, reported-model check |
| `runtime/pi_execution.py` | Bounded final assistant/stream inspection; shared by finish, status, result and readiness |
| `runtime/pi_recovery.py` | Writer-free store conversion and separate immutable future-round helper generations |
| `runtime/pi_check.py` | One-check receipt: true exit/signal/timeout, log sha256, HEAD/dirty, counts, `log_tail` on failure |
| `runtime/pi_copy.py` / `pi_size.py` | Bounded evidence copying and byte scans without following symlinks |

Board side:

| File | Role |
| --- | --- |
| `runtime/pi_store.py` | Board files, locks, monitor records, owner routes and route pause |
| `runtime/pi_archive.py` | Plugin-owned transactional SQLite cards, events, decisions, phase quotas and queue claims; bounded windows and cursor pages |
| `runtime/pi_events.py` | Card and event model, status projection, bounded progress echoes, decide hint |
| `runtime/pi_queue.py` | Queue claims, the delivery card, one dispatch through `codex queue` |
| `runtime/pi_board.py` | Registration, refresh, decisions, compact views, `metrics`, CLI |
| `runtime/pi_takeover.py` | Failure-policy fold from exact review decisions: two complete deliveries, then takeover |
| `runtime/pi_handoff.py` | Hook entry: Interrupt route pause and cli-queue recovery evidence |
| `hooks/hooks.json` | Interrupt, SessionStart and UserPromptSubmit hook commands; requires host trust |

Every module the entries import is listed in `HELPER_FILES` and copied into a task's frozen `tools/`, so a running task never imports from the plugin directory. No module is larger than about 1.8k lines.

## The worker round

`start` and `continue` refuse unless `pi --version` is >= 1.0.0; the version is frozen as `piVersion` in `task.json` and the actual one is written to each `round.meta`. Before launch the runtime writes `rounds/N/worker.json`, the round's `brief.md` (user prompt plus a short fixed header) and `contract.md` (full worker contract). Launch:

```
pi -p --mode json --session-id ... --session-dir ... --model ... --thinking ... --tools <list>
   --no-extensions --no-approve -e <tools>/pi_worker.ts --no-skills --no-prompt-templates @brief.md
CODEX_PI_WORKER_CONFIG=<round>/worker.json
```

- **Load proof.** The extension atomically writes `worker.ready` (`piVersion`, `pid`, `at`). A round that ends without it, or in which a `tool_execution_start` appears before it, fails as `WORKER_EXTENSION_NOT_LOADED` and is never ready. An unreadable config leaves no marker and blocks every call.
- **Guard** (`tool_call`). Path tokens of `read`/`write`/`edit`/`grep`/`find`/`ls` and every path-like token of a `bash` command or `check` argument are resolved through symlinks (relative to the worktree). A path inside the main checkout or another registered worktree is blocked unless it is inside an allowed root (round worktree, task tools, round checks): `forbidden-path`. `write`/`edit` outside the allowed roots is blocked: `outside-worktree`. Unknown tools and undecidable inputs are blocked: `undecidable`. Blocks are appended to `worker-blocks.jsonl`. `bash` without a `timeout` gets the default (600 s); a larger value is clamped to the ceiling (the phase command cap, else 3600 s). Pi's bash tool kills the whole process group when its timeout fires (`dist/core/tools/bash.js` timer calling `killProcessTree`; `dist/utils/shell.js` `process.kill(-pid, "SIGKILL")`).
- **Tools.** `check` runs the frozen `pi_check.py` with `--output-dir <checks>`, so receipts are byte-compatible; the command string is split like a POSIX shell without running a shell. It returns `isError` for a nonzero exit, a timeout or a cancel and includes `log_tail`. `progress` and `readiness` call the frozen `pi_task.py`.
- **Prompt.** `before_agent_start` adds the contract as the `codex_pi_worker` system prompt section on every run.
- **Settle.** `agent_before_settle` runs `readiness`; when `settle.eligible` it claims the per-phase quota file (`settle/<hash>.claim`, exclusive create) and returns `continue: true` with one entry listing the missing items. Eligible means: every gap is missing evidence (no failed, skipped or unknown item), scope and resources fine, at least 60 s of phase budget left, no board pause, quota unused. Any error means no continuation.

## Host continuation

`pi_board.py` projects bounded execution/check evidence into a shared local board. The Pi supervisor refreshes it roughly every 15 seconds and on terminal exit. It enqueues only actionable events (a delivery card of at most 1200 UTF-8 bytes; ordinary progress milestones stay on the board) to the exact registered desktop task UUID. Idle delivery starts a turn; busy delivery waits for the current turn to end. A local observation does not call a model.

Event content and main decisions are distinct from queue claims. Claims use a short file lock, released before the bounded CLI invocation. A confirmed send is not repeated while review is pending. An ambiguous post-spawn failure is uncertain and needs explicit recovery; the queue has no caller idempotency key. Short hooks provide interruption pause and recovery evidence. The runtime records only sizes of what it prints to the Codex side (`codex-io.jsonl`); `metrics` aggregates them. The supervisor cannot detect its own death. Tested desktop evidence: [CLI queue validation](../docs/validation/cli-queue-20260926.md).

## Model policy

`load_config` accepts only models in `ALLOWED_MODELS` (`deepseek/deepseek-flash`, `newapi/glm-5.3`); the choice is frozen per task at `start`. `continue` and the detached worker revalidate the frozen model before touching evidence or launching anything; the spawn argv uses the frozen model and there is no fallback. Pi's reported provider/model comes from raw events and is compared with the frozen one: a mismatch stays visible as `modelCheck: "mismatch"` and the result stays `acceptance: "not_verified"`.

## Repository resolution

`--repo` may point anywhere inside a checkout; the runtime keeps **that checkout** as the project identity (`canonical_root`) and never loads another checkout's config. `validate_worktree` rejects both the configured project checkout and the repository's Git primary checkout as implementation worktrees. `continue` requires the frozen `repo` identity to match. `result` and `cancel` read common-dir evidence from any checkout of the project.

## State layout

```
<git-common-dir>/codex-pi/
  .admission.lock                         per-project start/continue admission
  worktrees/<sha256(realpath)>.claim.json permanent one-writer claim per worktree
  board.json / board.queue.json ...       shared board and queue state
  tasks/<task>/
    task.json                             frozen repo/worktree/model/thinking/session/runtime/piVersion
    .task.lock / .supervisor.lock         supervisor (task lock also inherited by Pi) / supervisor only
    tools/                                frozen helper snapshot (incl. pi_worker.ts) + VERSION
    session/                              pinned Pi session directory
    settle/                               one-shot settle quota claim files
    codex-io.jsonl                        size-only Codex-facing accounting
    rounds/<n>/
      brief.md contract.md                immutable (0444); contract.md is the full worker contract
      worker.json worker.ready            extension configuration and load proof
      worker-blocks.jsonl                 guard blocks (tool, rule, path)
      round.jsonl / round.err             raw Pi stdout/stderr
      round.meta / round.state.json       exit, timeout, cancel, HEAD, Pi version, error
      round.summary.json / .txt           bounded summary
      round.checks/                       pi_check receipts
```

A detached worker executes the **snapshot** `tools/pi_task.py`, so updating the plugin cannot change an active run. Worktree claims are task-lifetime reservations, never auto-released, so every new task needs a new linked checkout.

## Command contract

- `project` - resolved checkout, config, capabilities and limits (`allowedModels`, the worktree rule, active tasks). No model request, no worker.
- `start` - fresh task, immutable round 1, detached worker. `--read-only` limits Pi to `read,grep,find,ls` plus the native tools; writable adds `write,edit,bash`; neither is a security sandbox.
- `continue` - only a terminal-known task of the same frozen checkout, same pinned session and worktree, next immutable round. Refuses while the task lock is held (including by an orphaned Pi), when the previous state is unknown, under a pause or reached takeover, or when brief/exit/log evidence is stale; no automatic retry or replay.
- `status` / `result` - bounded read-only snapshot / compact terminal view; `supervisorAlive` and `activeWorker` expose lock truth.
- `cancel` - writes an explicit request for the live supervisor, which stops Pi's process group. If the supervisor is gone but an orphaned Pi still holds the task lock it returns `request: "orphaned"` without signaling an unverified PID.

Exit code 0 is a process fact. Completion additionally requires a verified final assistant `stop`; an error, abort, incomplete turn or unreadable terminal tail cannot reuse an earlier answer. Every result carries `acceptance: "not_verified"`.

## 0.6.1 storage and recovery

`board.json` is a small format marker; `board.store.sqlite3` is this plugin's own indexed state, never the desktop app's queue database. Current per-task projections and record blobs are capped; reads select a task or a cursor page (50 entries), with an additional 256 KiB default event-window budget. Events, exact decisions, quota ledgers and prior claim transitions are retained. Durable history grows on disk. SQLite transactions, full synchronization and the existing short locks preserve event/card and claim updates together. A stale projection cannot commit over a newer revision.

Explicit `recover-store` holds admission/board/queue locks, checks all registered and unregistered writer leases, archives original JSON bytes, imports a transaction and publishes the marker last. A rollback or interrupted marker publication is retryable with the same source fingerprint; changed/unverified inputs fail closed. Historical limits and takeover latches remain authoritative. Obsolete JSON-reading controls see pause sentinels; operators use the new installed board CLI.

`adopt-runtime` checks the exact 0.6 task, released leases and recorded process groups. It writes only a new hash-bound `runtime.adoptions/<hash>/tools/` and pointer for future rounds. Existing task.json, tools, session, rounds, phase contract, quality decisions and budget anchor are unchanged. `continue` still requires explicit pause recovery, verified ownership and remaining budget; its spawned round uses that generation and the original absolute phase deadline. Unknown ownership, exhausted budget or provider availability is not repaired by switching executor or resetting a task.

## Lock and liveness model

The supervisor holds `.task.lock` and `.supervisor.lock`; Pi inherits `.task.lock`, so a killed supervisor cannot free capacity while Pi runs. `.supervisor.lock` is never inherited: an active recorded state without a live lease is `unknown`, never `completed`. A parent-side `starting` record while the task lock is held counts as active. `cancel` never signals a PID it cannot prove it owns.

## Limitations

- The runtime validates an existing linked checkout; the main session creates it.
- The guard is a path-token check at the tool boundary, not a sandbox: it cannot see paths built at run time by the shell, and `bash` keeps the user's local capability. A process that detaches from its group escapes Pi's timeout kill.
- The model restriction rejects other IDs and surfaces reported mismatches but cannot attest the upstream provider.
- An orphaned Pi or supervisor is never killed automatically; the operator inspects the recorded PIDs.
- Config checks and constraints are text passed to Pi; the runtime never executes them. Acceptance stays with the project's checks and Codex review.
- Unreported provider usage stays `null`/unknown. Requires Python 3.10+, Git, POSIX process groups (macOS/Linux) and Pi >= 1.0.0 (Node runs the extension).

## Tests

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Tests use the offline Pi double in `tests/doubles/`, which plays the extension's load proof; the extension itself is driven through Node's native type stripping with a fake ExtensionAPI (`tests/doubles/extension_harness.mjs`). A missing `node` fails the suite. No live model call, no network, no Codex binary. `tests/test_modules.py` guards the module layout: size cap, acyclic imports, entry-module isolation, a complete frozen helper list and a frozen-tools run with the plugin directory removed.

## Reviews and takeover

Every task allows two complete Pi deliveries; the first reviewed quality failure requires the same Codex main session to complete the whole-task analysis (see the Skill) before the second delivery, and the second failure transfers implementation to that session. Counting is task-scoped since the last accepted outcome: one exact main decision per distinct round on a `review_required`/`phase_blocked` event (`changes_requested`/`reject`, `failure-kind quality`); duplicate events, checks, progress, the in-round settle continuation and explicitly external blockers do not count. A reached takeover is latched for that task; the board persists `codex_takeover_required` and `continue` and the worker refuse Pi. The runtime counts exact decisions and controls ownership; it cannot prove analysis quality and never starts Codex.
