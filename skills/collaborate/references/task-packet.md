# Task packet

For the main session preparing a dispatch or a whole-outcome repair. Rules and the whole-task analysis: [the Skill](../SKILL.md); commands: [runtime](runtime.md). Write in the user's language; keep APIs, paths and commands verbatim.

## Artifacts

- **Specification**: goal, settled design, facts, scope, acceptance. Reference the existing project design; write `task.md` only if none exists.
- **Brief** (`brief.md`): the short prompt below, pointing at the spec and acceptance IDs; never copy the design or the Skill. A repair writes a new brief; dispatched ones are not rewritten.
- **Phase contract**: `--contract-file`, a frozen snapshot, not a second PLAN. **Delivery report**: Pi's final message below.

## Specification template

Omit what does not apply; link, do not restate.

```markdown
# <phase/task> - <reviewable result>
## Goal, phase, later plan
Overall goal -> this phase's complete result -> authorized follow-ups and real dependencies; where this block ends.
## Baseline, scope, facts
Repo, exact baseline, worktree; allowed and protected changes. Verified facts with source or version; unknowns and how to verify; allowed inputs and gaps.
## Design, difficulties, verification
Fact owners and writers; public interfaces; states, idempotency, concurrency, recovery, meaning of errors and unknown. Each consequential difficulty -> solution -> failure or unlock path -> verification. One decisive boundary example where units, time zones, nulls, paging or dedup matter.
## Two-line parallel work (if any)
This line's outcome, sibling task and input commit, what waits, shared edits and resources, integration owner, combined acceptance.
## Acceptance
| ID | Observable pass condition | Real command / evidence | Counter-example that must fail |
Missing, skipped or unknown is not a pass; manual or external proof is marked.
## Repair, budget, escalation
What Pi fixes alone; command and phase budgets, resource scope; escalate only for a design contradiction, an authority change or repeated ineffective repair, with the minimal reproduction.
```

## Must-ask checklist

Settle each applicable item in the specification. If one is left open, Pi takes the conservative reading and reports a spec gap.

1. Which record or version wins when several exist: by content only (never by name, mtime or order).
2. Byte-identical rerun and replay: same input must give the same output and the same files.
3. An existing or same-name target: overwrite, refuse or version.
4. Dependence on time, timezone, environment or network.
5. Empty, missing and unknown values: never zero; block and list them.

## Dispatch prompt

```text
Complete <whole result>. Baseline <commit>; work only in <worktree>.
Read <constraints> and <spec path/sections>; acceptance is <contract path and IDs>.
Settled: <key decision and difficulty -> solution -> verification>. Scope: <allowed; protected>.
Own implementation, targeted checks, ordinary repairs and scoped commits; do not stop at code written or a partial PASS.
Run formal checks with the `check` tool.
Inputs: <allowed sources; gaps>; do not bypass <forbidden sources>.
On <design contradiction / authority change / repeated ineffective repair> return the evidence and one decision question.
If a must-ask item is open, take the conservative reading and report a spec gap.
Report in the delivery format; stop at <completion boundary>.
```

Delete lines that do not apply; do not paste repository rules. Dispatch through `--prompt-file`.

## Mapping to the contract

Fields of `runtime/pi_phase.py`: `phaseId`, `goal`, `result`, `baseline`, `scope`, `designRef` + `designSha256` (a real worktree file), `acceptanceItems[]` (`id`, `description`, `checkId`, `command`, `passCondition`, `evidence`, optional `minRun`, `forbidSkip`, `targetedCommand`, `estimatedSeconds`), `budgetSeconds`, `commandTimeoutSeconds`, `resourceLimits`, `autonomousRepair`, `escalateWhen`. Commands must be real; an unbuilt gate is part of the deliverable, never a placeholder PASS. `targetedCommand` is only a local repair suggestion and never substitutes for the formal `command`; `estimatedSeconds` is a finite positive planning estimate, not a completion guarantee. Before dispatch: spec and contract share goal and boundary, inputs exist, pass conditions are falsifiable.

## Delivery format

```markdown
Result: <ready for review / partial / blocked>; never "accepted".
Candidate: <full commit, worktree, dirty files and owner>; task/phase/round.
Changes: <effect on behavior and fact owners; design trade-offs>.
Integration (if any): <peer commits, combined candidate, checks run, open dependencies>.
Acceptance: <ID -> command, exit, counts -> receipt and evidence -> candidate>.
Open: <missing, failed, skipped or unknown items; impact; repairs tried; facts needed>.
Spec gaps: <must-ask items left open and the reading chosen>.
Evidence limits: <superseded, mismatched or inapplicable evidence, kept apart>.
```

Link logs, do not paste. Exit 0, Pi's summary or a local PASS never replace acceptance. Keep secrets out; no pushing or external action by default. A same-cause repair brief adds only the differences: rejected candidate and items, shared cause, reproduction, related entries (reproduced, reachable, excluded), fix decision, scope, affected checks, valid evidence, downstream effect.
