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
