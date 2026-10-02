# Dispatch in any project

The shared plugin supplies `$sol-luna`; the consuming project supplies its own
domain rules and checks. In a project chat select GPT-6.1 Sol / Medium, then ask:

```text
Use $sol-luna to complete this authorized outcome. Keep Sol 6.1 Medium for design
and acceptance, and one fresh-context native GPT-6 Luna Max for implementation.
Read this project's AGENTS.md and relevant design. Give the worker a complete
result, let it repair ordinary failures, and return only complete delivery or a
real blocker. Continue the same child on repair. Do not create nested reviewers.
```

Use the tool schema exposed in that chat. Explicit model/effort plus
`fork_turns="none"` are available in the current desktop collaboration interface.
Do not assume a full-history fork can select a different model. If model selection
or fresh context is unavailable, report it; do not start a separate model CLI.
An unavailable parent-selection tool means the user must select the parent model
in the client; prompts cannot change that setting. Report this precisely instead
of implying that the plugin forces the parent configuration.

Resolve checkout and common directory with `git rev-parse --show-toplevel` and
`git rev-parse --git-common-dir`. Use the designated worktree for implementation,
and project-relative rule/design references. Keep the brief self-contained:

```text
Project / designated checkout: absolute path, baseline full SHA
Outcome: observable complete result
Scope and boundaries: allowed writes, domain rules, chosen consequential design
Acceptance: real commands and required observations; missing evidence blocks
Budget: original deadline/resource bounds; no reset on repair
Evidence: private directory within this project's Git common directory
Return: full candidate SHA, per-item results, failures/unknowns, evidence index
```

Store only the recovery facts needed in the existing private task record: project,
owner, child identity, chosen model/effort, baseline, candidate, quality decisions,
deadline and writer state. No new board format, global project registry or
controller is required. Do not write raw sessions or real identities into public
project documentation. When continuing after interruption, establish that the
same child is available and which tools/writers are active. If the client cannot
resume it, preserve evidence and report the blocker; a new agent is not the same
session. Model instructions do not kill escaped processes or enforce time limits.

For parallel Codex-Pi / Sol-Luna comparisons, use different worktrees and isolated
databases, ports and evidence directories. Only integrate one chosen result after
both writers release. The Pi worktree guard can forbid other worktrees; do not
ask Pi to inspect Luna's checkout. Let the main reviewer compare committed diffs.
Use a stable private project label in comparison records; two projects may have
identical baselines but their measurements must never be joined.

The plugin install/update uses its existing marketplace and supported installation
flow. Keep its identity `codex-pi`; it distributes both `collaborate` and `sol-luna`.
Do not hand-edit managed plugin caches or trust settings. Existing Pi tasks keep
their frozen runtime and model. Source implementation is distinct from an
installed-plugin load test; use a new chat after the installed skill is available.
