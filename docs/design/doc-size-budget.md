# Model-visible guidance size budget (0.8.6)

## Outcome and boundary

Deliver a coherent 0.8.6 maintenance outcome in which the four model-visible collaboration documents fit
frozen byte budgets **without losing one load-bearing rule**, guarded by a deterministic test, and in which
the consuming project stops pushing a Codex-side policy document into every Pi round.

Observable on completion:

- `skills/collaborate/SKILL.md` and the three `skills/collaborate/references/*.md` files are each within
  their frozen budget, total **≤ 21,504 bytes** (from 42,692 measured at the baseline, −49.6%).
- `tests/test_docs.py` exists and fails a future regression in budget, link integrity, documented-command
  reality, mechanism single-home and project-constraint direction.
- Every rule in the inventory below still resolves to exactly one home after the change, or is marked
  `DELETE` with a same-address-duplicate reason.
- `.agents/codex-pi.json` lists `AGENTS.md` and design documents as project constraints, never
  `skills/collaborate/SKILL.md`.

Explicitly outside: no change to runtime algorithms, acceptance, counting, budget, ownership, model policy,
receipts, transport or hooks; no claim about model behaviour, speed or cost (that would need a controlled
experiment); no public push, install or cache edit.

## Measured baseline (`b73ae86`, VERSION 0.8.5)

| Model-visible file | bytes | loaded when | ≈ tokens |
| --- | ---: | --- | ---: |
| `skills/collaborate/SKILL.md` | 15,579 | every `$collaborate` activation, whole file | ~4.0k |
| `skills/collaborate/references/runtime.md` | 16,099 | each dispatch/review cycle for syntax | ~4.1k |
| `skills/collaborate/references/task-packet.md` | 7,564 | each dispatch preparation | ~1.9k |
| `skills/collaborate/references/handoff.md` | 3,450 | each actionable event | ~0.9k |
| total | **42,692** | one dispatch cycle ≈ 39.2 KB | ~10.0k |

Pi-side fixed per-round inputs measured on a real task: `contract.md` 8,126 B and `worker.json` 11,268 B
(the contract text is carried twice). `runtime/IMPLEMENTATION.md` is 30,263 B and `README.md` 30,602 B.

Facts verified at the baseline, not assumed:

- Cross-document verbatim duplication is near zero: a 10-gram overlap scan over the four documents returns
  at most 8 shared shingles (`runtime.md` ↔ `IMPLEMENTATION.md`). Bloat is semantic accumulation, not copy-paste.
- `runtime.md` declares "Usage only" but holds only 2,329 B of command blocks; 13,770 B is prose, of which
  `The worker round` (3,418 B) duplicates the same-titled `IMPLEMENTATION.md` section (3,743 B).
- No test reads any of the four documents. The only document coupling in the suite is
  `tests/test_step4.py:245`, which asserts the `runtime/VERSION` string appears in `README.md`.
- All 71 relative Markdown links in the repository resolve; `skills/**` documented CLI invocations all match
  the real `--help` surface (the only two misses are README lines documenting deliberately removed commands).
- Mechanism markers already present in `runtime/IMPLEMENTATION.md`: `worker.json`, `worker.ready`,
  `WORKER_EXTENSION_NOT_LOADED`, `agent_before_settle`, `forbidden-path`, `recover-store`, `targetedCommand`,
  `checkExecution`. Marker `CODEX_PI_NETWORK_DIAG_SUPERVISOR` exists in **no** maintainer document: the
  transport-diagnostics mechanism is currently described only in a model-visible file.
- `docs/validation/recovery-0.6.1-20261002.md` already documents `recover-store`;
  `docs/design/necessary-verification.md` is already the authority for verification selection and evidence reuse.

## Frozen budgets (the gate)

| File | budget | from |
| --- | ---: | ---: |
| `skills/collaborate/SKILL.md` | 8,192 | 15,579 |
| `skills/collaborate/references/runtime.md` | 6,144 | 16,099 |
| `skills/collaborate/references/task-packet.md` | 5,120 | 7,564 |
| `skills/collaborate/references/handoff.md` | 2,048 | 3,450 |
| total | **21,504** | 42,692 |
| `runtime/IMPLEMENTATION.md` (receiving home) | **≤ 42,000** | 30,263 |

The `IMPLEMENTATION.md` cap prevents "shrunk the visible file by hiding the text elsewhere": moved prose has
about 11.7 KB of headroom, which is enough for the two sections this phase moves and nothing more.

## Single home map and allowed dispositions

Ownership: rule → `SKILL.md`; command syntax → `references/runtime.md`; event handling →
`references/handoff.md`; packet templates and must-ask list → `references/task-packet.md`; mechanism and
limits → `runtime/IMPLEMENTATION.md`; recovery and validation history → `docs/`; version history → `README.md`
plus `docs/validation/`.

Each inventoried rule receives exactly one disposition:

- `KEEP` — stays in the model-visible file, wording may tighten, normative strength unchanged.
- `COMPRESS` — several argumentative sentences collapse into one imperative rule; every clause survives.
- `MOVE→target` — the only full description lands in the named maintainer document; the model-visible file
  keeps at most a one-line pointer.
- `DELETE(reason)` — removed because the same assertion already has another home at the same address.
- `WEAKEN` — **forbidden.** Acceptance, counting, budget, ownership, `unknown is not zero` and freeze
  semantics may not lose strength, scope or exception clauses.

## Rule inventory (the acceptance instrument)

Because no test reads these documents, this numbered inventory is what proves the change is a relocation and
not a loss. Main fills the `→ new location` column during review. Short claims are pointers to the source
line, not replacements for it; the source line is authoritative.

### `SKILL.md`

| ID | Load-bearing claim (abridged; source line) | Disposition |
| --- | --- | --- |
| S1 | Existing Codex main task allocates, designs, dispatches, reviews, takes over; Pi implements a bounded outcome; no extra Codex agent, nested model or automatic model review; Codex CLI is `queue` and plugin management only, never `exec`/`resume`/`fork` (L12) | KEEP |
| S2 | Project config selects one `ALLOWED_MODELS` entry; `qwen38/qwen38` uses thinking `max`; frozen at `start`; later edits affect new tasks only; no fallback, an unavailable model fails the round and keeps evidence; Pi ≥ 1.0.0 (L13) | COMPRESS |
| S3 | Worker runs with extensions, skills and prompt templates disabled; user goal and project contracts govern scope and acceptance; config constraints and checks are references, never success claims; routine choices need no extra approval gate (L14) | KEEP |
| S4 | codemode usage discipline: simple calls direct; batching/branching/filtering in a script; nested `tools.*` keep the same guard; await every call and inspect structured failure — a fulfilled promise is not success; parallel checks need independent resources and unique ids; dependent writes/commits serialize (L14) | MOVE→runtime.md |
| S5 | New workers serialize checks unless the frozen phase declares a bounded `checkExecution` pool plus complete `parallelSafe` `checkResources`; measure 1/2 lanes before 4; count child threads and memory; shared resources exclusive; does not raise `maxWorkers`; no host-wide isolation (L14) | MOVE→runtime.md |
| S6 | Whole-task analysis is the single reusable design act: done before first dispatch, reused on every delivery, updated before takeover; no matrix, schema or hash gate (L18, L20) | KEEP |
| S7 | Define the complete outcome first; prefer the configured Pi model for the largest self-contained result when facts are accessible, consequential interfaces and invariants are settled, genuine feedback exists and authorized resources suffice; route by time to accepted delivery and all attributable costs; token share, call share, file count and confidence are never routers; a complete read-only investigation qualifies (L18) | KEEP |
| S8 | Codex resolves consequential unknowns before dispatch; Pi keeps freedom to read source and choose routine implementation; avoid whole-history prompts and mechanical fragments (L18, L23) | KEEP |
| S9 | Direct Codex completion and early takeover need a concrete task-specific reason recorded in the existing design or review record, not a new approval form (L18) | KEEP |
| S10 | Analysis content: goal → phase → remaining authorized plan; decision-critical source, shared invariants, failure/recovery/ordering boundaries, evaluation validity; separate verified facts / assumptions / unknowns; per consequential difficulty solution → decision → failure or unlock path → verification (L22–L23) | KEEP |
| S11 | On a delivery, review the exact candidate and evidence, find symptom and shared cause, assess effect on remaining tasks, fold into one route; a pass may only confirm the plan (L24) | KEEP |
| S12 | Each delivery proves a bounded coherent observable result; refine oversized phases into smaller frozen contracts; files, layers and happy paths do not define completion; acceptance, counted failures, pause and budgets survive refinement (L28) | KEEP |
| S13 | Establish real entrypoints, data forms and state/ordering/restart sequences from source, including async error returns and revalidation after waits; missing or unknown coverage is not a pass; a shared rule has one owner and one implementation, proven through its public interface first (L29) | KEEP |
| S14 | Where an evaluator controls acceptance, calibrate against independent expected facts first; prior records are calibration, not proof (L30) | KEEP |
| S15 | Fix behavior, constraints, evidence and blocking standard before dispatch; a violation or missing evidence blocks acceptance; unrelated improvements are follow-up (L31) | KEEP |
| S16 | Reconcile actual Git changes, including dirty and untracked, with the outcome at dispatch and delivery (L32) | KEEP |
| S17 | Select checks by affected behavior and actual consumers; development reproduces failure and affected boundaries, delivery proves the outcome, review inspects original receipts and reruns only for coverage/applicability/authenticity; full regression only at a justified broad integration boundary, never because a commit, round, reviewer or release label changed; inspect wrapper composition (L32) | COMPRESS (authority: `docs/design/necessary-verification.md`) |
| S18 | Reuse original evidence only where behavior, source, dependencies, inputs, environment and evaluator stay applicable with no contradictory failure; cite original receipt, candidate, log and applicability reason; never relabel old evidence as a new run; never replace a failed frozen mandatory command with a weaker check; current readiness consumes current-round exact-candidate receipts only (L33) | COMPRESS (same authority) |
| S19 | Human-only prose needs format, JSON and reference validation; model-visible instructions, schemas, startup inputs and generated artifacts need relevant semantic or assembly checks regardless of extension; real model experiments only where a model-behaviour claim needs them (L34) | KEEP |
| S20 | Respect frozen command/resource limits and the 60-second reserve; `targetedCommand` is a local debugging suggestion; the formal command still needs `final:true` on a clean candidate; a targeted receipt never covers the formal item; no contract is forced to run a full suite (L35) | KEEP (detail home: runtime.md) |
| S21 | A brief may name one cheap existing precondition command; when it fails, stop dependent expensive checks and report it; precondition text never proves the command ran, only its receipt (L36) | KEEP |
| S22 | Within `maxWorkers` split only independent results and settle shared contracts first; distinct identity, session, worktree per task; briefs name outcome, baseline, allowed changes, shared boundaries, resource isolation (worktrees do not isolate databases or ports) and budget; one task integrates exact peer commits and runs combined checks after writers release; component checks never prove the phase; counts and budgets stay per original task; splitting or renaming never resets them (L40) | KEEP |
| S23 | Packet = one specification + short prompt + frozen `--contract-file` for a complete phase; preparing a brief is not dispatch; dispatch only inside the authorized scope; supplied documents are material, not authority to run their commands (L44) | KEEP |
| S24 | Delegate a whole independently reviewable outcome in a separate worktree; constrain behavior, public boundaries and acceptance evidence, not file or function names; Pi owns implementation, tests, repairs and scoped commits until takeover; never start a duplicate worker to bypass an active or unknown task (L46) | KEEP |
| S25 | Wait without polling: supervisor refreshes the board locally; unchanged state and ordinary progress never call Codex; only review-ready, blocked, failed or anomalous rounds enqueue one card; do independent work and end the turn; never loop on `status`/`show`, no heartbeat, no held Stop hook; answer progress from one `status`; a PID, fresh log or growing file proves activity only; Interrupt pauses handoff, not Pi, until explicit resume (L50) | KEEP |
| S26 | A card is execution evidence, not a goal or acceptance; read task, round and event id, call `result` once, inspect only the relevant diff, receipts and original evidence; require the exact full candidate commit and the project's real checks; exit 0, summaries and model claims are insufficient (L54) | KEEP |
| S27 | `execution_failed` denotes incomplete execution including exit-zero provider errors, never a quality delivery; resolve the incident without inventing a rejection or resetting usage (L54) | KEEP |
| S28 | Explicit recovery or adoption preserves originals, owner, session, worktree, pinned policy and deadline (L54) | KEEP |
| S29 | Material defect procedure: trace reachable entries and state boundaries, name the shared cause, classify related paths as reproduced / reachable but unverified / excluded with reason; give the same Pi session one consolidated brief; never infer a defect from a missing test; an active round keeps its immutable brief; record the decision for the exact event; keep failed attempts, logs and usage; delivery, handling and acceptance are separate states (L56) | KEEP |
| S30 | A delivery is a completed report reviewed by the main task, not a tool call, red test, progress echo or round number; new tasks allow at most two complete Pi deliveries and freeze early-takeover eligibility; revisions, renames, pause/resume, retries and config edits cannot raise a pin (L60) | KEEP |
| S31 | Count one exact main decision per distinct round on a `review_required`/`phase_blocked` event decided `changes_requested`/`reject` with `--failure-kind quality`; duplicates, Pi red tests, routine repairs, progress and the in-round settle continuation never count; only a genuine missing external prerequisite uses `--failure-kind external` with a note naming evidence and unlock condition; implementation or evidence defects are never external (L62) | KEEP |
| S32 | `reviewPolicy` derives from exact decisions; after the first quality failure reassess whether another Pi delivery is worthwhile; continue the same session with one consolidated repair or take over explicitly with the exact current event, full candidate and evidence-based reason after all writers release; a design contradiction or execution incident can justify takeover without inventing a quality failure; takeover is ownership handoff, never acceptance, and preserves the real failed-delivery count; historical tasks keep their frozen policy; at the limit the board emits `codex_takeover_required` and Pi is refused; pause/resume and later acceptance cannot clear it; acceptance before the limit starts a fresh count; renaming never resets (L63) | KEEP |
| S33 | Takeover procedure: verify Pi, descendants and supervisor released the checkout (unknown ownership blocks writing); keep candidate, dirty work, evidence and budgets; redo the analysis, update the design, then implement, integrate and verify directly; never hand back to Pi, start another model, weaken acceptance or reset spend; resolve the takeover event as a handoff receipt, never as new code passing old Pi checks (L64) | KEEP |
| S34 | Edit canonical source only, never managed caches, hook trust or a running task's frozen helpers; a new runtime does not migrate 0.5.x tasks (L68) | KEEP |
| S35 | Cost scope: `metrics` covers Pi execution only; reported cost including zero is not verified billing; `costComplete` means fields are present; `codexBytes` counts plugin output bytes, not Codex tokens; full workflow cost stays unknown without separately measured Codex design/review/takeover and external work; keep those in existing project acceptance records; include failed attempts, repairs and takeover; compare only the baselines an adoption decision needs; external implementation is construction evidence, not proof a product Bot can do it (L68, L70) | MOVE→IMPLEMENTATION.md (keep 2 lines) |
| S36 | Scripted or synthetic fixed usage observes requests and bytes only, never a measured token or cost saving; native `compact()` aborts first, so validate only on an idle synthetic session and never expose a production compact tool; an active-state control proves only that this plugin never triggers compact (L72) | MOVE→IMPLEMENTATION.md |

### `references/runtime.md`

| ID | Claim | Disposition |
| --- | --- | --- |
| RT1 | Start, then register immediately; default transport `offline`; `cli-queue` needs a local Codex CLI with `queue`, the desktop app and the exact owner UUID; `--codex-bin` selects the executable; never guess an owner or start another process to deliver; `PI_BIN` selects Pi; project config keys; the worktree is isolated and reserved for the task lifetime (L11) | KEEP |
| RT2 | Every command prints one single-line compact JSON; never interpolate user text into shell commands (L3) | KEEP |
| RT3 | `result` content and "collect once per terminal round"; raw logs, receipts and the native session live under the Git common dir `codex-pi/tasks/`; `show` supplies the absolute evidence paths a card omits (L25) | COMPRESS |
| RT4 | `record-outcome` semantics: bounded immutable numbered revisions, explicit same-repository members only, idempotent normalized replay, correction requires the current `--expected-revision`, stale or conflicting updates change nothing, never inferred by name or predecessor (L27) | MOVE→docs/validation/outcome-observations-0.8.5.md |
| RT5 | `metrics --outcome` is a six-section read-only projection; `--outcomes` is a bounded listing, never a denominator or success rate; a closeout is never acceptance, ownership or permission; Pi-native and Main-reported facts stay separate; unknown is not zero; intervals use unions; reported interface/SDK cost is not billing; no record or read command executes, scans a session, or changes execution, decisions, budgets, pause, quality counters or notifications (L27) | COMPRESS |
| RT6 | `continue` keeps the pinned session and worktree in a new immutable round and refuses while paused until explicit resume; `cancel` signals only the owned worker group; verify it ended before repair (L36) | KEEP |
| RT7 | Launch argv shape, `worker.json` contents and the load proof `WORKER_EXTENSION_NOT_LOADED` (L40) | MOVE→IMPLEMENTATION.md (already there) |
| RT8 | Guard rules: `forbidden-path` with symlinks resolved, `outside-worktree`, `undecidable`, `worker-blocks.jsonl`, bash default and ceiling, codemode top level only with every nested `tools.*` re-guarded, `// @options` handling, deadline clamp and output bound (L42) | MOVE→IMPLEMENTATION.md (keep rule names in one line) |
| RT9 | `check` semantics: receipt bindings, `log_tail` bound, deadline re-read with one final effective window and the 60-second reserve, structured `ok:false` with `receipt:null` and no spawn, `elapsedSeconds`/`allowedSeconds`/`estimateSource`, codemode outer deadline, `targetedCommand` versus the formal command with `final:true`, `outputSchema`/`structuredContent`, a failed check still writes its receipt (L43) | MOVE detail→IMPLEMENTATION.md; KEEP admission outcome |
| RT10 | Contract reaches the model as a system prompt section every run and `contract.md` every round; settle continues once per phase from the quota file (L44–L45) | MOVE→IMPLEMENTATION.md |
| RT11 | A complete phase freezes with `--contract-file`; the project PLAN stays authoritative and the contract is not a second PLAN (L49) | KEEP |
| RT12 | Readiness consumes current-round exact-candidate receipts only; missing, failed, skipped or unknown is never ready; there is no automatic cross-round or cross-head PASS reuse — select necessary commands at freeze time and cite applicable original component evidence in review; no PASS is cached; a formal command may be a bounded component check (L57) | KEEP |
| RT13 | Progress is Pi's self-report, never acceptance; ordinary progress stays on the board and only a sustained unrepaired check failure may enqueue, at most twice per phase; `accept` binds phase, contract, candidate and live round state and refuses any mismatch; a phase command timeout may not exceed `commandTimeoutSeconds`; declared `resourceLimits` stop only the owned group on a known breach and an incomplete measurement stays unknown (L57) | KEEP |
| RT14 | Explicit 0.6 recovery: use the installed CLI; store conversion requires released writers and retains original JSON, events, decisions and claims; adoption prepares a separate immutable runtime for future rounds and never edits old tools, resumes, changes model or extends budget; 0.5 workers are not adopted; an `execution_failed` incident is handled separately from quality review and an exit-zero error cannot be accepted (L70) | MOVE→docs/validation/recovery-0.6.1-20261002.md (keep command block + pointer) |
| RT15 | `--failure-kind external` requires a note naming the unlock condition; counting rules live in the Skill (L78) | KEEP |
| RT16 | Takeover prerequisites: frozen early-takeover capability only; exact latest delivery or incident event, current contract and actual HEAD must agree; admission, task and supervisor leases plus recorded process groups must be released; active, unknown, stale, accepted or conflicting outcomes refuse; identical replay is idempotent; `codex_takeover_required` carries the ownership receipt resolved by `decide resolve`; taking over neither accepts the candidate nor fabricates a rejection and a later resume cannot return implementation to Pi (L86) | KEEP |
| RT17 | `metrics` cost scope and `codexBytes` limits (L88) | DELETE(reason: same as S35, single home) |
| RT18 | `checkExecution` (`maxConcurrent` 1..4, `cpuSlots`, `memoryMiB`) and per-item `checkResources` (`parallelSafe`, `cpuSlots`, `memoryMiB`, `exclusiveKeys`); metadata follows normalized argv and conflicts refuse; new workers serialize by default; parallel needs complete estimates and a configured pool; FIFO waiting consumes the original deadline with cleanliness, pressure and budget revalidated before spawn; result fields; soft per-worker bounds, not host-wide limits; `Promise.allSettled` only for declared independent checks; validate 1/2 lanes before 4; `CODEX_PI_PRESSURE_SNAPSHOT` is a marked synthetic probe (L92–L96) | COMPRESS (keep schema, default serial, 1/2 lanes, soft-bound disclaimer; MOVE rest) |
| RT19 | Network policy and diagnostics: exactly `proxyUrl` and `diagnostics`; credential-free `http(s)://host:port` with root path and no query or fragment; validated before any task side effect and a rejected value is never echoed; `start` freezes the policy and `continue` applies only it; explicit routing overrides the Pi child's proxy variables while `NO_PROXY` survives and the supervisor and queue keep their own environment; with diagnostics the supervisor adds one `--import` option and three sidecar variables, only the direct Pi child writes, one writer and a 64 KiB bound, bounded cause-chain classification, explicit `AbortError` only as ordinary cleanup, strict redaction, unreadable-is-unknown, and remediation that never promises permanent repair (L100–L106) | MOVE→IMPLEMENTATION.md (author there); KEEP grammar + "unknown ≠ healthy" + remediation line |

### `references/task-packet.md`

| ID | Claim | Disposition |
| --- | --- | --- |
| TP1 | Allocation preference restated before preparing a packet (L6) | DELETE(reason: same as S7/S9, single home) |
| TP2 | Artifacts: specification; brief is the short prompt that points at the spec and acceptance IDs and never copies the design or the Skill; a repair writes a new brief; a dispatched brief is never rewritten; the contract is a frozen snapshot, not a second PLAN; the delivery report is Pi's final message (L10–L13) | KEEP |
| TP3 | Specification template sections (goal/plan, baseline-scope-facts, design-difficulties-verification, parallel line, acceptance table, repair-budget-escalation) | KEEP |
| TP4 | Must-ask checklist: which record or version wins by content only; byte-identical rerun and replay; an existing or same-name target; dependence on time, timezone, environment or network; empty versus missing versus unknown, never zero, block and list them (L56–L61) | KEEP |
| TP5 | Writing convention (one reader, one actor, one action per sentence, condition next to the action it limits, one term per thing, exact negation/permission/uncertainty, no imposed word count) | COMPRESS |
| TP6 | Dispatch prompt template including exact `checkId` or `id`, when to use `targetedCommand`, `final:true` on the clean candidate, allowed inputs and forbidden sources, the escalation clause, and the conservative reading plus spec gap when a must-ask item is open | KEEP |
| TP7 | Mapping from the specification acceptance table to the frozen contract items | KEEP |
| TP8 | Delivery report format | KEEP |

### `references/handoff.md`

| ID | Claim | Disposition |
| --- | --- | --- |
| HD1 | Card composition and the ~15 s local refresh; idle resumes, busy handles after the turn (L3) | COMPRESS (rule home is S25) |
| HD2 | Handle one event: read ids, reuse a `result` already collected, verify diff and original receipts, then decide; `changes_requested`/`reject`/`resolve` meanings; acceptance needs the full real candidate; a decision on an old round never accepts the current one; `+N pending event(s)` means `show` and handle all in the same turn; a repeated delivered event is a dedup receipt; a resource breach or deadline gets a bounded question, not an endless retry | KEEP |
| HD3 | Evidence-based early ownership handoff uses `takeover` with the current event, full candidate and reason; it rechecks released writers, contract and HEAD, preserves real counts and emits the same event, which is resolved as an ownership receipt (L9) | KEEP |
| HD4 | Pause and uncertain delivery: pause stops dispatch, not Pi; prompts and progress questions never resume it; a queued card does not override a later pause; an uncertain send may already have arrived, so keep the claim, inspect, and recover explicitly only if needed; `rearm` may duplicate because the queue has no idempotency key and never rearms a live inflight send or steals locks; hooks only check pause and recovery; a dead supervisor cannot report itself | KEEP |
| HD5 | Updates: formal install only, never edit managed caches, hook trust or app queue databases; a running task keeps its frozen helpers; a 0.5.x task finishes with them; registering a terminal task may enqueue a review event at once | COMPRESS |
| HD6 | Unit tests do not prove desktop delivery; run one real bounded tiny Pi task and observe completion, supervisor, the same desktop task and a visible reply; `register` reports `routePaused` with the exact `resume` command; an interrupted idle desktop task keeps a queued card until reopened | COMPRESS (pointer to `docs/validation/`) |

## Guard to add: `tests/test_docs.py`

Deterministic, offline, under one second, no model and no network:

1. **Budget** — the frozen table above per file plus the total, plus `IMPLEMENTATION.md ≤ 42,000`.
2. **Link integrity** — every relative Markdown link under `skills/`, `docs/` and `runtime/` resolves.
3. **Documented commands are real** — build the subcommand and flag sets from
   `pi_task.py`/`pi_board.py` `--help` and assert every invocation appearing in `skills/**` exists.
4. **Mechanism single home** — the markers `worker.json`, `agent_before_settle` and
   `CODEX_PI_NETWORK_DIAG_SUPERVISOR` occur zero times in the four model-visible files and at least once in
   `runtime/IMPLEMENTATION.md`. (The first two already satisfy this in `IMPLEMENTATION.md`; the third must be
   authored there by this phase. `worker.ready` and `forbidden-path` may still be *named* in `runtime.md`, so
   they are not markers.)
5. **Constraint direction** — `.agents/codex-pi.json` lists `AGENTS.md`, never references
   `skills/collaborate/SKILL.md`, and every listed constraint path exists in the repository.

Stage 1 lands checks 1–4 with the document cuts; stage 2 lands the project-config change with check 5. A
check may not be weakened, skipped or satisfied by a wording-match test.

## Budget revision (main, after takeover)

The candidate `6da8156` met the frozen budgets but the independent review found three current rules
missing, of which one was a whole command surface: `pi_task.py progress` and `pi_board.py rearm / refresh /
pause / resume` had no syntax anywhere except the pre-0.6 archive, and `tests/test_docs.py` could not see it
because its command check ran documented→real only. Restoring that coverage costs bytes the pre-review
estimate had not reserved, and the alternative — deleting rules to hit 21,504 — is the `WEAKEN` disposition
this design forbids.

Final integrated content is **22,373 bytes** (−47.6% against the 42,692-byte baseline), and the guard now
pins per-file caps at 8424 / 7016 / 5312 / 2132 with the total at 22,784, which leaves ≥128 bytes of slack
per file so a later release cannot be blocked by a one-byte-full document. The original −50% figures stay
above as the record of what was estimated, not what was delivered. The remaining gap to 21,504 can only be
closed by moving rarely-needed sections (`0.6 recovery`, `network policy`) into an on-demand reference, which
changes the metric from per-file bytes to dispatch-cycle loaded bytes and needs a separate decision.

## Loaded-set accounting (0.8.7, main, the deferred-tier decision)

That decision is now taken: the budgeted quantity is the text a round actually loads, not the size of a
folder. A normal dispatch, check or review round reads `SKILL.md`, `references/runtime.md`,
`references/task-packet.md` and `references/handoff.md`; those four are budgeted together at **21,504 bytes**
(the accepted −50% figure, restored as a real invariant) and currently measure **21,358 bytes** (−49.97%).
`references/runtime-ops.md` forms a second tier with its own cap, 4,608 bytes at first and 5,120 bytes once
the session closeout surface moved in, and is read only when one of its stated conditions applies: store repair or pre-0.6 conversion/adoption, declaring or changing parallel
check resources, closing out or comparing an outcome, or classifying a proxy/transport loss.

Deferral, not deletion, is the only allowed operation here, and it is guarded three ways: every
`references/*.md` must be declared in exactly one tier, every on-demand file must stay linked from
`SKILL.md`, and the reverse subcommand-coverage check still reads all of `skills/**`, so a live command that
loses its only current syntax fails the build whether it was cut or moved. Nothing was deleted to reach the
number: the deferred text (guard-rule names, network value rules and triage, six-section closeout limits,
check-admission fields, adoption and store-repair commands) resolves in `runtime-ops.md` or
`runtime/IMPLEMENTATION.md`, and `metrics`/`record-outcome`, `events`, `decisions`, `takeover` and the
decision flags stay in the loaded set because main uses them every round.

## Check ladder and time budget (3600 s phase, 900 s command cap, serial checks, one writer)

| Role | Command | Expected wall |
| --- | --- | ---: |
| child precommit (repeat on changed tree only) | `env PYTHONPATH=tests python3 -m unittest test_docs test_modules test_step4 -v` | ≈ 10 s (measured: `test_modules` 1.6 s, `test_step4` 5.6 s) |
| stage 1 gate (frozen) | same argv at the stage 1 clean commit | ≈ 10 s |
| stage 2 gate (frozen) | same argv at the final clean commit, with check 5 added | ≈ 10 s |
| acceptance | `python3 scripts/check_public_privacy.py` | 0.13 s measured |
| acceptance | `git diff --check <baseline> HEAD` | < 1 s |
| acceptance | `python3 runtime/pi_task.py project --repo .` (proves the edited config still validates and prints the new constraints) | ≈ 1 s |
| implementation, commit and buffer | prose relocation, new test, one config line | ≈ 2,400 s |
| repair reserve | | 900 s |

Honest total ≈ 3,500 s ≤ 3,600 s. The full Python suite (475 tests, ≈ 425 s measured) is **not** a required
check for this phase: no runtime module, helper snapshot or shared test support is touched, and a version bump
alone is not a reason for full regression. If the candidate turns out to change `runtime/`, that justification
is void and the full suite becomes required before acceptance.

## Protected scope

`runtime/**` algorithms, `hooks/**`, all existing tests except the four version-string assertions named below,
all existing `docs/design/*` and `docs/validation/*` evidence, and this frozen design.

Allowed changes: the four model-visible documents; `runtime/IMPLEMENTATION.md` as the receiving home; the new
`tests/test_docs.py`; one `.agents/codex-pi.json` `constraints` line; release metadata `runtime/VERSION` and
`.codex-plugin/plugin.json` to **0.8.6**; the short new `README.md` release section (which also keeps
`tests/test_step4.py` version-string assertion true); and those existing hardcoded version assertions in
`tests/test_check_budget.py`, `tests/test_context_workflow.py`, `tests/test_runtime_efficiency.py` and
`tests/test_step4.py`, which move from `0.8.5` to `0.8.6` and to nothing else. This follows the 0.8.3
policy-release precedent: guidance text is installed plugin content, so it carries a version.

## Stop conditions

Stop and report, without guessing, when: an inventoried rule cannot survive at its strength inside the
budget; a cut would change acceptance, counting, budget, ownership or model semantics; the guard cannot be
honest; or the phase budget is exhausted. Routine wording, ordering and section merging belong to Pi.

## Report requirements

Exact full candidate SHA, per-file byte counts before and after, the completed disposition table, the guard
test output, receipts for every acceptance command with their real counts and elapsed time, and an explicit
statement of what this outcome does **not** prove (no model-capability, speed or cost claim; no full-suite
regression). Failed attempts and original logs stay in the record.
