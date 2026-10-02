# Slim handoff (0.6.0): fewer Codex tokens, clearer Pi rounds

Status: authorized by the user on 2026-10-02 (four steps, Pi stays the executor). This file is the single specification for the 0.6.0 work; each step's brief points here.

## Evidence that motivates it

From one consuming project's local task records (10 Pi rounds, aggregates only):

- Pi is cheap: US$0.02–0.46 per round, about US$1.3 in total. Large tasks failed the first review and the Codex main session implemented the takeover, which is where the real cost went.
- Every round had 5–87 `bash outside path` flags. Most were the repository's **main checkout**, not the round worktree, so Pi read and ran commands against the main checkout. The runtime only reported this afterwards.
- Codex-facing text is heavy. The Skill plus references are about 51 KB. `status`, `result` and `show` print indented JSON of 5–15 KB. `result` returns both `summary` and `summaryText`. Each progress echo opens a full Codex turn that says "No decision is required".
- Pi receives the full ~60-line worker contract again on every round of the same session. `pi_check` prints one JSON line, so a failing check sends Pi back to read the log.

## Step 1 · Codex-facing output and documents

1. **Delivery card.** The queue packet for a review-ready, blocked or failed round is one compact card of at most **1200 UTF-8 bytes**. It contains:
   - task, round, event id, kind, full candidate SHA;
   - one line per acceptance item: `id exit=<n> run/pass/fail/skip` or `missing` / `unknown`;
   - review policy as `failed/limit owner`;
   - Pi's final report, truncated to fit;
   - a single `show`/`decide` hint.

   Absolute evidence paths are left out; `show --task` gives them on demand. The existing safeguards stay: overflow/drain reservation, oversized-event fallback and the exact event id. The old 3500-byte limit becomes 1200.
2. **Compact CLI output.** Everything the CLIs print to stdout is single-line JSON (`separators=(",", ":")`, no indent). Files on disk keep their current format.
   - `result` defaults to a compact view: the selected round's state, execution, candidate head, usage totals, check receipt lines (as in the card), at most 1200 characters of Pi's final text, and notes. It drops `summaryText`, the per-round evidence paths of other rounds and duplicated notes.
   - `result --full` returns the previous shape unchanged.
   - `status` and `show` drop repeated fixed notes (for example the "exit 0 is not acceptance" note, which appears once at most).
3. **Progress echoes.** Ordinary milestone echoes no longer enqueue; they stay on the board. Anomaly milestones (stalls, resource/deadline signals) still enqueue, within the existing per-phase quota. Their question names the anomaly and the decision the user can make.
4. **Documents.**
   - `skills/collaborate/SKILL.md` becomes the single authority and stays at or below about **8 KB**.
   - `references/runtime.md` holds command usage only (≤ 6 KB) and no release or validation history.
   - `references/handoff.md` (≤ 3 KB) holds only event handling and recovery.
   - `references/task-packet.md` (≤ 5 KB) holds the brief template and delivery format, plus the **must-ask checklist**:
     - which record or version wins, by content only;
     - byte-identical rerun and replay;
     - existing or same-name target;
     - dependence on time, timezone, environment or network;
     - empty, missing and unknown values (never zero, block and list).
   - Each rule appears once. Others link to it.
   - Release and validation history moves to `docs/validation/`.
   - The model policy is stated once and matches code: the project config selects one of `ALLOWED_MODELS`, the choice is frozen per task, and there is no fallback.
   - The `wait` instruction must not invite repeated waits.

## Step 2 · Clearer, faster Pi rounds

1. **Contract once per session.** Round 1, and any round whose phase contract hash differs from the last round that carried it, gets the full worker contract. Every other round gets a short header (≤ 15 lines) containing:
   - the task, round, contract hash and the round-1 brief path;
   - the frozen `pi_check` command template;
   - the outside-worktree rule;
   - "same worker contract as round N; it still applies".

   The full contract is still written to `rounds/N/contract.md` every round for audit.
2. **Failure tails for Pi.** When `pi_check` exits nonzero, times out or is cancelled, its stdout JSON adds `log_tail`: at most the last 20 lines and at most 2000 bytes, decoded as UTF-8 with replacement. Receipts are unchanged.
3. **Main checkout is off limits.** The command guard terminates an owned bash command whose text references a forbidden path. Forbidden paths are the repository's main checkout root and any other registered worktree of the same repository. Paths are resolved through symlinks before comparing, so a symlink alias of the main checkout counts. A path inside the round worktree, the task `tools` directory or the round `checks` directory is never forbidden. The termination is recorded like an existing guard termination, and Pi sees an error naming the rule. In the round summary:
   - a `write`/`edit` tool call outside the allowed directories makes delivery readiness **not ready** (`SCOPE_ESCAPE`);
   - a `read` outside them is counted and shown, but does not block.
4. **Brief guidance.** The generated worker contract adds one line: "If the brief leaves a must-ask item open (see task packet), choose the conservative reading and report it as a spec gap."

## Step 3 · Remove what queue delivery replaced

Remove these, with their tests, and keep everything else working:

- the legacy Stop-hook `arm`/`ack`/`status`/`release` delivery path in `pi_handoff.py`. The hooks stay only for pause on Interrupt and bounded recovery for cli-queue routes. A `delivered` binding no longer counts as needing recovery.
- `pi_task.py check` (observer);
- `pi_task.py wait`;
- `pi_board.py packet` and `pi_board.py dispatch` as CLI entries. The supervisor keeps calling the functions internally.

Frozen helper snapshots of existing tasks are untouched, because they run from their own `tools/`. `upgrade` refuses to adopt the new runtime for a task whose binding still uses the legacy Stop-hook route. Instead it reports how to finish that task with its frozen helpers.

## Step 4 · Measured comparison (Pi stays)

`pi_board.py metrics --repo R [--task T]` prints one compact JSON line per task:

- rounds;
- Pi usage and cost totals (from the round summaries; unknown stays unknown);
- review decisions by kind and failure kind;
- whether takeover happened;
- **Codex-facing bytes**: the sum of queued packet bytes, plus the bytes of each `result`/`status`/`show` stdout that the runtime printed for that task.

The runtime records those stdout byte counts in an append-only `codex-io.jsonl` under the task directory, as size only, never content. This makes "fewer Codex tokens" measurable between releases. It does not claim a saving without comparable runs.

## Step 5 · In-process worker extension (Pi ≥ 1.0.0)

Authorized on 2026-10-02 together with a real Pi validation. The machine runs a single Pi, 1.0.0. Verified Pi 1.0.0 facts:

- `--no-extensions` still loads explicit `-e <path>` files.
- A `tool_call` handler can block a call with `{block, reason}` or mutate its input. If the handler throws, the call is blocked.
- `registerTool` returns structured results with `isError`.
- `agent_before_settle` can return `{continue: true}` together with entries.
- `before_agent_start` can adjust system prompt sections.
- The bash tool accepts an optional `timeout`.
- `--no-approve` skips project `.pi` resources in non-interactive runs.

1. **Ship and freeze.** Add `runtime/pi_worker.ts`. It has no dependencies; it uses type-only imports from the host package and Node built-ins. Freeze it into `tools/` with the other helpers. The launch argv becomes `pi -p --mode json --no-extensions --no-approve -e <tools>/pi_worker.ts …` and keeps the existing flags.
2. **Per-round config.** Before launch, the runtime writes `rounds/N/worker.json` and passes its path in `CODEX_PI_WORKER_CONFIG`. The file holds:
   - task and round;
   - the worktree;
   - the forbidden roots and allowed roots computed in Step 2;
   - the default bash timeout and its ceiling (Step 2 values);
   - the checks dir and the tools dir;
   - the Python executable;
   - the phase flag;
   - the persisted auto-continue quota path;
   - the contract text.
3. **Load proof.** On load, the extension atomically writes `rounds/N/worker.ready` containing `{piVersion, pid, at}`. The round fails with `WORKER_EXTENSION_NOT_LOADED` and is never treated as ready in either of two cases: the round ends without the marker, or a tool execution appears in the JSON stream before the marker. Any guard decision the extension cannot make blocks the call. It never allows by default.
4. **`tool_call` guard.**
   - `read`, `write`, `edit` and every path token of a `bash` command are resolved with the same rules as Step 2's `forbidden_reference` (realpath, relative to the worktree, allowed roots win).
   - A forbidden path is blocked with this reason: `forbidden-path: <path> is outside this task's worktree <worktree>; work only inside it`.
   - A `write`/`edit` outside the allowed roots is also blocked, with `outside-worktree`. A `read` outside the allowed roots that is not forbidden is allowed.
   - A bash call without `timeout` gets the default; a larger value is clamped to the ceiling.
   - Each block is appended to `rounds/N/worker-blocks.jsonl` as `{at, tool, rule, path}`.
   - The Step 2 command guard and summary flags stay as the second line of defence.
5. **Native tools.**
   - `check(id, command, timeoutSeconds?, watchPath?, maxBytes?)` runs the frozen `pi_check.py` with the same arguments, so receipts are byte-compatible. It returns the receipt summary plus `log_tail`, and sets `isError` when the exit is nonzero, the check timed out or it was cancelled.
   - `progress(...)` and `readiness()` call the frozen `pi_task.py` subcommands.
   - The brief no longer prints long helper command templates. It names these tools.
6. **Settle check.** In a phase task, `agent_before_settle` runs `readiness`. It returns `continue: true` with an entry listing the missing items only when all of these hold:
   - every gap is missing evidence (not a failure or a design question);
   - the existing persisted per-phase auto-continue quota is unused, in which case it consumes the quota.

   The supervisor's post-round auto-continue reads the same quota, so the two together never exceed one continuation per phase.
7. **Contract in the system prompt.** `before_agent_start` appends the worker contract (from `worker.json`) as a system prompt section on every run of the session. Every round's brief file becomes the user prompt plus the Step 2 short header. `contract.md` is still written each round.
8. **Version gate.** `start` and `continue` refuse when `pi --version` is below 1.0.0 or cannot be read. The version is frozen in `task.json` as `piVersion`, and the actual version is recorded in each `round.meta`.
9. **Tests.**
   - Python tests drive the extension through Node: Node 22.18+/24 strips TypeScript types natively, with a fake `ExtensionAPI` harness in `tests/doubles/`.
   - They cover: blocking with a reason for forbidden paths, including symlink aliases; allowing inside the worktree; blocking outside-worktree writes; filling and clamping the bash timeout; fail-closed on handler errors; `check` receipt compatibility and `isError`; settle continuing once and then not; the shared quota with the supervisor; the load-marker failure path; the version gate.
   - The suite fails rather than skips when `node` is missing from `PATH`.
10. **Real validation (main session, after acceptance).**
    - Run one tiny real round with the pinned model in a synthetic temp repository that has a main checkout and a task worktree.
    - The brief asks Pi to (a) `cat` a file in the main checkout, (b) write and commit one file in its worktree, and (c) run the `check` tool on a passing command and on a failing one.
    - Expected results:
      - the JSON stream shows the forbidden-path reason as a tool result;
      - `worker.ready` exists;
      - the receipts exist and the failing check returns `log_tail` with `isError`;
      - the commit exists only in the worktree.

## Release

Version 0.6.0. README, plugin manifest and IMPLEMENTATION.md are updated to match. The full suite and `scripts/check_public_privacy.py` pass. No push, no install, no cache edits. Desktop queue delivery after these changes needs its own real validation (README "Fast acceptance") before it is installed.

## Implementation amendments

- Step 1 (accepted at `60e513c`):
  - The only anomaly that enqueues a progress echo is a sustained unrepaired check failure. Resource, deadline and ownership faults keep their own event kinds. No stall detector was added.
  - A multi-event card carries one decide hint, for the first event.
  - `SKILL.md` came out at 8.6 KB and `task-packet.md` at 5.2 KB, both accepted as "about".
- Step 2 additions:
  - The decide hint must reference the candidate and contract printed above it instead of repeating them. The target is ≤ 200 bytes, leaving room for Pi's report.
- Step 2 (accepted at `af3d1d1`):
  - Pi sees a forbidden-path command only as a signal termination. The rule name lives in the brief, in `contract.md` and in the guard records. Injecting text into Pi's tool result would need a different Pi invocation and is out of scope.
  - The decide hint is ≤ 200 bytes for typical paths. Very long install or repo paths can reach about 260 bytes. `decide` binding semantics are unchanged.
  - `SCOPE_ESCAPE` applies once `round.summary.json` carries `scope_escape`. Older rounds are not reinterpreted.
  - The forbidden-path check is a textual heuristic, not a sandbox.
- Step 3 (accepted at `158f79c`, +264/−2813 lines):
  - Pending legacy states are `armed`, `suspended`, `expired` and `needs_recovery`. A legacy binding file is retired by the user, because the runtime no longer has `release`.
  - No legacy binding state injects recovery text. `hooks.json` keeps `Interrupt`, `SessionStart` and `UserPromptSubmit`.
- Step 4 and release (accepted at `df08aa8`):
  - `show` across several tasks splits its stdout bytes evenly, with the remainder going to the first task. The total is exact; the per-task split is an estimate.
  - `metrics` takeover reflects the review-policy latch.
  - The manifest version is `0.6.0` without a `+codex.<timestamp>` cache suffix. The release owner adds a suffix at install time if one is wanted.
  - Baseline from one consuming repository (6 tasks, read-only `metrics`):
    - Pi cost US$0.10–0.46 per task, every usage record complete;
    - 2 takeovers;
    - Codex bytes not tracked before 0.6.0.
- Step 5 (accepted at `ddef69c`). The user widened the scope on 2026-10-02: lean code, native mechanisms, and compatibility code deleted.
  - Removed: `pi_command_guard.py`, summary scope-escape flags, the first-round contract logic, the supervisor auto-continue, old pins, `upgrade`, `result --full`, and legacy naming.
  - Pi 1.0 bash `timeout` kills the whole process group (`dist/core/tools/bash.js` 84–89, `dist/utils/shell.js` 157–176). `setsid` descendants escape, as before.
  - Real validation with Pi 1.0.0 and `deepseek/deepseek-flash` in a synthetic repository, three rounds:
    1. `worker.ready` was written before the first tool call. The contract reached the model through the system prompt; the model declined the forbidden read on its own. `check ok` passed and `check bad` returned `isError` with `log_tail`. The commit landed only in the worktree.
    2. A `continue` round forced the reads: `bash cat` and `read` of a main-checkout file were both blocked with the `forbidden-path` reason as an error tool result and logged to `worker-blocks.jsonl`. The brief was 15 lines.
    3. A phase task with two items, where the brief asked only for A1: `agent_before_settle` continued once in the same process, Pi added A2, the claim file was written, and the run settled.

## Step 6 · Split the two large modules (pure move)

`pi_task.py` (3.3k lines) and `pi_board.py` (2.7k lines) mix unrelated concerns. Split them by moving code only; behaviour does not change.

- **From `pi_task.py`:**
  - phase contract, readiness, evidence snapshot and settle view → `pi_phase.py` (merged with the existing module);
  - brief, contract text and `worker.json` composition → `pi_brief.py`;
  - supervisor loop and round finish → `pi_supervisor.py`;
  - `pi_task.py` keeps the filesystem/process helpers, config, layout and admission, and thin CLI commands.
- **From `pi_board.py`:**
  - queue delivery state and dispatch → `pi_queue.py`;
  - card and event model → `pi_events.py`;
  - `pi_board.py` keeps the store, lease, routes and pause, compact views and the CLI.
- **Rules:**
  - Every moved top-level function or class keeps an identical AST. This was a one-time migration check and is not kept in the tree; see the step 6 amendment.
  - Only imports and module-level wiring may change. No re-export shims are kept for the old locations.
  - Callers and tests import from the new module. The frozen helper snapshot list includes every new module, and a test proves the frozen `tools/` runs with no import from the plugin directory.
  - Circular imports are resolved by dependency direction, not by function-local imports, unless such an import already existed.
  - No file larger than about 1.8k lines.
- Step 6 (pure move accepted at `d06c180`; independent AST check against `e63ca2f`: 280 definitions moved, 0 changed). Cleanup at `27bcca5`:
  - Three low-level modules were added because the dependency direction requires them: `pi_core`, `pi_store` and `pi_evidence`.
  - The `__file__` overrides were replaced with `BOARD_CLI`/`TASK_CLI` constants, and the printed hints are unchanged.
  - The one-time AST checker and its baseline were removed. `tests/test_modules.py` keeps these layout guards: no cycles, no upward imports, a complete frozen list, frozen tools that run without the plugin directory, and ≤ 1.8k lines per file.
  - A real Pi 1.0.0 smoke ran on the split runtime with all 16 helpers frozen: the worker loaded, `check` wrote a receipt, the commit landed only in the worktree, and frozen `pi_task.py result` ran from `/tmp` with a minimal environment and no `__pycache__`.
