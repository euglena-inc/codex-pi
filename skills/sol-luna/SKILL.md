---
name: sol-luna
description: Use native Codex GPT-6.1 Sol and GPT-6 Luna collaboration for a complete implementation, repair and review, or compare it with Codex-Pi. Use when this route is requested; do not replace an active Pi task.
---

# Sol decides and reviews; Luna owns implementation

This is a separate route alongside `collaborate`. The existing main chat uses
`gpt-6.1-sol` at **medium**; one native `gpt-6-luna` subagent at **max** implements a bounded, complete
outcome. Ordinary exploration, edits, tests and repairs stay in Luna's context.
Sol owns consequential design, exact-candidate review and final acceptance.

## Native capability and ownership

Use the tools actually exposed by the current client. Require explicit child
model selection, a fresh child context, continuation of the same child, and
native result waiting. In this client's collaboration interface these are
`spawn_agent(model="gpt-6-luna", reasoning_effort="max", fork_turns="none")`,
`followup_task`, `wait_agent` and `interrupt_agent`. Other clients may expose
different names; inspect their schemas rather than copying these arguments.
Do not substitute a user-owned sidebar chat, `codex exec`, Pi, an MCP service,
heartbeat, queue bridge or a second supervisor for unavailable native tools.
Report the unavailable capability and continue independent authorized work.

Keep the main model Sol and the worker model Luna for the outcome. Do not claim
the pairing is verified from a prompt or requested model alone. Use available
runtime metadata; mark unavailable actual-model evidence unknown. If the main
model cannot be selected or established, surface that limitation instead of
silently changing the route. Keep Sol medium and Luna max fixed; do not silently
lower effort, raise Sol effort, or enable Ultra. A different policy is a separately
authorized comparison, not a fallback. Do not spawn nested workers or reviewers.

## Multiple projects, one shared plugin

Read [project usage](references/projects.md) on first dispatch or recovery. The
installed plugin supplies this skill to each supported project chat; it does not
require `.agents/codex-pi.json`, copied agent roles, global configuration changes,
or a per-project supervisor for this route. Pi configuration remains Pi-only.

Bind each outcome to the current project checkout, Git common directory, owner
chat, native child identity, baseline, scope, acceptance and remaining budget in
the project's existing task record. Keep private evidence outside tracked files,
for example under that repository's Git common directory. Repositories with the
same directory name or candidate SHA are still distinct projects. Never read
other projects' task records, scan all chats, or reuse another project's child.
Across projects use separate fresh children and independent resources; for repair
of the SAME outcome reuse its original child. Keep project domain contracts in
the project, not in the shared plugin. Missing records can be created within the
authorized task; they do not create a user approval gate.

Native subagents share the parent's checkout and permissions. A task instruction
is not a filesystem sandbox. Default to one writer: Sol and unrelated workers
must not edit Luna's files while it runs. Use an existing suitable isolated
checkout or a native managed worktree when isolation is needed; name its absolute
path in the brief. If the client cannot bind the child to that checkout, do not
claim enforced isolation. Parallel route comparisons require separate worktrees
and independent ports, databases and output locations.

## Give Luna an executable outcome

Sol resolves decision-critical design from relevant source and project contracts
once, then reuses it at review. Delegate a whole observable result, not a series
of individual edits. Give Luna the goal, baseline, allowed checkout and changes,
existing design references, chosen consequential boundaries, acceptance commands,
evidence location and resource/time limits. Routine implementation choices belong
to Luna. Do not copy the main conversation or the Pi skill into its brief.

For unfamiliar code, a bounded Luna exploration may return entrypoints, cited
facts and unresolved decisions before implementation. Continue that same child
after Sol resolves the design; do not require a separate exploration agent or
repeat Sol's own source audit. Small work without enough execution to amortize
delegation can stay with Sol; record it as direct execution, not Sol-Luna.

Luna owns implementation, scoped commits, tests and ordinary failure repair.
It reads project constraints and works only in the designated scope. No ordinary
progress report or red test requires Sol guidance. Return early for a genuine
external prerequisite, consequential unresolved design, execution failure or
budget exhaustion, with the evidence and unlock condition. Never invent a pass.

## Wait and review complete deliveries

Use native event waiting and continue independent work. Do not loop on status,
request progress messages, or load the child transcript. Respect tool wait bounds
and keep the user informed during ongoing work. Host completion notifications
may still reach Sol; the skill cannot promise zero model wakeups or recovery
across arbitrary client shutdowns. An interrupt does not prove tools or writers
have stopped. Establish writer release before takeover or integration.

Luna returns one short delivery card: outcome/attempt, exact full candidate SHA,
actual model evidence if available, changed scope, each acceptance result,
remaining failures/unknowns, and evidence paths. Keep routine logs on disk outside
tracked files. Use a concise summary (aim for 1200 UTF-8 bytes); this is guidance,
not a runtime-enforced byte cap. Unknown required evidence remains blocking.

Sol reads the relevant diff and original evidence, verifies decision-critical
behavior and the exact combined candidate, then records accept, quality repair,
external block or execution incident in the existing task/design record. Summaries,
exit zero and green component tests do not constitute acceptance. Avoid repeating
unchanged checks with valid evidence, but rerun checks affected by repairs.

Allow two complete Luna quality deliveries for the original outcome. After the
first reviewed quality failure, Sol traces shared causes and gives one coherent
repair brief through `followup_task` to the SAME child. Internal test failures,
exploration, duplicate reports, external blocks and execution incidents do not
consume a quality delivery. They still consume the original time/spend budget.
After a second quality failure, Sol takes over only after writer release,
preserving the candidate, dirty work and evidence. Never start another Luna to
reset the count. If the original child cannot be continued, report recovery as
blocked rather than silently creating a fresh implementation attempt.

These counts, scope and budgets are maintained by the main chat in its existing
task record; they are not Pi's persistent admission or process guards. On recovery,
reconcile the native child identity, writer state and previous decisions before
continuing. Do not claim a hard token cap when the runtime provides no such cap.

## Keep both routes and compare fairly

Use `collaborate` for Codex-Pi and this skill for Sol-Luna. Keep route, worker
identity, model settings, acceptance and evidence fixed per outcome; do not migrate
an active task. Read [comparison guidance](references/comparison.md) only when
running or reporting a comparison. The offline `scripts/compare_routes.py` groups
private normalized records by project and matching task contract; it does not
launch agents or collect usage. A small brief or fewer output bytes is not a
measured Sol-token saving. Missing usage/model evidence stays unknown. Native
validation and community evidence limits are in the
[implementation notes](../../docs/design/sol-luna-native.md).
