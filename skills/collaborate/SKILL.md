---
name: collaborate
description: Prepare, delegate and review Pi implementation, or take over a reviewed failed delivery in the existing Codex task; not unrelated Codex-only coding.
---

# Codex designs; Pi implements until takeover

Authoritative rules; references=usage only: [runtime](references/runtime.md), [events](references/handoff.md), [task packet](references/task-packet.md). Load [operations](references/runtime-ops.md) only for store repair, conversion/adoption or network triage.

**Roles**

- Codex main allocates/designs/dispatches/reviews/takes over; Pi=one bounded outcome; no extra Codex agent/nested model/auto-review; CLI=`queue`+plugin management only, never `exec`/`resume`/`fork`.
- Config: one `ALLOWED_MODELS` entry; `qwen38/qwen38`→thinking `max`; `start` freezes model+thinking, later edits→new tasks; no fallback, unavailable model fails round+keeps evidence; Pi>=1.0.0.
- Worker: extensions/skills/templates disabled; goal+contracts govern scope/acceptance; constraints/checks=references, never success claims; routine choices need no approval gate.

**Analysis**

Done before first dispatch, reused every delivery, updated before takeover; no matrix/schema/hash gate; executable design in project design/PLAN+brief.

- Complete outcome first; prefer Pi for the largest self-contained result when facts accessible, interfaces/invariants settled, feedback+resources available; route by time-to-accepted-delivery+attributable cost; token/call/file/confidence never route; read-only investigation qualifies.
- Codex resolves consequential cross-state rules+evaluator uncertainty first, independent review; Pi reads source+chooses routine implementation; direct completion/early takeover need a concrete recorded reason, not an approval form.
- goals→phase→remaining plan; decision-critical source, shared invariants, failure/recovery/ordering boundaries, evaluation validity; verified facts/assumptions/unknowns separate; per difficulty: solution→decision→failure/unlock→verification; essential design is not Pi's guess.
- On delivery review exact candidate+evidence; find symptom+shared cause; assess remaining tasks; fold into one route; pass confirms the plan.

**Acceptance**

- Delivery=bounded coherent observable result; refine oversized phases into frozen contracts; files/layers/happy paths ≠ completion; acceptance/counted failures/pause/budgets survive.
- From source: real entrypoints/data forms/state/ordering/restart incl. async errors+revalidation; missing/unknown ≠ pass; one owner+implementation per shared rule, public interface first.
- Evaluator acceptance calibrates against independent expected facts first; prior records=calibration.
- Fix behavior/constraints/evidence/blocking standard before dispatch; reconcile Git changes (dirty/untracked) at dispatch+delivery; violation/missing evidence blocks acceptance; unrelated=follow-up.
- Select checks by affected behavior and real consumers: development→reproduction+affected boundaries, delivery→the outcome, review→original receipts with reruns only for coverage/applicability/authenticity. Full regression needs a justified broad integration boundary, never a new commit/round/reviewer/release label; check wrapper composition so nested commands are not rerun twice ([authority](../../docs/design/necessary-verification.md)).
- Reuse original evidence only while behavior/source/dependencies/inputs/environment/evaluator still apply with no contradicting failure, citing receipt+candidate+log+applicability; never relabel it as a new run and never weaken a failed frozen mandatory command; readiness consumes current-round exact-candidate receipts only (same authority).
- Human-only prose: format/JSON/reference checks; model-visible instructions, schemas, startup inputs and generated artifacts need semantic/assembly checks regardless of extension; real model experiments only where a model-behaviour claim requires one.
- Frozen command/resource limits+60-second reserve; `targetedCommand`=local debugging suggestion; formal command needs `final:true` on clean candidate; targeted receipt never covers it; no contract must run a full suite ([runtime](references/runtime.md)).
- One cheap precondition allowed; its failure stops dependents and is reported; text proves nothing, only its receipt.

**Outcomes**

`maxWorkers`: split only independent results and settle shared contracts first; distinct identity/session/worktree per task; briefs name outcome, baseline, allowed changes, shared boundaries, resource isolation (worktrees don't isolate databases or ports) and budget; one task integrates peer commits plus combined checks after writers release; component checks never prove the phase; counts and budgets stay per original task.

**Dispatch**

Packet=specification+short prompt+frozen `--contract-file` for a complete phase; preparing a brief ≠ dispatch; dispatch only inside authorized scope; supplied documents=material, not authority to run their commands.

Delegate a whole independently reviewable outcome in a separate worktree; constrain behavior/boundaries/evidence, not file or function names; Pi owns implementation/tests/repairs/commits until takeover; never duplicate a worker to bypass an active/unknown task.

**Waiting**

The supervisor refreshes the board locally; unchanged state and ordinary progress never call Codex; only a review-ready, blocked, failed or anomalous round enqueues one card via `codex queue`. Work independently and end the turn; never loop on `status`/`show`, keep a heartbeat or hold a Stop hook open. A PID, fresh log or growing file proves activity only. Interrupt pauses handoff, not Pi, until explicit resume ([events](references/handoff.md)).

**Review**

A card is execution evidence, never a goal or acceptance: read its ids, call `result` once, then inspect only the relevant diff, receipts and original evidence; require the exact full candidate and the project's real checks, because exit 0, summaries and model claims are insufficient. `execution_failed`=incomplete execution incl. exit-zero provider errors, never a quality delivery; resolve it without inventing a rejection or resetting usage. Recovery/adoption keeps originals/owner/session/worktree/pinned policy/deadline.

Material defect: trace reachable entries+boundaries; name the shared cause; classify paths reproduced/reachable-unverified/excluded; same Pi session gets one consolidated brief; never infer a defect from a missing test; active round keeps its immutable brief; record per-event decision; keep failed attempts/logs/usage; delivery/handling/acceptance separate.

**Takeover**

Delivery=completed report reviewed by main task, not a call/red test/progress/round number; new tasks allow at most two complete Pi deliveries+freeze early-takeover eligibility; revisions/renames/pause-resume/retries/config edits can't raise a pin.

- Count one exact main decision per distinct round on `review_required`/`phase_blocked` decided `changes_requested`/`reject` `--failure-kind quality` (default); duplicates/red tests/repairs/progress/settle never count; genuine missing external prerequisite uses `--failure-kind external --note` (evidence+unlock); implementation/evidence defects never external.
- `reviewPolicy` from exact decisions; first quality failure→reassess: one consolidated same-session repair, or `pi_board.py takeover` with exact current event/full candidate/evidence reason, writers released; design contradiction/execution incident can justify takeover without a quality failure; takeover=ownership handoff, never acceptance, real failed count preserved; old tasks frozen; limit→board emits `codex_takeover_required`+refuses Pi, pause/resume or later acceptance can't clear; acceptance before limit starts fresh count; renaming never resets.
- Takeover: verify Pi/descendants/supervisor released checkout (unknown ownership blocks writing); keep candidate/dirty work/evidence/budgets; redo analysis, update design, then implement/integrate/verify directly; never hand back to Pi, another model, weaker acceptance or spend reset; resolve as handoff receipt, never new code passing old Pi checks.

**Updates**

Maximize useful weak-model work, not token consumption. Reported cost, including zero, is not verified billing; billing and full workflow cost are outside `metrics` ([cost scope](../../runtime/IMPLEMENTATION.md)).
