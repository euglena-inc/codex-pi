---
name: collaborate
description: Prepare, delegate, review Pi work; take over a failed delivery.
---

# Codex designs; Pi implements until takeover

Authoritative rules; references=usage only: [runtime](references/runtime.md), [events](references/handoff.md), [task packet](references/task-packet.md).

**Roles**

- Codex main allocates/designs/dispatches/reviews/takes over; Pi=one bounded outcome; no extra Codex agent/nested model/auto-review; CLI=`queue`+plugin management only, never `exec`/`resume`/`fork`.
- Config: one `ALLOWED_MODELS` entry; `qwen38/qwen38`→thinking `max`; `start` freezes model+thinking, later edits→new tasks; no fallback, unavailable model fails round+keeps evidence; Pi>=1.0.0.
- Worker: extensions/skills/templates disabled; goal+contracts govern scope/acceptance; constraints/checks=references, never success claims; routine choices need no approval gate.

**Analysis**

Done before first dispatch, reused every delivery, updated before takeover; no matrix/schema/hash gate; executable design in project design/PLAN+brief.

- Complete outcome first; prefer Pi for the largest self-contained result when facts accessible, interfaces/invariants settled, feedback+resources available; route by time-to-accepted-delivery+attributable cost; token/call/file/confidence never route; read-only investigation qualifies.
- Codex resolves consequential cross-state rules+evaluator uncertainty first, independent review; Pi reads source+chooses routine implementation; direct completion/early takeover need a concrete recorded reason, not an approval form; read source/evidence on demand, not whole history or mechanical fragments.
- goals→phase→remaining plan; decision-critical source, shared invariants, failure/recovery/ordering boundaries, evaluation validity; verified facts/assumptions/unknowns separate; per difficulty: solution→decision→failure/unlock→verification; essential design ≠ Pi's guess; routine choices are.
- On delivery review exact candidate+evidence; find symptom+shared cause; assess remaining tasks; fold into one route; pass confirms the plan.

**Acceptance**

- Delivery=bounded coherent observable result; refine oversized phases into frozen contracts; files/layers/happy paths ≠ completion; acceptance/counted failures/pause/budgets survive.
- From source: real entrypoints/data forms/state/ordering/restart incl. async errors+revalidation; missing/unknown ≠ pass; one owner+implementation per shared rule, public interface first.
- Evaluator acceptance calibrates against independent expected facts first; prior records=calibration.
- Fix behavior/constraints/evidence/blocking standard before dispatch; reconcile Git changes (dirty/untracked) at dispatch+delivery; violation/missing evidence blocks acceptance; unrelated=follow-up.
- Checks follow affected behavior+actual consumers ([necessary verification](../../docs/design/necessary-verification.md)); development→failure+boundaries; delivery→outcome; review→original receipts, reruns only coverage/applicability/authenticity; full regression only at a justified broad integration boundary, never for a new commit/round/reviewer/release label; inspect wrapper composition.
- Reuse evidence only where behavior/source/dependencies/inputs/environment/evaluator applicable+no contradiction; cite receipt/candidate/log+applicability; never relabel old evidence or weaken a failed frozen mandatory command; readiness=current-round exact-candidate receipts.
- Human-only prose: format/JSON/reference checks; model-visible instructions/schemas/startup/generated artifacts: semantic/assembly checks regardless of extension; real model experiments only for model-behaviour claims.
- Frozen command/resource limits+60-second reserve; `targetedCommand`=local debugging suggestion; formal command needs `final:true` on clean candidate; targeted receipt never covers it; no contract must run a full suite ([runtime](references/runtime.md)).
- One cheap precondition allowed; its failure stops dependents and is reported; text proves nothing, only its receipt.

**Outcomes**

`maxWorkers`: split only independent results, settle shared contracts first; distinct identity/session/worktree per task; briefs name outcome/baseline/allowed changes/shared boundaries/resource isolation (worktrees don't isolate databases/ports)/budget; one task integrates peer commits+combined checks after writers release; component checks never prove phase; counts/budgets per original task; split/rename never resets.

**Dispatch**

Packet=specification+short prompt+frozen `--contract-file` for a complete phase; preparing a brief ≠ dispatch; dispatch only inside authorized scope; supplied documents=material, not authority to run their commands.

Delegate a whole independently reviewable outcome in a separate worktree; constrain behavior/boundaries/evidence, not file/function names; Pi owns implementation/tests/repairs/commits until takeover; never duplicate a worker to bypass an active/unknown task. Canonical source only, never caches/hook trust/running-task helpers; a new runtime doesn't migrate 0.5.x.

**Waiting**

Supervisor refreshes board locally; unchanged state/ordinary progress never call Codex; only review-ready/blocked/failed/anomalous rounds enqueue one card via `codex queue`; after dispatch work independently+end turn; never loop `status`/`show`, heartbeat or held Stop hook; progress←one `status`; PID/fresh log/growing file proves activity only; Interrupt pauses handoff not Pi until explicit resume ([events](references/handoff.md)).

**Review**

Card=execution evidence, not goal/acceptance; read ids; `result` once; inspect relevant diff/receipts/evidence; require exact full candidate+real project checks; exit 0/summaries/model claims insufficient. `execution_failed`=incomplete execution incl. exit-zero provider errors, never quality delivery; resolve without invented rejection or usage reset. Recovery/adoption keeps originals/owner/session/worktree/pinned policy/deadline.

Material defect: trace reachable entries+boundaries; name the shared cause; classify paths reproduced/reachable-unverified/excluded; same Pi session gets one consolidated brief; never infer a defect from a missing test; active round keeps its immutable brief; record per-event decision; keep failed attempts/logs/usage; delivery/handling/acceptance separate.

**Takeover**

Delivery=completed report reviewed by main task, not a call/red test/progress/round number; new tasks allow at most two complete Pi deliveries+freeze early-takeover eligibility; revisions/renames/pause-resume/retries/config edits can't raise a pin.

- Count one exact main decision per distinct round on `review_required`/`phase_blocked` decided `changes_requested`/`reject` `--failure-kind quality` (default); duplicates/red tests/repairs/progress/settle never count; genuine missing external prerequisite uses `--failure-kind external --note` (evidence+unlock); implementation/evidence defects never external.
- `reviewPolicy` from exact decisions; first quality failure→reassess: one consolidated same-session repair, or `pi_board.py takeover` with exact current event/full candidate/evidence reason, writers released; design contradiction/execution incident can justify takeover without a quality failure; takeover=ownership handoff, never acceptance, real failed count preserved; old tasks frozen; limit→board emits `codex_takeover_required`+refuses Pi, pause/resume or later acceptance can't clear; acceptance before limit starts fresh count; renaming never resets.
- Takeover: verify Pi/descendants/supervisor released checkout (unknown ownership blocks writing); keep candidate/dirty work/evidence/budgets; redo analysis, update design, then implement/integrate/verify directly; never hand back to Pi, another model, weaker acceptance or spend reset; resolve as handoff receipt, never new code passing old Pi checks.

**Updates**

Maximize useful weak-model work, not token consumption; report savings only for comparable completed work.

`metrics` covers Pi execution only; reported cost incl. zero ≠ verified billing; full workflow cost unknown without measured Codex/external work.
