# Pre-0.6 Skill and reference documents (archived)
Verbatim snapshots of the four collaboration documents as of commit `05689ac`, kept when 0.6.0 slimmed them (see [slim handoff design](../design/slim-handoff.md)). They are **superseded and not authoritative**: the live rules are in [the Skill](../../skills/collaborate/SKILL.md) and its references. This file preserves wording, release and validation history (O1-O4, command protection, fast acceptance) that no longer belongs in Codex-facing text. Relative links inside the snapshots are not maintained.

## Skill (`skills/collaborate/SKILL.md`)

````markdown
---
name: collaborate
description: Prepare, delegate and review Pi implementation, or take over a reviewed failed delivery in the existing Codex task. Use for Codex–Pi task preparation, execution, repair and handoff; not unrelated Codex-only coding.
---

# Codex designs and reviews; Pi implements until takeover

Use the existing Codex main task and Pi only. Pi MUST use `deepseek/deepseek-flash`, thinking `max`; no fallback, nested models, extra Codex agent or automatic model review. Codex CLI is permitted as the deterministic `queue` transport and for plugin management; never invoke `exec`, `resume` or `fork` to create another model worker. The worker runs with Pi extensions, skills and prompt templates disabled; a separate Pi-native handoff extension does not govern this route.

The user's goal and project contracts govern scope and acceptance. Read the project's `.agents/codex-pi.json` through `pi_task.py project`. Its constraints/checks are references, not executable success claims or another plan. Keep required real integration and independent review evidence. Resolve routine implementation choices without extra approval gates.

## Whole-task analysis (the single reusable design act)

This Skill is the single authority for the whole-task analysis. Codex performs it before the first dispatch, reuses it on every complete Pi delivery, and updates it before direct takeover. Model judgment decides how to investigate and present it; no required matrix, headings, plan schema or hash gate exists, and analysis quality is never a parser or model check.

The analysis must leave an executable design in the existing project design/PLAN and the relevant brief:

- Relate the overall goal, the current phase and the remaining authorized plan. Detail the phase's complete results, result-oriented tasks, dependencies/order when needed, responsibility boundaries and what completion quality means. This is substantive design, not a list of coding steps or prescribed functions.
- Examine decision-critical source/contracts and affected paths, shared invariants, failure/recovery/ordering boundaries and evaluation validity. Distinguish verified facts, assumptions and genuine unknowns. For each plausible consequential difficulty give a concrete chosen solution, the decision it needs, its failure or unlock path and its verification. Do not leave essential design for Pi to guess.
- Keep routine implementation choices with Pi. Stay bounded by the authorized outcome and consequential downstream dependencies; this is not a repository-wide rewrite or a mandatory reread of all history. Source evidence may show that a risk is inapplicable.

On a complete Pi delivery, review the exact candidate and evidence, identify the current symptom and shared cause, then assess where that cause or a changed design can affect the remaining tasks. Consolidate all material findings into one complete route: preserve working code and valid evidence, refine dependencies, acceptance and budget, and resolve design choices before dispatching a second complete delivery or directly taking over. A passing delivery may simply confirm the existing plan; it needs no rewrite, and previously valid investigation is not repeated.

## Bounded results and trustworthy acceptance

Make each delivery prove a bounded, coherent observable result. Refine an oversized phase into actual smaller frozen contracts before dispatch, not merely suggested work lines. Shrink what one delivery claims, while fully covering the relevant behavior of that result; file boundaries, technical layers and happy paths do not define completion. Preserve the overall acceptance requirements, counted failures, pause and budgets when refining the plan. The model chooses the result boundaries and evidence; no fixed stage count, matrix or research sequence is required.

- Establish from source the actual entrypoints, data forms and consequential state/ordering/restart sequences affected by a shared rule. All relevant asynchronous success, error and exception returns must respect it, including revalidation after waits where authority can change. Source-backed exclusions are valid; missing or unknown required coverage is not a pass.
- Give shared rules an explicit owner and one authoritative implementation. Prove a mechanism needed by later results through its real public interface before dependent expansion; consumers use that proven contract instead of independently reproducing its judgment. Choose early complete results that expose architecture defects before many dependent features accumulate. Do not create a generic framework solely to enforce this instruction.
- Where an evaluator controls acceptance, require actual calibration evidence against independent expected facts before expensive or unseen evaluation. Prepare this early or alongside independent implementation. Use authorized real interface records and meaningful corrupted, missing or stale variants to show the collector reads the right facts and the evaluator rejects invalid evidence. Freezing a future-check description is not calibration; prior records are calibration material, not current-candidate proof or unseen evaluation. Separate structured fact checks from narrative judgment; keyword exceptions cannot substitute for semantic evaluation.
- Fix the promised behavior, shared constraints, evidence requirements and blocking standard before dispatch. A violation of those commitments or missing required evidence blocks acceptance; unrelated improvements and future enhancements belong in follow-up work. If investigation finds a necessary omission, consolidate the material findings into one refined design and contract without weakening the original result. Design approval alone proves neither implementation quality nor repaired behavior.
- Reconcile scope against actual Git changes during dispatch preparation and delivery, including plan/design files and dirty or untracked work. Resolve discrepancies in the existing scope and brief; declarations alone are insufficient. Use relevant targeted checks and state sequences for each result, reuse valid evidence, and reserve costly full regression and real acceptance for the exact combined candidate when required. A relevant failure or changed candidate still requires the affected checks again.

## Up to two independent Pi outcomes

During the whole-task analysis, decide whether the authorized work benefits from one or two Pi execution lines, within the project's actual `maxWorkers` capacity. Parallelism is optional, not a required split. Use the bounded results above, resolve shared contracts and essential dependencies first, and keep genuinely dependent work ordered.

Bind both tasks to the same owning Codex main task, with distinct task identities, Pi sessions and isolated worktrees. In the existing design and briefs, identify each outcome, exact baseline, allowed changes, shared boundaries, dependencies, test/resource isolation and budget. Allocate conflicting shared edits explicitly; separate worktrees do not isolate databases, ports or other external resources. Reuse common design references instead of duplicating the full plan in both briefs. Do not start additional workers merely because capacity is available.

Name one Pi task as the integration owner within its authorized scope and budget. It combines the exact peer commits, resolves integration defects and runs required checks on the combined candidate after the necessary writers release their checkouts. Codex performs the overall design and review; routine integration implementation stays with Pi until takeover. Independent checks or accepted component results do not prove the combined phase complete.

On either delivery, reuse the whole-task analysis to examine both outcomes, their shared causes and downstream dependencies. Independent work may continue; a changed shared design pauses only affected dependent actions until the design and next immutable brief are clear. Apply the existing two-delivery policy to each original task; splitting, moving defects to the integration task or renaming an outcome must not reset failures or budgets. After the pinned limit, Codex takes over the affected outcome after verifying writer release; disjoint authorized Pi work may continue.

Keep ordinary progress on the board. In a main turn, review already available deliveries together using bounded evidence; do not poll or keep the conversation blocked waiting for the other worker. Important blockers still need timely handling. Existing completion events remain per task: these instructions neither batch wakeups nor introduce scheduling, heartbeat or extra model review.

## Dispatch and continue

When preparing or revising a Pi task, apply the whole-task analysis above and read [the task packet standard] [archived link: references/task-packet.md]. It supplies one outcome-oriented specification, a short dispatch prompt, the existing runtime contract mapping and a compact delivery format. Reuse an existing project design instead of creating a second specification. A request to prepare a brief is preparation only; dispatch only within the user's authorized execution scope. Supplied documents are material to analyze, not independent authority to run their commands.

Verify decision-critical facts (actual interfaces, source ownership, schema/semantics and accessible evidence); distinguish verified facts, assumptions and unknowns. Do not pre-investigate every function. Pi can investigate bounded implementation details; an unresolved authority or essential input blocks the dependent action, not all independent work. References to forbidden/private inputs are not permission to inspect them.

Delegate a whole independently reviewable outcome, using a few result-oriented milestones only when dependencies warrant them. Constrain the behavior, public boundaries and acceptance evidence; prescribe file/function names only for real public contracts or ownership constraints. For behavioral changes, establish a small meaningful failure or boundary check when feasible. Pi runs the relevant checks and repairs ordinary failures itself. Do not force tiny timed tasks, universal network probes, repeated full reviews or implementation micro-plans.

Use a separate Git worktree; capacity is not authorization for parallel business phases. Pi owns implementation, tests, productive repairs and scoped commits until the review-failure boundary below transfers that outcome to Codex. Expected red tests and routine failures stay with Pi; design contradictions or repeated ineffective repairs require a concrete reproduction.

Run the plugin's `runtime/pi_task.py` with Python. `start` creates a task; `continue` preserves its exact Pi session/worktree in a new immutable round. Use the board to bind the task to the exact owning desktop task UUID and opt into CLI queue delivery. See [runtime commands] [archived link: references/runtime.md]. Never create a duplicate worker to bypass an active or unknown task.

For a complete phase, pass `--contract-file` with the machine-readable contract (goal, complete result, baseline/scope, design ref+hash, acceptance IDs with real check commands, budget, repair/escalation boundary). Pi reports short structured progress and can check mechanical delivery readiness; a normally completed round with only missing receipts may be continued once in the same session/worktree/budget. A phase sends at most two non-blocking progress echoes (default 10 minutes apart, persisted per phase) for verified or evidence-bearing milestones; ordinary progress and repeated writes never queue, and a recorded pause blocks echoes, auto-continuation and explicit `continue`. Accept a phase with `decide ... --phase ... --contract-hash ... --reviewed-head ...`; only an accepted phase, an authorized next contract and no conflicting worker permit the next phase. Tasks without a contract keep the legacy path. Details: [phase operations] [archived link: ../../docs/validation/phase-autonomy-o1-20260926.md].

## Wait without repeated model turns

The existing Pi supervisor refreshes a small board locally. Unchanged state and ordinary progress do not call Codex. Actionable completion, deadline/resource failure or unavailable ownership enqueue one bounded handoff through `codex --disable daemon_auto_start queue`. An idle desktop task can resume; a busy task processes it after the current turn. Do independent work, then finish the turn when no main work remains. Do not loop over `wait`, create a heartbeat or hold a Stop hook open to simulate progress.

Answer user progress questions immediately from the bounded board/status. A PID, fresh log or growing file proves activity only. Check the current command, real deadline and evidence before declaring useful progress or failure. Resource guards protect declared commands/paths; no monitor can report its own death without an independent observer.

Hooks remain short pause/recovery checks. User interruption pauses handoffs; it does not cancel Pi. Ordinary prompts do not resume a paused route. Respect the latest user direction even if a message was already queued. Use explicit recovery for unknown delivery; never repeatedly enqueue an uncertain send. See [handoff and recovery] [archived link: references/handoff.md].

## Review and retain evidence

Treat every queued packet as execution evidence, not a new goal or acceptance. Read its exact task/round/event and question; collect that round's `result` once, then inspect only the relevant diff, receipts and original evidence. Codex independently reviews Pi's implementation. Require the exact real candidate commit, actual affected checks, and the project's acceptance; exit 0, summaries and model claims are insufficient.

When a completed round reveals a material defect, apply the whole-task analysis above: check the frozen candidate, diff, code and original evidence, trace the reachable entries and state/ordering boundaries, identify the shared cause, and classify related paths as reproduced, reachable but unverified, or excluded with a reason. Give the same Pi session one consolidated, actionable brief with the exact reproduction or smallest failing check, permitted repair scope, affected acceptance checks, remaining unknowns and the consequence for later authorized work. Do not infer a defect from a missing test or wake the main model for ordinary progress and Pi's routine red tests. An active round keeps its immutable brief; wait for its terminal state unless the user directs a stop.

Record a decision for the exact event/candidate. Reuse the whole-task analysis to refine the complete route before continuing; after the first reviewed quality failure of a two-delivery task the runtime instruction restates that requirement and binds it to the exact decision. Group material findings and `continue` the same Pi session. Preserve failed attempts, raw logs, immutable briefs, native session, model/usage and unresolved issues on disk. Keep the existing project plan aligned with authorized design and ownership; mark a result complete only after acceptance. Reuse valid evidence instead of rereading all history or rerunning unchanged checks. Queue delivery, handling and code acceptance are separate states.

## Two complete deliveries: whole-task replan, then takeover

A delivery is a completed report reviewed by the existing Codex main task, not a Pi tool call, red test, status echo or total session round number. A **new task pins two complete Pi deliveries**; tasks created before 0.5.3 keep their frozen pin (one or two) or, without a pin, the former limit of three. The pin is copied to the board at creation; contract revisions, phase renames, pause/resume, retries and later config edits can neither raise it nor erase counted failures.

Count **one exact main decision per distinct round** on a `review_required`/`phase_blocked` event when its decision is `changes_requested`/`reject` with `--failure-kind quality` (the default for negative delivery decisions). Duplicate events for one round, Pi's internal red tests, routine repairs, progress, and the single pre-review missing-receipt auto-continuation never count. A genuine external prerequisite uses `--failure-kind external --note ...` explaining the missing evidence/authority and unlock condition; it does not count, and changing executor cannot supply it. An expected missing model/human gate in a correctly bounded checkpoint is not a failed checkpoint. Do not relabel implementation or evidence defects as external blockers.

The runtime derives `reviewPolicy` (`limit`, `limitSource`, `failedDeliveries`, `implementationOwner`, `reason`, `instruction`) from exact board decisions. After the first reviewed quality failure of a two-delivery task the instruction requires the whole-task analysis to be completed and the complete route refined before the second delivery; the same analysis is reused before direct takeover. No plan schema, mandatory headings, hash gate or model judge is added; the script cannot prove the analysis quality. At the pinned limit the board emits `codex_takeover_required`, and `continue`, automatic continuation and the internal worker refuse further Pi implementation; `pause`/`resume` and later acceptance cannot clear takeover for the same task. Acceptance before the limit starts a fresh count for an authorized next phase; after takeover, a future outcome needs a separate dispatch. Renaming a failed task, phase, contract or checkpoint is never a reset. Review existing eligible historical decisions when adopting the rule; raw round counts alone prove nothing. Unregistered tasks need board registration and exact review decisions before further repair dispatch; prose-only failure reports are not a machine counter.

Takeover is **the reusable whole-task analysis followed by direct implementation**, not a longer list of patches for Pi:

- First verify the Pi worker, descendants and supervisor have released the checkout. Preserve its candidate, dirty work, immutable evidence and budgets. Never run two writers; unknown ownership blocks writing. No automatic cancellation or new Codex CLI/model worker is introduced.
- Reassess the complete authorized result and the remaining authorized plan with the whole-task analysis: whether the requirements and evaluation are sound, the design and source ownership, all reachable affected paths and shared causes, failure/recovery behavior, downstream dependency risks and the evidence already obtained. Stay bounded by the current outcome and consequential later work, not an unrelated repository rewrite.
- Update the existing design/task with one coherent solution, implementation scope, necessary verification, remaining external dependencies and rollback. Reuse working components; fix the underlying design where needed. Ordinary local design/implementation remains authorized; only a materially different commitment needs a user decision.
- The **existing Codex main task directly implements, integrates and verifies** the solution. Do not hand the same failed outcome back to Pi, start another Codex model, weaken acceptance, reset spend, or call a checkpoint complete. Project acceptance and final reporting remain authoritative; the takeover event is resolved as a handoff receipt, never used to claim that new Codex code passed old Pi checks.

For an already running frozen helper, enforce this user rule in the main task now and adopt updated helpers only at a verified terminal boundary. Do not hot-edit task snapshots or managed plugin caches.

Every real main turn still carries its normal context. Savings come from eliminating empty checks and narrowing evidence reads; report measured savings only for comparable completed work. Update canonical plugin sources, never managed caches or hook trust. Running helpers stay frozen; adopt a verified runtime only after the task reaches a safe terminal boundary. A live desktop conversation may still have a hook command pinned to its installed plugin cache path: installing a new cachebuster can remove that path even when the hook source is unchanged. Defer reinstall until those conversations can reload their hooks; a frozen Pi helper alone is not a safe installation boundary. If a pinned path has already vanished, restore its exact installed version through plugin management and verify the hook executable, without editing managed caches.
````

## Runtime commands (`references/runtime.md`)

````markdown
# Local runtime commands

Python scripts call the existing Pi CLI. Pi is pinned to `deepseek/deepseek-flash`, thinking `max`; any other model is rejected. The optional Codex `queue` transport delivers to the existing desktop task without invoking a second model. Authentication stays in the user's existing local configuration. No extra MCP, heartbeat or service is installed.

Use an accepted plugin runtime for new operations. Replace the example paths and exact owner UUID before executing; do not interpolate user text into shell commands.

```sh
python3 /absolute/plugin/runtime/pi_task.py project --repo /absolute/repo
python3 /absolute/plugin/runtime/pi_task.py start --repo /absolute/repo --task TASK-1 --worktree /absolute/worktree --prompt-file /absolute/brief.md
python3 /absolute/plugin/runtime/pi_board.py register --repo /absolute/repo --task TASK-1 --thread OWNER_UUID --transport cli-queue --title 'Task title' --goal 'Reviewable result'
```

Start and immediately register; registration also catches a task that already finished. Registration defaults to `offline` unless `cli-queue` is explicit. The transport requires a local Codex CLI supporting `queue`, the desktop application, and its exact existing task UUID. `--codex-bin /absolute/codex` chooses a specific executable. Never guess an owner, invoke `exec/resume/fork` or launch an app-server to deliver a message. A task started by old helpers must adopt the accepted runtime at a terminal boundary before its supervisor can provide new notifications.

Project configuration lives in `.agents/codex-pi.json`: schemaVersion 1, pinned model/thinking, existing constraint paths, acceptance command guidance, maxWorkers and timeoutSeconds. It is not another PLAN or proof of acceptance. Use an isolated worktree; a task reserves its worktree for its lifetime. `start --read-only` limits Pi tools. `PI_BIN` may select the installed Pi executable.

## One read when needed

```sh
python3 /absolute/plugin/runtime/pi_board.py show --repo /absolute/repo --task TASK-1
python3 /absolute/plugin/runtime/pi_task.py status --repo /absolute/repo --task TASK-1 --round 1
python3 /absolute/plugin/runtime/pi_task.py result --repo /absolute/repo --task TASK-1 --round 1
python3 /absolute/plugin/runtime/pi_task.py continue --repo /absolute/repo --task TASK-1 --prompt-file /absolute/repair.md
```

Normal supervision stays inside the detached Pi supervisor. Do independent work, then end the main turn when blocked on Pi. Do not build main-model polling loops around `wait`; the low-level bounded wait command is only a diagnostic. Answer a user's progress question from one compact snapshot. Observe actual command/deadline/receipt evidence, not PID or output growth alone.

`result` is collected once per terminal round. It retains raw pointers and bounded summaries, all attempts, model usage and native Pi session evidence below the Git common directory `codex-pi/tasks/`. Open only the relevant receipt/log/diff. Continue the exact saved task for repairs; do not replace a live or unknown worker with a new identity. A reused session does not keep idle Pi processes alive after the round ends.

## Phase contracts (0.5: accepted O1, verified O2)

Prepare the design and prompt with [the task packet standard] [archived link: task-packet.md]; use the existing contract schema below, not a second task format.

An authorized complete phase may be dispatched with a frozen contract: give `start`/`continue` a
`--contract-file` JSON (schema in `docs/validation/phase-autonomy-o1-20260926.md`). The contract
freezes goal, complete result, baseline, scope, design ref/hash, acceptance item IDs with real check
commands, the phase budget and the autonomous-repair/escalation boundary. The brief references the
contract; the project PLAN/design remains authoritative and no parallel plan is created.

```sh
python3 /absolute/plugin/runtime/pi_task.py progress --repo WT --task TASK --round N \
    --activity implementing --step '...' --completed-criteria ITEM --next '...' --evidence-ref PATH
python3 /absolute/plugin/runtime/pi_task.py readiness --repo REPO --task TASK --round N
python3 /absolute/plugin/runtime/pi_task.py phase-status --repo REPO --task TASK
python3 /absolute/plugin/runtime/pi_board.py decide --repo REPO --task TASK --event-id EVENT \
    --decision accept --reviewed-head FULL_SHA --phase PHASE --contract-hash HASH
```

Ordinary progress is self-report only, never a check receipt and never a queue message. Readiness
compares the exact contract with real `pi_check` receipts bound to the candidate; missing, failed,
skipped or unknown evidence is never ready (scope coverage is complete with a bounded cap, and
`forbidSkip`/`minRun` need parseable counts). Running self-repairable check failures/timeouts stay
local. A normally completed round with only mechanically missing evidence may be continued once in
the same session/worktree/budget (quota persisted per phase); the second shortfall, a real design
question or an unknown start escalates. A phase also emits at most two bounded non-blocking
progress echoes (default 10 minutes apart, persisted per `phaseId`) for verified receipts or
evidence-bearing check/repair milestones; ordinary progress, repeated writes and "still alive"
never queue. A recorded user pause blocks progress echoes, auto-continuation and explicit
`continue` until an explicit resume. `accept` binds phase, contract and candidate; a different
phase requires the previous one accepted on the board and no conflicting worker. One normalized
phase evidence snapshot (candidate known/unknown, per-item grades, scope, execution, readiness) is
built by `pi_task` and consumed by the board, progress events, `readiness` and the accept gate;
there is no second candidate or receipt-validity inference. A phase acceptance receipt must present
the item's declared command and a wrapper deadline within the contract's `commandTimeoutSeconds`;
a known command or timeout mismatch can never cover the item, missing or contradictory
identity/timing stays unknown, and every consumer uses that one verdict. The generated phase brief
uses that per-command cap while a legacy task keeps the project round-timeout example; the
whole-round supervisor timeout is a separate limit. A ready review event carries a durable episode
id: invalidating and recovering the same round/candidate publishes exactly one new review event,
unchanged refreshes stay idempotent, the old event stays superseded, and `accept` binds only the
new event. `pi_check` also reports run/pass/fail/skip for the unambiguous final `unittest` summary
(Go verbose counts unchanged); malformed or contradictory final summaries (a `FAILED` without a
positive failure count, `OK` with failures, duplicate fields, or a dangling final `Ran` line)
yield no counts, and `minRun`/`forbidSkip` stay unknown/skipped/failed when that summary is missing,
zero, skipped or failing. `accept` re-reads the live status and
refuses when the round, contract revision, candidate, readiness, worktree HEAD or writer-free state
no longer match the stored event. Tasks without a
contract keep the legacy review path. The persisted frozen contract must hash to its stored digest
and agree with the phase-state anchor; on a mismatch the board keeps the phase binding with unknown
readiness so old phase events cannot fall back to the legacy accept path. O1 is accepted at candidate `7331da9`; O2 ran a real two-round
fixture (R1 `changes_requested`, same-session R2 accepted) documented in
[o2-eventfold-20260926.md] [archived link: ../../../docs/validation/o2-eventfold-20260926.md]; O3 is accepted at
`e65ca30`; O4 hardening is documented in
[o4-release-hardening-20260926.md] [archived link: ../../../docs/validation/o4-release-hardening-20260926.md] and
awaits review; formal installation and business-task migration are still pending. See
[docs/validation/phase-autonomy-o1-20260926.md] [archived link: ../../../docs/validation/phase-autonomy-o1-20260926.md]
for operations and the current verification boundary.

## Checks and resource protection

The generated brief contains task-specific frozen `toolsDir` and round-specific `checksDir`. Run from the Pi worktree:

```sh
python3 /absolute/task/tools/pi_check.py --output-dir /absolute/round/round.checks --id affected-tests --timeout-seconds 600 -- make test
python3 /absolute/task/tools/pi_check.py --output-dir /absolute/round/round.checks --id evidence-test --timeout-seconds 180 --watch-path /absolute/evidence --max-bytes 104857600 --health-interval-seconds 15 -- python3 real_check.py
python3 /absolute/task/tools/pi_copy.py /absolute/source /absolute/new-destination --max-bytes 104857600
```

Replace example commands and limits with meaningful project budgets. A running marker reports wrapper start, actual deadline, child identity and optional directory guard. Final immutable receipts bind command, revision, exit and log hash. Failed, skipped, interrupted, unknown or zero-test attempts never become a pass. The command timeout and whole Pi round timeout are separate. For a phase task the per-command `--timeout-seconds` must not exceed the contract's `commandTimeoutSeconds`; a receipt outside that bound or with a different command can never cover its acceptance item.

For a phase task with `resourceLimits`, the supervisor additionally measures every declared in-worktree path at most every 60 seconds while Pi runs and once at termination under a bounded aggregate scan budget, using the no-follow high-water state under the round directory; it validates the path cannot escape the worktree through `..` or symlink components. A known overage (even a partial lower bound already over the cap) stops only the owned Pi process group and makes the phase not ready; a measurement unknown for two minutes escalates the same way. Missing, corrupt, stale, contradictory or final-incomplete resource evidence is unknown when limits are declared, never ready; a complete limit needs a valid nonnegative measured byte count and scan evidence, a missing declared path is known zero, and `resourceLimits=[]` declares no limit. This covers declared paths and persistently observable writes, not arbitrary external writes or a dead supervisor.

Directory guards measure declared regular-file bytes without following symlinks. An observed known breach stops only the owned command group and records the failure; an incomplete measurement stays unknown. The copy helper preserves literal symlinks and refuses existing destinations, recursion, known overages or unknown verification. These rules prevent the 14 MB → 6.5 GB expansion incident without asking the main model to poll.

See [handoff and recovery] [archived link: handoff.md] for event decisions, uncertain delivery, interruption and safe migration.

## Review failure and direct Codex implementation

Dispatch a new task with the fixed two-delivery pin:

```sh
python3 runtime/pi_task.py start --repo REPO --task TASK --worktree WT --prompt-file BRIEF
```

A new task pins two complete Pi deliveries; `start` accepts only `--review-limit 2` (the default), and historical pins are not migrated. The pin is frozen in `task.json` and copied to the board at registration; contract revisions, phase renames, pause/resume, retries and later config edits never raise it or erase counted failures.

Record negative delivery decisions as quality failures. For example:

```sh
python3 runtime/pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision changes_requested --failure-kind quality --note "Actual outcome failed the bound acceptance check"
```

Only genuine missing external evidence/authority uses `--failure-kind external` with a nonempty `--note` naming the unlock condition. `reviewPolicy` in `decide`/`show` exposes the pinned `limit`/`limitSource`, distinct failed rounds, `implementationOwner`, `reason` and `instruction`. Counting is task-scoped since the last accepted outcome: one main decision per distinct round; exact replays, duplicate events for one round, `--failure-kind external`, checks, progress and the single missing-receipt auto-continuation do not increment. A contract edit or phase rename cannot reset the count. Old phase-bound negative decisions count unless explicitly classified otherwise; inspect their actual reports before drawing a model-capability conclusion. No PID/log activity, raw test failure or queue delivery increments the counter by itself.

The whole-task analysis and takeover responsibility are defined once in [the collaborate Skill] [archived link: ../SKILL.md]; follow it before continuing or taking over. The runtime deliberately has no plan file, heading parser or plan hash gate: scripts count exact decisions and control ownership, they cannot prove reasoning quality. At the pinned limit `codex_takeover_required` is emitted and Pi continuation is refused, including after board resume. Acceptance before the limit resets the count for an authorized next phase; a reached takeover remains latched for the same task, and a future independently authorized outcome is a separate dispatch. Takeover transfers the remaining outcome to the existing main task; it does not launch a model or grant write ownership.

## Owned command protection (2026-10-01)

Validated canonical source includes `pi_command_guard.py` in each new immutable helper snapshot. Temporary bash probes have a default600-second execution deadline plus10-second cleanup grace, anchored to the OS process birth; explicit tool and actually owned pi_check deadlines preserve legitimate longer checks within the phase command ceiling. Exact native tool completion wins. Only the uniquely owned new child process group can be terminated; ambiguous ownership, detached descendants, PID reuse and observation failures stay unknown. This returns a failed command to the same Pi worker; it never authorizes automatic worker cancellation or code acceptance. Signal-attempt evidence is append-only and its effect initially unconfirmed.

Active old snapshots are never hot-edited. A user-authorized pinned separate command observer may protect that exact old round without changing its helper, contract, session, model or budget. It holds a lease, respects board pause, verifies original Pi birth identity and exits at terminal/worker release. Future tasks should start through the validated canonical source until cache/hook installation has a safe boundary. See `docs/validation/command-protection-20261001.md` and its exact tests/hashes. A user-requested30-minute main-thread heartbeat is additional diagnosis and authorized successor work; it does not replace the deterministic deadlines or independent acceptance.
````

## Handoff and recovery (`references/handoff.md`)

````markdown
# Event handoff and recovery

The existing Pi supervisor refreshes the shared board about every 15 seconds and dispatches only actionable events. The board describes the goal, phase, round, candidate commit, command/check evidence and pending question. Queue delivery, event handling and code acceptance are separate facts. Ordinary progress does not wake the main model. An idle desktop task resumes automatically; a busy task handles the queued message after its current turn. Already queued messages cannot be treated as permission to override a later pause.

## Handle one event

Read its exact task, round, event ID and question. Reuse a result already collected for that round. Verify the relevant diff, exact candidate and original check receipts, then decide:

```sh
python3 /absolute/plugin/runtime/pi_board.py decide --repo /absolute/repo --task TASK-1 --event-id EVENT_ID --decision accept --reviewed-head FULL_COMMIT_SHA --note 'Verified checks and review evidence'
```

Use `changes_requested` with concrete findings when repairs are needed; `reject` rejects the candidate and `resolve` handles a non-review incident. Acceptance requires the full real immutable candidate commit and project evidence. Decisions on old rounds never accept the current round. If a packet reports overflow, read remaining pending board events and handle them in this same main turn; a terminal supervisor will not supply another periodic tick. A repeated delivered event is a receipt to deduplicate, not a reason to rerun Pi or checks.

Before continuing or taking over, consult [the collaborate Skill] [archived link: ../SKILL.md] and apply its whole-task analysis: review the exact candidate and evidence, identify the current symptom and shared cause, and assess the effect on the remaining authorized plan and dependencies. Only then continue the same Pi session with one consolidated brief or, at the pinned limit, verify writer release and implement directly. Productive red tests and routine repairs remain with Pi. Investigate the first relevant error before repeating an unchanged failure. A resource breach or deadline should produce a bounded question and evidence, not an infinite retry or automatic acceptance.

## Pause and uncertain delivery

```sh
python3 /absolute/plugin/runtime/pi_board.py pause --repo /absolute/repo --task TASK-1 --note 'User paused handoff'
python3 /absolute/plugin/runtime/pi_board.py resume --repo /absolute/repo --task TASK-1 --thread OWNER_UUID
python3 /absolute/plugin/runtime/pi_board.py recover --thread OWNER_UUID
python3 /absolute/plugin/runtime/pi_board.py rearm --repo /absolute/repo --task TASK-1 --event-id EVENT_ID
```

User Interrupt pauses the owner's handoff route; it does not cancel Pi. A progress question does not resume a pause. Resume only when the user authorizes continuation. `pi_task.py cancel` separately cancels the owned worker; verify it has ended before starting repair.

Confirmed queue delivery is not retried merely because review is unfinished. A timeout or ambiguous crash may have delivered already: retain the uncertain claim, inspect evidence and use explicit recovery only when needed. `rearm` may duplicate a previously uncertain delivery because CLI queue has no caller idempotency key. Never rearm a live inflight send, steal locks, clear decisions or claim exactly-once delivery. Failures are visible through board/recovery evidence; no second model repairs transport.

Hooks stay short and serve pause/recovery. CLI queue tasks suppress overlapping legacy Stop delivery. A supervisor cannot report its own death without another observer: after a process/host failure, recovery occurs on the next normal interaction. There is no universal two-minute discovery guarantee. Declared command deadlines and resource budgets provide bounded detection while their supervisor is alive; desktop response can additionally wait for its active turn to finish.

## Adopt an update safely

Update the canonical plugin and install through the formal plugin mechanism. Never modify managed caches, hook trust or private app IPC/queue databases. If the app requests Hook review/trust, the user does that in the app. A plugin update does not rewrite a running worker's helpers.

At a verified terminal boundary, with no worker/supervisor ownership, run the accepted runtime:

```sh
python3 /absolute/new-plugin/runtime/pi_task.py upgrade --repo /absolute/repo --task TASK-1
python3 /absolute/new-plugin/runtime/pi_board.py register --repo /absolute/repo --task TASK-1 --thread OWNER_UUID --transport cli-queue --title 'Task title' --goal 'Authorized result'
```

Verify frozen helper hashes/version and unchanged Pi session/worktree before `continue`. Registering a terminal task can enqueue a review event immediately; respect already-completed review evidence rather than duplicating work. Keep legacy business workers intact until their own safe boundary. For an old 0.2 long Stop hook, the explicit `pi_handoff.py release --event-key EVENT_KEY --session-id OWNER_UUID` invalidates that exact binding without cancelling Pi; verify the old hook exited.

## Fast acceptance

Use one real tiny Pi task: write fixed bytes to one file, run a deterministic check with a ten-second timeout, commit only that file, and exit. Bound the whole round to three minutes. Test completion → own supervisor → same desktop task → visible main reply. A 15-second fixture passed this path on 2026-09-26. Use isolated short child processes for failure, cancellation, timeout and duplicate/race tests; they need no additional model calls. Transport tests and green unit tests do not waive independent code review.
````

## Task packet standard (`references/task-packet.md`)

````markdown
# Pi 任务包标准

供 **Codex 主会话**在准备派工或整块返修时使用。Skill 管决策，任务说明管交付，公共 runner 管执行、证据与通知。标准化信息，不替模型规定每一步。主会话的可复用整任务分析与两次交付责任边界以 [Skill] [archived link: ../SKILL.md] 为唯一权威；本文只定义任务包产物、模板与交付格式。

## 最小产物与唯一权威

| 产物 | 内容与归属 |
| --- | --- |
| 任务规格 | 目标、已定设计、关键事实、范围与验收。优先引用已有项目设计/阶段说明；缺失才写 `task.md`。 |
| 派工提示 | 用下面的短模板生成 `brief.md`，引用准确规格与验收项，不全文复制设计。小任务可直接以任务规格作为 brief，不强制两份 Markdown。 |
| 阶段合同 | 完整阶段使用现有 `phase-contract.json` / `--contract-file`，是 runner 对本次设计的冻结快照，不成为第二份 PLAN。旧活跃任务不为套模板重启或热改合同。 |
| 交付报告 | Pi 完成后按下方格式给出。复用已有报告文件或 runner 最终消息；不强制再建 `delivery.md`。 |

优先使用仓库现有任务目录。路径、阶段数和文件名是建议，不是门禁。持久设计可按仓库规则入库；私有数据、运行日志、凭据和任务本地指针留在获准证据位置。runner 已保存不可变 brief/round，无需把所有提示和日志再次提交。设计冻结后返修写新 brief，保留原版，不覆盖旧日志或追加改写已派发的规格。

## 任务规格模板

保持下列信息顺序，可合并短项。已有权威文件承载的内容给精确引用即可。条件性内容不适用时省略，不填机械占位表。

```markdown
# <阶段/任务> · <可审阅成果>

## 目标、当前阶段与后续计划
整体目标 → 本阶段完整结果 → 后续已授权计划与真实依赖；本块到哪里结束，哪些后继不属于本次。
现有计划/设计/阶段合同的唯一入口。复杂结果写清成果导向的任务、依赖/顺序、责任边界与完成质量，不写编码步骤或固定函数。
明确本次需要证明的闭合结果，以及它涉及的实际入口、数据形态与重要状态序列；过大的结果细化为真实冻结合同，保留总体要求、失败历史、暂停和预算。

## 基线、范围与关键事实
- 仓库、准确基线、隔离工作树；允许修改和必须保留的范围。
- 派工和交付以实际 Git 差异核对范围，包含 PLAN/设计文件及 dirty/untracked 工作；不能只依赖文字声明。
- 影响设计的已核实接口/字段/数据语义：事实 → 源码或原证位置/版本。
- 未确定项及如何验证；只有真正改变权限、设计或验收的未知才回主会话裁决。
- 输入与证据供给：已允许且可读的具体来源，缺失/禁止来源，以及可独立推进的部分。

## 设计决定、难点与验证
- 谁持有事实、谁可写；经过哪些现有公开接口；必须满足的外部合同。
- 关键状态转移、幂等/并发/恢复、错误与 unknown 的语义（按任务需要）。
- 对可能影响结果的难点：难点 → 已选方案/必要决定 → 失败或解锁路径 → 验证方式；关键设计不留给 Pi 猜。
- 共享机制的 owner、唯一权威接口、实际行为证明及依赖它的后继；相关异步成功、错误与异常返回如何遵守同一规则。
- 相关时写清空值、时区、单位、分页/去重或序列化口径，给一个决定性的边界算例。
其余实现由 Pi 自行决定。

## 并行协作（适用时）
引用现有设计中的两路完整结果与共享契约；写清本路任务/会话/工作树、同伴任务与准确输入提交、可独立推进和必须等待的部分，以及共享修改与测试资源归属。
指定一个 Pi 任务负责授权范围内的整合、修复和最终组合检查；本路交付边界与总体阶段完成边界分别明确。拆分和整合不重置已有失败次数或预算。

## 交付与验收
| ID | 可观察通过条件 | 实际检查命令/原证 | 必须拒绝的反例 |
| --- | --- | --- | --- |
| <稳定ID> | <行为及结果，不能只有exit0> | <确切命令、证据类型与位置> | <该边界的失败情形> |
补充整合/提交/文档范围；人工或外部证明单独标明，模拟不能替代。
检查、原始证据、源码与依赖版本必须对得上；缺失、跳过、未知都不是通过。
评价决定验收时，引用基于真实公共接口记录和独立预期事实的有效/无效校准原证，写清准备位置及依赖；旧记录不冒充当前候选或未见题。区分当前承诺的阻断缺陷与后续增强，设计通过不等于实现通过。

## 自主修复、预算与升级条件
Pi 可自行完成的实现、测试和普通返修；只在共同根因需要改变设计/授权时回报。
命令与阶段预算、资源范围；缺失必要输入时保留成果并指出解锁条件。
若本块有真实依赖，列少量“成果 → 前置 → 验证”的里程碑；不要按读代码/编码/测试拆阶段。
```

Pi 每次完成一次完整交付后，主会话复用同一次（可更新的）整任务分析核对准确候选与原证，定位当前症状与共同根因，再判断同一根因或设计变化对后续任务与真实依赖的影响，把结论收敛进现有设计与下一份 brief。交付通过可以只是确认既有方案仍有效，不必重写或重复已有效的调查；只有影响设计的难点才需要新的方案与验证。

事实核实深度服从风险。公开API变更应核对安装版本的公开合同；数据任务应核对真实字段与含义。没有必要时不调用真实网络/付费模型，不因为模板提到样本就读取受禁目录。模型能力和规格质量都会影响结果，详细规格不能替代验证。

代码演进任务必须明确版本策略：新项目直接替换时只支持目标版本，不默认增加旧版 fallback；保留历史原证不等于运行时兼容旧格式。已有用户的暂停、预算和“终态后停止”条件优先，不能因 runner 支持自动补齐而解除。

## 派工短提示模板

```text
完成 <任务/阶段的整块成果>。基线 <准确commit>；仅在 <工作树> 实施。
先读 <现有约束入口> 和 <规格路径/必要章节>；阶段验收以 <合同路径及验收ID> 为准。
已定：<本阶段任务/依赖与本次最重要的设计决定；难点→已选方案→验证见规格>。范围：<允许变更及保护项>。
并行时：<本路完整结果、同伴输入/等待条件、共享修改与资源归属、Pi集成负责人及组合验收边界>。
自行选择实现并完成针对性验证、普通修复和授权内提交；不要停在代码写完或局部PASS。
正式检查使用本任务冻结 pi_check 和本轮 checksDir，按实际命令保留退出码、日志和失败。
关键输入：<允许来源、明确缺口>；不得从 <受禁来源> 绕过限制获取。
发生 <设计矛盾/权限改变/重复无效修复等具体条件> 时，交付原证和最小决策问题。
按任务包交付格式返回准确候选、验收映射、未完成项；到 <本次完成边界> 停止。
```

删除不适用的行。约束只写本次有意义的差异，不把全部仓库规则和 Skill 复制进提示。语言随用户，API/路径/字段/命令原样保持。执行统一用 `--prompt-file` 与公共插件，不从参考材料复制 `pi --no-session`、长 shell 字符串或 `| tail` 验收命令；不创建无记录的另一个 Pi 会话。

## 对接现有机器合同

使用 [runtime 指南] [archived link: runtime.md] 和现有 `runtime/pi_phase.py` 校验器，不新造 schema/调度脚本。将规格映射到：

| 规格内容 | 现有字段 |
| --- | --- |
| 阶段身份、目标、完整结果 | `schemaVersion: 1`、`phaseId`、`goal`、`result` |
| 代码与设计冻结 | `baseline`、`scope`、`designRef`、`designSha256` |
| 验收映射 | `acceptanceItems[]`: `id`、`description`、`checkId`、`command`、`passCondition`、`evidence`；计数有意义且可解析时加 `minRun`、`forbidSkip` |
| 时间和资源预算 | `budgetSeconds`、`commandTimeoutSeconds`、`resourceLimits` |
| 自修、回到主会话的条件 | `autonomousRepair`、`escalateWhen`；必要时 `preconditions`、`nextPhaseRef` |

`designRef`必须指向工作树内真实文件，hash由文件内容计算，基线必须可解析。规格可以直接作为设计文件；复杂设计另有 authority 时引用它，不复制一份。短 brief 不宣称是机器合同。命令必须实际对应项目能力，不能为填字段发明；尚待实现的 gate 要明确归本块交付，不能占位 PASS。预算沿既有授权，返修不重置。

准备完检查：**规格与合同是否同一目标/边界？输入是否实际可供给？通过条件能否被原证证伪？** 必需历史回执缺失时，不伪造空文件，也不把“缺失故障相同”写成“没有回归”。受禁数据先由获授权路径提供准确白名单和冻结包，不能自行跨越边界。可先完成独立代码与局部验证，完整验收仍保持未通过。

## Pi 交付格式

```markdown
结果：<完成待审 / 部分完成 / 阻塞>，不能自行写“主会话已验收”。
候选：<完整commit、worktree、dirty及其归属>；任务/阶段/round：<准确身份>。
变更：<对用户行为和事实owner的影响；必要设计取舍>。
整合（适用时）：<准确同伴提交、组合候选、本路与组合检查范围、尚未完成的依赖>。
验收：<验收ID → 实际命令/exit/计数 → 本轮receipt与原证 → 对应候选>。
未完成：<缺失/失败/跳过/未知、影响、尝试过的修复及下一步所需事实>。
证据限制：<被覆盖/哈希不符/历史版本不适用；分别保留，不冒用为当前通过>。
```

日志和运行回执只链接不粘贴。命令退出0、Pi总结、局部PASS、相同失败名称都不能替代阶段验收。Pi报告是索引，Codex审查确切候选和原证；不每轮重读全仓、不重复已有效检查。材料有秘密时只引用获准位置，禁止打印 token、cookie 或连接串。不要默认删除临时诊断/失败证据、推送、发布或执行外部动作。

## 同根因返修补充

沿用上面格式，但只补本轮差异：被拒候选与验收项、共同根因、准确复现/反例、相关入口（已复现/可达未验证/排除）、修复决定、允许范围与受影响检查、仍有效的证据，以及该根因或设计变化对后续任务与依赖的影响。整任务分析、两次交付与接手责任以 [Skill] [archived link: ../SKILL.md] 为准，本文不重复 pin/上限或计数规则。未经新授权，不启动后继阶段。不能因主会话发现一个问题就回到逐函数指挥，也不能把未做过的检查说成已验证。
````
