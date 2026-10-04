# Codex-Pi runtime implementation

Release 0.8.6 fits the four model-visible collaboration documents into a frozen byte budget (per-file caps 8424/7016/5312/2132, total <= 22784, which is -46.9% against the 42692-byte baseline) without dropping an inventoried rule, and adds `tests/test_docs.py`: a deterministic offline guard for per-file and total budgets, relative-link integrity under `skills/`, `docs/` and `runtime/`, documented `pi_task.py`/`pi_board.py` subcommands and flags against the live `--help` surface, the reverse direction (every public subcommand must appear in the model-visible documents, so shrinking a reference can never delete the only current syntax for a live command), mechanism single-homing and `.agents/codex-pi.json` constraint direction. The first delivery met the pre-review estimates but had deleted that command surface and two baseline rules; the reviewed phase budget was exhausted, so implementation completed in the main session after an explicit takeover (see `docs/design/doc-size-budget.md` "Budget revision"). Mechanism prose now lives here: worker-round launch detail, network diagnostics, check-resource admission and cost scope. No runtime algorithm, acceptance, counting, ownership, model or receipt semantics change; no model-capability, speed or cost claim is made.

Release 0.8.5 adds a main-owned closeout record and a read-only outcome view to the existing metrics CLI. `record-outcome` validates a bounded reported payload (explicit same-repository task/round members, status, full candidate for accepted reports, evidence references, reported timing and work quantities, and pinned standard check receipts) and publishes immutable numbered revisions under the private git-common `codex-pi/outcomes/<ID>/` directory with the existing lock and atomic-write primitives. Ids, member paths and extra checks are resolved from the verified git-common state root, so a symlinked state/outcomes/member component or an escaping path is refused for record, query and listing alike; every read is byte-bounded before parse, and a normalized envelope larger than the reader's revision limit is refused before publish. Identical normalized payload replays are idempotent; corrections need the exact current revision and retain older snapshots; stale or conflicting updates change nothing; stored revisions are shape-validated beyond their own digest. `pi_outcome.py` owns validation, trusted paths and storage; `pi_outcome_view.py` projects six compact sections (delivery, timing, rework/incidents, verification, usage/cost, intervention/coverage) and the bounded cursor listing. The explicitly selected member `(task, round)` set is the only attribution boundary: archive decisions/events are cursor-paged per task, immediately tagged with task identity, and filtered to those rounds, duplicate task members are merged, unselected acceptances/failures/takeovers never mix in, and unattributable records keep coverage unknown. Quality failures count only delivery-event quality decisions deduplicated by distinct `(task, round)`; the earliest reviewed delivery keeps its own decision. Verification normalizes each check source once (stored summaries usually name a bare receipt file, resolved against the owning round's checks directory), deduplicates by canonical path across round summaries and extra Main checks, treats missing/contradictory/unverified copies as completeness degradation rather than silently choosing one, keeps dirty or incomplete-argv receipts out of clean-repeat groups, and counts failed extra Main checks. Usage/cost consumes the stored assistant, auxiliary and combined total blocks once (no assistant-only completeness claim, no double-counted auxiliary cost or reasoning, no model proration); missing or legacy coverage keeps the total unknown, a `workComplete` declaration never invents a cost, and an explicit empty external report can be a reported zero. Timing never guesses: an inconsistent finish/start or incomplete member creation metadata yields unknown with a reason, interval unions are never added to wall time, and missing boundaries stay null rather than zero. Read-only queries and the closeout write touch no execution, board decision, budget, pause, quality counter, notification, session or raw check log. No new database, service, scheduler or model call is introduced; both new modules are lower-level helpers frozen into new task snapshots.

Release 0.8.4 aligns active verification guidance with actual change impact and reuses one test-support environment isolation helper in fixture creation and the standalone connection validator. The helper copies the parent environment, drops inherited Node/diagnostic injection and applies explicit fixture overrides last. Production network, ownership, budgets and receipt validation are unchanged. Formal commands remain current-round/exact-candidate; evidence applicability is reviewed explicitly, not inferred by a cache.

Release 0.8.3 makes the weak-model-first allocation preference explicit in the Skill and the task packet: when facts, settled consequential interfaces, genuine feedback and authorized resources allow, prefer the configured Pi model for the largest reasonably self-contained complete outcome; choose the route by time to accepted delivery and all attributable costs, and require a concrete task-specific reason for direct Codex completion or early takeover. No runtime mechanism changes. Release 0.8.2 adds the optional `qwen38/qwen38` model route; projects selecting it use Pi's highest thinking setting, `max`. Release 0.8.0 added optional frozen network policy, primary-process transport diagnostics, and the explicit `newapi/deepseek-flash` route. Worker instructions became clearer without changing acceptance semantics. The instruction pilot pins its shared runtime to the original experiment commit so later release version bumps do not invalidate the recorded evidence. Historical task helpers remain unchanged.

Version 0.7.1 (`runtime/VERSION`). The 0.7.0 native codemode behavior is unchanged. The `check` tool re-reads the real round `deadlineAt` before spawning and computes one final effective window from the requested/contract cap, `remainingSeconds - 60`, and a verifiable codemode outer deadline minus its cleanup grace. A check whose requirement (the larger finite call/contract estimate, else the requested/contract cap) exceeds that window is refused with structured `requiredSeconds`/`allowedSeconds` and no receipt; the admitted helper timeout is the window itself and keeps fractional seconds. A nested check inside codemode counts that script's own outer deadline; an unverifiable one is refused with a direct-check hint. Executed checks report the real `elapsedSeconds`, `allowedSeconds` and the adopted `estimateSource`. A phase acceptance item may declare the optional `targetedCommand` and `estimatedSeconds`; contradictory metadata for one normalized argv is rejected at freeze time, a full command with a targeted suggestion needs explicit `final:true` on a clean worktree, and targeted runs never cover the formal acceptance item. The check helper analyzes its log in one streaming pass: fixed-size byte chunks produce the full SHA-256, the conservative Go/unittest counts and a bounded `log_tail` together, so no second full-file decode is needed; over-long single lines stay unknown instead of re-anchoring dropped prefixes; only a truly absent log keeps the historical empty-log compatibility, while an unreadable existing file or a failed mid-read raises so no normal-looking receipt is written. Receipt verification at terminal-summary time hashes the same log in a stream too. `round.summary.json` now carries a labelled `metrics` breakdown (unique assistant usage keyed by response id when available, reasoning as an output sub-item, request-context first/last/peak, real check counts and completed elapsed, session-derived model/tool interval unions and the unattributed remainder); each metric separates its known value from a `complete` flag, timing is only derived when the caller passes an explicit session directory/id and round window, the session file is accepted only when its persisted header id matches, and missing or ambiguous sources stay `null` with a reason. Board `metrics` aggregates those stored summaries without any session scan, partitions every round into known/incomplete/unknown per metric, and keeps the old summary fields unchanged. Auxiliary session usage (`compaction`, `branch_summary`, `usage` entries) is attributed strictly inside the caller's round window, deduplicated by explicit entry id only for byte/canonically identical replays, and reported separately from the unchanged assistant fields; conflicting repeats, non-finite or negative token/cost fields, a failed or unfinished compaction signal without a successful entry, a missing session source, or an unverified round transcript stays unknown/incomplete instead of zero, while a reliable clean session without auxiliary entries is a known zero. `scripts/validate_codemode.py --context-workflow` exercises the real scripted-provider SDK for a precondition negative control, a direct-read versus codemode-batch request/added-context-byte comparison, and idle native `session.compact` safety with failure/cancel/no-model and active-state controls; the active control only shows that this plugin never triggers compact or exposes a compact tool, not that native auto-compaction cannot run between turns, and scripted fixed usage is never read as real token or cost savings. `scripts/benchmark_runtime.py --baseline-ref REF` materializes the old helper read-only from git and runs helper then terminal summary on synthetic 10 MiB/200 MiB logs in separate sequential child pairs, comparing hash, counts, tail, wall and peak RSS (max of the pair, never accumulated) and failing nonzero if the candidate peak growth or the saving versus the old implementation misses the declared bound. New workers register Pi's public `createCodemodeExtension({models:false, mode:"on"})` from inside the explicit worker extension, so scripts use native `tools.*` calls through Pi's validation and `tool_call`/`tool_result` hooks while `--no-extensions`, skills and prompt templates stay isolated. The 0.6.4 terminal recovery remains: bounded recovery for large terminal `agent_end` records and read-only re-evaluation of eligible legacy `unknown` rounds from their complete stream plus matching exit metadata. Sol-Luna and its offline comparison tooling are now maintained as a standalone personal skill; Pi execution behavior is unchanged by this extraction. It requires Pi >= 1.0.0. Historical task/round snapshots remain immutable; 0.5.x workers cannot adopt a new runtime.

Cross-project persistent Pi worker lifecycle for the Codex main session: **Codex main -> shell -> Python lifecycle scripts -> Pi CLI with one worker extension**. There is no MCP server and no package manifest. Python owns admission, persistence, evidence, model policy and process control. A single dependency-free TypeScript file (`runtime/pi_worker.ts`) runs inside Pi and guards and supports the worker. Current commands and rules are in [runtime guidance](../skills/collaborate/references/runtime.md), [handoff guidance](../skills/collaborate/references/handoff.md) and [the Skill](../skills/collaborate/SKILL.md).

The Pi execution path starts only Pi. The opted-in board transport invokes only `codex --disable daemon_auto_start queue` to notify the existing desktop task; it never starts another Codex model.

## Entrypoints

```sh
python3 runtime/pi_task.py project  --repo <path-inside-project>
python3 runtime/pi_task.py start    --repo <path> --task <id> --worktree <linked-checkout> --prompt-file <brief> [--contract-file <phase.json>] [--read-only]
python3 runtime/pi_task.py continue --repo <path> --task <id> --prompt-file <follow-up>
python3 runtime/pi_task.py status | result | readiness | phase-status | progress | cancel ...
python3 runtime/pi_board.py register | show | decide | takeover | pause | resume | recover | rearm | refresh | record-outcome | metrics ...
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
| `runtime/pi_summary.py` | Bounded round summary, streaming receipt hash verification, labelled usage/context/check metrics with known/complete separation, header-verified session timing, validated strict-window auxiliary `compaction`/`branch_summary`/`usage` usage with explicit id conflicts and round-signal health, reported-model check |
| `runtime/pi_execution.py` | Bounded final assistant/stream inspection; shared by finish, status, result and readiness |
| `runtime/pi_recovery.py` | Writer-free store conversion and separate immutable future-round helper generations |
| `runtime/pi_check.py` | One-check receipt: true exit/signal/timeout, single-pass log sha256/counts/`log_tail`, HEAD/dirty, `log_tail` on failure |
| `scripts/benchmark_runtime.py` | Read-only old/new synthetic log benchmark (helper then summary): hash/counts/tail parity, wall and peak RSS with declared growth/saving bounds |
| `scripts/validate_codemode.py` | Real Pi 1.0/QuickJS probe: native codemode, bounded concurrency, and the mutually exclusive context-workflow precondition, batch-read and idle-compaction checks |
| `runtime/pi_copy.py` / `pi_size.py` | Bounded evidence copying and byte scans without following symlinks |

Board side:

| File | Role |
| --- | --- |
| `runtime/pi_store.py` | Board files, locks, monitor records, owner routes and route pause |
| `runtime/pi_archive.py` | Plugin-owned transactional SQLite cards, events, decisions, phase quotas and queue claims; bounded windows and cursor pages |
| `runtime/pi_outcome.py` | Main-reported outcome observations: trusted-path validation, strict payload/revision validation, bounded immutable revisions and bounded record-file reads |
| `runtime/pi_outcome_view.py` | Read-only six-section outcome projection and bounded cursor listing over selected member rounds, archived cursors, stored summaries and normalized check receipts |
| `runtime/pi_events.py` | Card and event model, status projection, bounded progress echoes, decide hint |
| `runtime/pi_queue.py` | Queue claims, the delivery card, one dispatch through `codex queue` |
| `runtime/pi_board.py` | Registration, refresh, decisions, compact views, `metrics`, CLI |
| `runtime/pi_takeover.py` | Failure-policy fold from exact review decisions: at most two complete deliveries, explicit takeover |
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

- **Load proof.** The extension atomically writes `worker.ready` (`piVersion`, `pid`, `at`, `codemode: true`) only after the native codemode registration returned. A round that ends without it, or in which a `tool_execution_start` appears before it, fails as `WORKER_EXTENSION_NOT_LOADED` and is never ready. An unreadable config or a Pi without the public export leaves no marker and blocks every call.
- **Guard** (`tool_call`). Path tokens of `read`/`write`/`edit`/`grep`/`find`/`ls` and every path-like token of a `bash` command or `check` argument are resolved through symlinks (relative to the worktree). A path inside the main checkout or another registered worktree is blocked unless it is inside an allowed root (round worktree, task tools, round checks): `forbidden-path`. `write`/`edit` outside the allowed roots is blocked: `outside-worktree`. Unknown tools and undecidable inputs are blocked: `undecidable`. Blocks are appended to `worker-blocks.jsonl`. `bash` without a `timeout` gets the default (600 s); a larger value is clamped to the ceiling (the phase command cap, else 3600 s). Pi's bash tool kills the whole process group when its timeout fires (`dist/core/tools/bash.js` timer calling `killProcessTree`; `dist/utils/shell.js` `process.kill(-pid, "SIGKILL")`).
- **Codemode.** The native `codemode` tool is active in both selections. Its source is never scanned for forbidden strings: every nested `tools.*` call is a separate `tool_call` with `parentToolCallId` and passes the same guard. A nested `codemode` call and non-string/empty source are blocked. The first line `// @options:` is parsed with upstream semantics; `timeout_ms` is clamped to the command cap with the bounded bash default, `max_output_tokens` is bounded at 10000, and malformed or unsupported options fail closed before the script runs. Cancellation propagates to nested tools through Pi's abort signal; completed calls are not undone. `models` globals are disabled, so model usage stays assistant usage.
- **Tools.** `check` runs the frozen `pi_check.py` with `--output-dir <checks>`, so receipts are byte-compatible; the command string is split like a POSIX shell without running a shell. It returns `isError` for a nonzero exit, a timeout or a cancel and includes `log_tail`. `check`, `progress` and `readiness` declare `outputSchema` and return `structuredContent` with explicit `ok`/error semantics, while keeping the text and `details` ordinary calls and evidence readers use. A failed check still records its immutable receipt. `progress` and `readiness` call the frozen `pi_task.py`.
- **Prompt.** `before_agent_start` adds the contract as the `codex_pi_worker` system prompt section on every run.
- **Settle.** `agent_before_settle` runs `readiness`; when `settle.eligible` it claims the per-phase quota file (`settle/<hash>.claim`, exclusive create) and returns `continue: true` with one entry listing the missing items. Eligible means: every gap is missing evidence (no failed, skipped or unknown item), scope and resources fine, at least 60 s of phase budget left, no board pause, quota unused. Any error means no continuation.

## Host continuation

`pi_board.py` projects bounded execution/check evidence into a shared local board. The Pi supervisor refreshes it roughly every 15 seconds and on terminal exit. It enqueues only actionable events (a delivery card of at most 1200 UTF-8 bytes; ordinary progress milestones stay on the board) to the exact registered desktop task UUID. Idle delivery starts a turn; busy delivery waits for the current turn to end. A local observation does not call a model.

Event content and main decisions are distinct from queue claims. Claims use a short file lock, released before the bounded CLI invocation. A confirmed send is not repeated while review is pending. An ambiguous post-spawn failure is uncertain and needs explicit recovery; the queue has no caller idempotency key. Short hooks provide interruption pause and recovery evidence. The runtime records only sizes of what it prints to the Codex side (`codex-io.jsonl`); `metrics` aggregates them. The supervisor cannot detect its own death. Tested desktop evidence: [CLI queue validation](../docs/validation/cli-queue-20260926.md).

## Model policy

`load_config` accepts only models in `ALLOWED_MODELS` (`deepseek/deepseek-flash`, `newapi/glm-5.3`, `newapi/deepseek-flash`, `qwen38/qwen38`); the choice is frozen per task at `start`. `qwen38/qwen38` should use Pi's highest thinking setting, `max`. `continue` and the detached worker revalidate the frozen model before touching evidence or launching anything; the spawn argv uses the frozen model and thinking setting and there is no fallback. Pi's reported provider/model comes from raw events and is compared with the frozen one: a mismatch stays visible as `modelCheck: "mismatch"` and the result stays `acceptance: "not_verified"`.

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

## Bounded allocation and cost scope

New task metadata freezes `reviewPolicyPin.earlyTakeover: true`. `pi_board.py takeover` acquires admission/task/supervisor/board leases, verifies terminal execution, recorded process-group release, latest event/current contract and actual HEAD, then persists an explicit main-decision latch and the existing takeover event. `pi_takeover.py` keeps actual quality counts separate from handoff causes, including across the existing archive policy checkpoint. Continue and supervisor admission reuse the existing latch guard. Historical tasks without eligibility retain their original policy. The process inventory helper is shared with runtime adoption; it cannot attest arbitrary unrecorded detached processes.

Summary and board metrics label Pi execution and provider-reported cost. Provider-reported cost, including zero, is not verified billing; `costComplete` only means the reported fields are present. `codexBytes` counts plugin output bytes, not Codex tokens. Report completeness does not imply billing completeness. Full workflow cost is unknown because Codex design/review/takeover and external work are not measured here; project acceptance records hold those sources, include failed attempts, repairs and takeover in the original strategy, and compare only the baselines an adoption decision needs; external implementation is construction evidence, not proof that a product Bot can do it. Scripted or synthetic fixed usage observes requests and bytes only, never a measured token or cost saving. Pi 1.0 `compact()` aborts the current operation first, so validate it only on an idle synthetic session, never from an active worker, and never expose a production compact tool; an active-state control shows only that this plugin does not trigger compact, while native auto-compaction can still run between turns under unchanged default thresholds. One codemode batch may read and filter repeated reads; judge it by real provider request counts and tool-result bytes, never by scripted-provider usage. No session scan is added to board metrics. The default allocation preference belongs to the Skill; runtime admission, ownership and cost behavior are unchanged.

## Lock and liveness model

The supervisor holds `.task.lock` and `.supervisor.lock`; Pi inherits `.task.lock`, so a killed supervisor cannot free capacity while Pi runs. `.supervisor.lock` is never inherited: an active recorded state without a live lease is `unknown`, never `completed`. A parent-side `starting` record while the task lock is held counts as active. `cancel` never signals a PID it cannot prove it owns.

## Limitations

- The runtime validates an existing linked checkout; the main session creates it.
- The guard is a path-token check at the tool boundary, not a sandbox: it cannot see paths built at run time by the shell, and `bash` keeps the user's local capability. A process that detaches from its group escapes Pi's timeout kill.
- The model restriction rejects other IDs and surfaces reported mismatches but cannot attest the upstream provider.
- An orphaned Pi or supervisor is never killed automatically; the operator inspects the recorded PIDs.
- Config checks and constraints are text passed to Pi; the runtime never executes them. Acceptance stays with the project's checks and Codex review.
- Unreported provider usage stays `null`/unknown. Requires Python 3.10+, Git, POSIX process groups (macOS/Linux) and Pi >= 1.0.0 (Node runs the extension).

## Network policy and diagnostics (0.8.0)

Optional project config `network` carries exactly `proxyUrl` and `diagnostics`. `proxyUrl` must be a credential-free `http(s)://host:port` URL with a root path and no query or fragment; it is validated before any task side effect, and a rejected value is never echoed. Omission preserves inherited proxy environment behavior; there is no automatic discovery, retry or route switch. `start` freezes the project policy in `task.json`; `continue` applies only that frozen policy, so later project edits affect new tasks only and a task without the key keeps inherited behavior and its frozen helpers. Explicit routing overrides `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and their lowercase forms on the Pi child only so child tool processes inherit it; `NO_PROXY`/`no_proxy` exclusions survive; the supervisor and Codex queue transport keep their own environment.

With `diagnostics: true` the supervisor appends one `--import` option (percent-encoded `file://` URL) to the Pi child's existing `NODE_OPTIONS` and sets `CODEX_PI_NETWORK_DIAG_FILE`, `CODEX_PI_NETWORK_DIAG_SCOPE` and `CODEX_PI_NETWORK_DIAG_SUPERVISOR`. The frozen `pi_network_diagnostics.mjs` observes undici diagnostics channels and appends bounded redacted JSONL to `rounds/N/round.network.jsonl` (relative time/duration, phase, safe class, allowlisted code, CONNECT status and a `primary` flag). Only the supervisor's direct Pi child writes, so a round has one writer and a hard 64 KiB aggregate bound; inherited tool children keep the proxy policy but never append. Classification scans a bounded error/cause chain, ranks explicit proxy evidence above ambiguous abort codes, accepts proxy evidence only from usable messages, treats only an explicit `AbortError` as ordinary cleanup, and leaves unknown evidence `unknown`. It never stores URLs, hostnames, headers, bodies, prompts or raw exception text; write failures are harmless. The reader trusts only a bounded UTF-8 prefix of allowlist-valid records: missing, oversized, truncated, non-UTF-8 or forged content is `unreadable` with empty counts and forged values are never echoed. `status`/`result` include a compact `network` block with sanitized proxy mode/source/origin and the sidecar reference/counts; an untrusted or missing sidecar is unknown, not healthy. Remediation: a pre-TLS reset points to transport; a CONNECT 503 identifies proxy tunnel failure; validate or explicitly switch the user-configured route and never promise permanent repair. Native Pi retry configuration is separate and is not modified.

## Tests

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Tests use the offline Pi double in `tests/doubles/`, which plays the extension's load proof; the extension itself is driven through Node's native type stripping with a fake ExtensionAPI (`tests/doubles/extension_harness.mjs`). A missing `node` fails the suite. No live model call, no network, no Codex binary. `tests/test_modules.py` guards the module layout: size cap, acyclic imports, entry-module isolation, a complete frozen helper list and a frozen-tools run with the plugin directory removed.

## Reviews and takeover

New tasks allow at most two complete Pi deliveries; the first reviewed quality failure requires the same Codex main session to reassess the outcome before choosing same-session repair or an explicit early takeover. The second failure transfers implementation to that session. Historical tasks retain their frozen eligibility and limits. Counting is task-scoped since the last accepted outcome: one exact main decision per distinct round on a `review_required`/`phase_blocked` event (`changes_requested`/`reject`, `failure-kind quality`); duplicate events, checks, progress, the in-round settle continuation and explicitly external blockers do not count. A reached takeover is latched for that task; the board persists `codex_takeover_required` and `continue` and the worker refuse Pi. The runtime counts exact decisions and controls ownership; it cannot prove analysis quality and never starts Codex.

## Check resource admission (0.7.1)

The worker owns one in-memory FIFO permit pool; there is no second persistent scheduler. Optional phase `checkExecution` (`maxConcurrent` 1..4, `cpuSlots`, `memoryMiB`) and item `checkResources` (`parallelSafe`, `cpuSlots`, `memoryMiB`, `exclusiveKeys`) reach the worker through the frozen config, and metadata follows the normalized command argv; contradictory declarations for one argv are refused. Missing declarations/pressure use exclusive admission. Explicit independent checks share bounded count/CPU/memory estimates and disjoint resource keys. A waiting caller may expire or abort without spawning. Permits are released in finally, and pressure, candidate cleanliness and the original round/codemode deadline are revalidated after waiting. Results include `queueSeconds`, `resourceMode`, `pressureState`, `pressureSynthetic` and a pool snapshot; `CODEX_PI_PRESSURE_SNAPSHOT` is a marked synthetic probe override that ordinary production checks should not set.

macOS reads the kernel VM pressure level; Linux uses bounded MemAvailable/MemTotal reads and labelled availability headroom. These are admission snapshots, not RSS enforcement or a host-wide guarantee. Existing directory-byte resourceLimits remain disk limits. Tests use real helper subprocess intervals and the native Pi/QuickJS scripted provider; production identity/session/evidence ownership is unchanged.
