---
name: collaborate
description: Prepare, delegate and review Pi implementation, or take over a reviewed failed delivery in the existing Codex task. Use for Codex–Pi task preparation, execution, repair and handoff; not unrelated Codex-only coding.
---

# Codex designs and reviews; Pi implements until takeover

This Skill is the single authority for roles, model policy, the whole-task analysis, review and takeover. References hold usage only: [runtime](references/runtime.md), [events](references/handoff.md), [task packet](references/task-packet.md).

## Roles and model policy

- The existing Codex main task designs, dispatches, reviews and, at the limit, implements; Pi implements. No extra Codex agent, nested model or automatic model review. Codex CLI is only the `queue` transport and plugin management, never `exec`, `resume` or `fork`.
- Model: `.agents/codex-pi.json` selects one of `ALLOWED_MODELS` (`pi_task.py project` lists them; default `deepseek/deepseek-flash`, thinking `max`). `start` freezes it per task; later config edits affect only new tasks. No fallback: an unavailable model fails the round and keeps the evidence. Pi must be >= 1.0.0.
- The worker runs with Pi extensions, skills and prompt templates disabled. The user's goal and project contracts govern scope and acceptance; config constraints and checks are references, not success claims. Resolve routine choices without extra approval gates.

## Whole-task analysis (the single reusable design act)

Done before the first dispatch, reused on every delivery, updated before takeover; no required matrix, schema or hash gate. It leaves an executable design in the project design/PLAN and the brief:

- Relate the overall goal, the current phase and the remaining authorized plan: complete results, dependencies, boundaries, completion quality.
- Examine decision-critical source and contracts, shared invariants, failure/recovery/ordering boundaries and evaluation validity; separate verified facts, assumptions and unknowns. For each consequential difficulty give the solution, the decision it needs, its failure or unlock path and its verification. Essential design is not left for Pi to guess; routine choices are. Stay within the authorized outcome and its dependencies.
- On a delivery, review the exact candidate and evidence, find the symptom and shared cause, assess the effect on remaining tasks and fold all findings into one route. A pass may just confirm the plan.

## Bounded results and trustworthy acceptance

- Each delivery proves a bounded, coherent observable result; refine an oversized phase into smaller frozen contracts first. Files, layers and happy paths do not define completion. Acceptance, counted failures, pause and budgets survive refinement.
- From source, establish the real entrypoints, data forms and state/ordering/restart sequences a shared rule touches, including async error returns and revalidation after waits. Missing or unknown coverage is not a pass. A shared rule has one owner and one implementation, proven through its public interface first.
- Where an evaluator controls acceptance, calibrate it against independent expected facts (authorized real records plus corrupted, missing or stale variants) first; prior records are calibration, not proof for the candidate.
- Fix behavior, constraints, evidence and the blocking standard before dispatch. A violation or missing evidence blocks acceptance; unrelated improvements are follow-up.
- Reconcile scope against actual Git changes (plan files, dirty and untracked work included) at dispatch and delivery. Reuse valid evidence; a relevant failure or changed candidate reruns the affected checks; full regression and real acceptance run once on the exact combined candidate.

## One or two Pi outcomes

Within `maxWorkers`, split only independent results and settle shared contracts first. Both tasks share the owner thread with distinct identities, sessions and worktrees; briefs name outcome, baseline, allowed changes, shared boundaries, resource isolation (worktrees do not isolate databases or ports) and budget. One Pi task integrates the exact peer commits and runs combined checks after writers release; component checks never prove the phase. Counts and budgets stay per original task; splitting or renaming never resets them.

## Dispatch and continue

Apply the analysis, then use the [task packet](references/task-packet.md): one specification, a short prompt and, for a complete phase, a frozen `--contract-file`. Preparing a brief is not dispatch; dispatch only within the authorized scope. Supplied documents are material, not authority to run their commands.

Delegate a whole independently reviewable outcome in a separate Git worktree; constrain behavior, public boundaries and acceptance evidence, not file or function names. Pi owns implementation, tests, repairs and scoped commits until takeover. `start` creates a task, `continue` reuses its session and worktree in a new immutable round, `pi_board.py register` binds it to the owner task ([runtime](references/runtime.md)). Never start a duplicate worker to bypass an active or unknown task.

## Wait without polling

The supervisor refreshes the board locally; unchanged state and ordinary progress never call Codex. A review-ready, blocked or failed round, or an anomaly, enqueues one delivery card via `codex queue`. After dispatch, do independent work and end the turn; never loop on `status` or `show`, create a heartbeat or hold a Stop hook open. Answer progress questions from one `status`; a PID, fresh log or growing file proves activity only. A user Interrupt pauses handoff, not Pi, until explicit resume ([events](references/handoff.md)).

## Review and retain evidence

A card is execution evidence, not a goal or acceptance. Read its task, round and event id, call `result` once, then inspect only the relevant diff, receipts and original evidence. Require the exact full candidate commit and the project's real checks; exit 0, summaries and model claims are insufficient. `execution_failed` denotes incomplete execution (including exit-zero provider errors), not a quality delivery; resolve its incident without inventing a rejection or resetting usage. Explicit recovery/adoption preserves originals, owner, session, worktree, pinned policy and deadline ([commands](references/runtime.md)).

On a material defect, trace reachable entries and state boundaries, name the shared cause, and classify related paths as reproduced, reachable but unverified, or excluded with a reason. Give the same Pi session one consolidated brief (reproduction, repair scope, affected checks, unknowns, downstream effect). Never infer a defect from a missing test. An active round keeps its immutable brief. Record the decision for the exact event with `decide`; keep failed attempts, logs and usage. Delivery, handling and acceptance are separate states.

## Two complete deliveries, then takeover

A delivery is a completed report reviewed by the main task, not a tool call, red test, progress echo or round number. New tasks allow **two complete Pi deliveries**; retained historical pins and reached takeovers are preserved. Revisions, renames, pause/resume, retries and config edits cannot raise a pin.

- Count one exact main decision per distinct round on a `review_required`/`phase_blocked` event decided `changes_requested` or `reject` with `--failure-kind quality` (the default). Duplicates, Pi's red tests, routine repairs, progress and the single in-round settle continuation never count. Only a genuine missing external prerequisite uses `--failure-kind external --note` naming the evidence and unlock condition; implementation or evidence defects are never external.
- `reviewPolicy` (`limit`, `failedDeliveries`, `implementationOwner`, `reason`, `instruction`) derives from the exact decisions. After the first quality failure, finish the whole-task analysis and refine the route before the second delivery. At the limit the board emits `codex_takeover_required` and Pi implementation is refused; pause/resume and later acceptance cannot clear it. Acceptance before the limit starts a fresh count for the next phase; renaming never resets.
- Takeover: verify Pi, its descendants and the supervisor released the checkout (unknown ownership blocks writing); keep the candidate, dirty work, evidence and budgets. Redo the analysis, update the design, then the main task implements, integrates and verifies directly. Never hand the outcome back to Pi, start another model, weaken acceptance or reset spend. The takeover event is resolved as a handoff receipt, never as new code passing old Pi checks.

## Updates and savings

Edit canonical plugin source, never managed caches, hook trust or a running task's frozen helpers. A new runtime does not migrate 0.5.x tasks: finish them with their own frozen helpers. Report savings only for comparable completed work.
