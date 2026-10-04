# Task packet

For the main session preparing a dispatch or a whole-outcome repair. Rules and the whole-task analysis: [the Skill](../SKILL.md); commands: [runtime](runtime.md). Write in the user's language. Keep APIs, paths and commands verbatim.

Before preparing this packet, define the complete outcome and prefer the configured Pi model for the largest reasonably self-contained result when facts are accessible, consequential interfaces are settled, real feedback exists and the authorized resources can support delivery. Codex direct completion or early takeover needs a concrete task-specific reason recorded in the existing design or review record (for example unresolved cross-state semantics dominating the remaining work, untrustworthy feedback, or repair/coordination cost beyond the likely benefit). Resolve consequential design uncertainty first; preserve Pi's freedom to read source and choose routine implementation. Design depth follows the actual risks, not a fixed document length.

## Artifacts

- **Specification**: the goal, settled design, facts, scope and acceptance. Reference the existing project design. Write `task.md` only when no design exists.
- **Brief** (`brief.md`): the short prompt below. It points at the spec and the acceptance IDs. Never copy the design or the Skill into it. A repair writes a new brief. A dispatched brief is not rewritten.
- **Phase contract**: `--contract-file`, a frozen snapshot. It is not a second PLAN.
- **Delivery report**: Pi's final message, in the format below.

## Specification template

Omit what does not apply; link, do not restate.

```markdown
# <phase/task> - <reviewable result>
## Goal, phase, later plan
Overall goal -> this phase's complete result -> authorized follow-ups and real dependencies. State where this block ends.
## Baseline, scope, facts
Repo, exact baseline and worktree. Allowed and protected changes. Verified facts with source or version. Unknowns and how to verify them. Allowed inputs and gaps.
## Design, difficulties, verification
Fact owners and writers. Public interfaces. States, idempotency, concurrency, recovery. The meaning of errors and of unknown values. For each consequential difficulty: solution -> failure or unlock path -> verification. Give one decisive boundary example where units, time zones, nulls, paging or dedup matter.
## Two-line parallel work (if any)
This line's outcome, sibling task and input commit, what waits, shared edits and resources, integration owner, combined acceptance.
## Acceptance
| ID | Observable pass condition | Real command / evidence | Counter-example that must fail |
Missing, skipped or unknown evidence is not a pass. Mark manual or external proof.
## Repair, budget, escalation
Allocation and its evidence; Pi execution measurements and the separate sources for Codex/external effort. What Pi fixes alone. Command and phase budgets and resource scope. Escalate only for a design contradiction, an authority change or repeated ineffective repair; include the minimal reproduction.
```

## Must-ask checklist

Settle each applicable item in the specification. If one is left open, Pi takes the conservative reading and reports a spec gap.

1. Which record or version wins when several exist: by content only (never by name, mtime or order).
2. Byte-identical rerun and replay: same input must give the same output and the same files.
3. An existing or same-name target: overwrite, refuse or version.
4. Dependence on time, timezone, environment or network.
5. Empty, missing and unknown values: never zero; block and list them.

## Writing convention

Write for one reader. Name the actor, and give one independently understandable action per sentence. Keep each condition next to the action it limits. Use one term for one thing. Keep negation, permission and uncertainty exact. Split a compound sentence only when every clause keeps its own connection and scope. Do not impose a word count and do not merge distinct domain actions.

## Dispatch prompt

```text
Complete <whole result>. Work only in <worktree> at baseline <commit>.
Read <constraints> and <spec path/sections>. Acceptance is <contract path and IDs>.
Settled decision and difficulty: <key decision -> solution -> verification>. Allowed scope: <allowed>. Protected scope: <protected>.
You own the implementation, targeted checks, ordinary repairs and scoped commits. Do not stop at code written or a partial PASS.
Run every formal check with the `check` tool. If an item declares a `targetedCommand`, run it first for local repair. Keep `final:true` for its formal command on the clean candidate.
Allowed inputs: <allowed sources; gaps>. Do not bypass <forbidden sources>.
If <design contradiction / authority change / repeated ineffective repair>, return the evidence and one decision question.
If a must-ask item is open, take the conservative reading and report a spec gap.
Report in the delivery format. Stop at <completion boundary>.
```

Delete lines that do not apply; do not paste repository rules. Dispatch through `--prompt-file`.

## Mapping to the contract

Fields of `runtime/pi_phase.py`: `phaseId`, `goal`, `result`, `baseline`, `scope`, `designRef` + `designSha256` (a real worktree file), `acceptanceItems[]` (`id`, `description`, `checkId`, `command`, `passCondition`, `evidence`, optional `minRun`, `forbidSkip`, `targetedCommand`, `estimatedSeconds`), `budgetSeconds`, `commandTimeoutSeconds`, `resourceLimits`, `autonomousRepair`, `escalateWhen`. Commands must be real. An unbuilt gate is part of the deliverable, never a placeholder PASS. `targetedCommand` is optional. It is only a local repair suggestion and never substitutes for the formal `command`. `estimatedSeconds` is a finite positive planning estimate, not a completion guarantee. Before dispatch, confirm that the spec and contract share the goal and boundary, that inputs exist, and that pass conditions are falsifiable.

## Delivery format

```markdown
Result: <ready for review / partial / blocked>. Never write "accepted".
Candidate: <full commit, worktree, dirty files and owner>; task/phase/round.
Changes: <effect on behavior and fact owners; design trade-offs>.
Integration (if any): <peer commits, combined candidate, checks run, open dependencies>.
Acceptance: <ID -> command, exit, counts -> receipt and evidence -> candidate>.
Open: <missing, failed, skipped or unknown items; impact; repairs tried; facts needed>.
Spec gaps: <must-ask items left open and the reading chosen>.
Evidence limits: <superseded, mismatched or inapplicable evidence, kept apart>.
```

Link logs; do not paste them. Exit 0, Pi's summary and a local PASS never replace acceptance. Keep secrets out. Do not push or take external action by default. A same-cause repair brief adds only the differences: rejected candidate and items, shared cause, reproduction, related entries (reproduced, reachable, excluded), fix decision, scope, affected checks, valid evidence, downstream effect.
