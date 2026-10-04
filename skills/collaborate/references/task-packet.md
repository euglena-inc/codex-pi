# Task packet

For the main session preparing a dispatch or whole-outcome repair. Rules: [Skill](../SKILL.md); commands: [runtime](runtime.md); keep APIs/paths/commands verbatim.

## Artifacts

- **Specification**: goal/design/facts/scope/acceptance; reference the project design; `task.md` only when no design exists.
- **Minimal work package** (what the brief+spec must name so Pi need not guess): one complete result, current source and facts, settled invariants/interfaces, real feedback and check entrypoints, remaining assumptions, resource limits, escalation. Read source and original evidence on demand; never paste whole history or fragment the outcome into mechanical steps.
- **Brief** (`brief.md`): short prompt pointing at spec+acceptance IDs; never copies design/Skill; a repair writes a new brief; dispatched briefs are never rewritten.
- **Phase contract**: frozen `--contract-file` snapshot; not a second PLAN.
- **Delivery report**: Pi's final message in the format below.

## Specification template

```markdown
# <phase/task> - <reviewable result>
## Goal/phase/plan
Goal -> phase result -> follow-ups + dependencies; state block end.
## Baseline/scope/facts
Repo/baseline/worktree; allowed+protected changes; verified facts+source; unknowns+verification; inputs/gaps.
## Design/difficulties/verification
Fact owners/writers; public interfaces; states/idempotency/concurrency/recovery; error/unknown meaning; per difficulty: solution -> failure/unlock -> verification; boundary example.
## Parallel work (if any)
Line outcome, sibling task+commit, waits, shared edits/resources, integrator, combined acceptance.
## Acceptance
| ID | Observable pass condition | Real command / evidence | Counter-example that must fail |
Fresh evidence+why; original evidence scope/invalidation; wrapper composition; full sets justified by integration impact; missing/skipped/unknown ≠ pass; manual/external proof.
## Repair/budget/escalation
Allocation evidence; Pi measurements + Codex/external sources; what Pi fixes alone; budgets+resource scope; escalate only for design contradiction, authority change or repeated ineffective repair, minimal repro.
```

## Must-ask checklist

Settle applicable items; if open: conservative reading + spec gap.

1. Which record/version wins: by content only (never name/mtime/order).
2. Byte-identical rerun/replay: same input, output and files.
3. Existing/same-name target: overwrite, refuse or version.
4. Dependence on time, timezone, environment or network.
5. Empty/missing/unknown values: never zero; block and list them.

## Writing convention

One reader; actor named; one action per sentence; condition beside its action; one term per thing; exact negation/permission/uncertainty; compound sentences only when every clause keeps its scope; no word count; never merge distinct domain actions.

## Dispatch prompt

```text
Complete <whole result>. Work only in <worktree> at baseline <commit>.
Read <constraints> and <spec>. Acceptance: <contract path+IDs>.
Settled decision/difficulty: <decision -> solution -> verification>. Allowed: <allowed>. Protected: <protected>.
You own implementation, targeted checks, repairs, scoped commits; never stop at code/partial PASS.
Run every frozen command via `check` with its exact `checkId`/`id`; `targetedCommand` for local repair only, keeping prior evidence; `final:true` for declared formal commands on the clean candidate.
Allowed inputs: <sources; gaps>. Do not bypass <forbidden sources>.
If <design contradiction / authority change / repeated ineffective repair>: evidence + one decision question.
If a must-ask item is open: conservative reading + spec gap.
Report in the delivery format. Stop at <completion boundary>.
```

Delete non-applicable lines; no repo rules; dispatch via `--prompt-file`.

## Contract mapping

pi_phase.py fields: phaseId, goal, result, baseline, scope, designRef+designSha256 (real file), acceptanceItems[] (id/description/checkId/command/passCondition/evidence + optional minRun/forbidSkip/targetedCommand/estimatedSeconds), budgetSeconds, commandTimeoutSeconds, resourceLimits, autonomousRepair, escalateWhen. Omit checkId (default id). Real commands only; unbuilt gates are deliverables, never placeholder PASS; targetedCommand never substitutes for command.

## Delivery format

```markdown
Result: <ready / partial / blocked>. Never write "accepted".
Candidate: <full commit, worktree, dirty files/owner>; task/phase/round.
Changes: <behavior + fact owners; design trade-offs>.
Integration: <peer commits, combined candidate, checks, open dependencies>.
Acceptance: <ID -> command, exit, counts -> receipt/evidence -> candidate>.
Open: <missing/failed/skipped/unknown items; impact; repairs tried; facts needed>.
Spec gaps: <must-ask items open + reading chosen>.
Evidence limits: <superseded/mismatched/inapplicable evidence, kept apart>.
```

Link logs, never paste; keep secrets out; no push or external action by default. A same-cause repair brief adds only differences: rejected candidate/items, shared cause, reproduction, related entries (reproduced/reachable/excluded), fix decision, scope, affected checks, valid evidence, downstream effect.
