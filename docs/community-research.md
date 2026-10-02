# Community research — 2026-09-26

Question: reusable Pi delegation from the existing Codex main conversation, with no Codex CLI or additional Codex agent; compact evidence, recoverable execution and project-owned acceptance.

| Source | Relevant capability | Gap for this workflow |
| --- | --- | --- |
| [howznguyen/pi-delegate-mcp](https://github.com/howznguyen/pi-delegate-mcp), inspected commit `8e258b772fb541c6c6bfc12ce949a3210da4cf5c` | MIT MCP server; Pi SDK delegates; model selection; spawn/run/steer/follow-up; read-only tools by default | `src/registry.ts` keeps workers in an in-memory Map; README recommends status polling; no project-specific durable acceptance receipt/worktree coordination equivalent to our existing runner. Its background spawn does not establish Codex task wake-up. |
| [pandysp/pi-mcp-server](https://github.com/pandysp/pi-mcp-server) | MIT MCP Pi/reply tools; session reuse, optional sandbox integration | Inspected package is 0.1.2 on Pi SDK `^0.52.9`; `src/session-store.ts` uses an in-memory map with eviction. Not a direct substitute for persistent project task/round recovery. |
| [minghinmatthewlam/pi-subagents](https://github.com/minghinmatthewlam/pi-subagents) | Pi RPC child agents, steer/wait, Pi-native completion notification | Its parent is Pi. A notification into a Pi parent is not evidence of notification into this existing Codex conversation. |

The installed-plugin directory search for “Pi coding agent” returned broader frameworks and unrelated services, not an exact matching Pi delegate plugin. This is a bounded search, not proof no other implementation exists. No community package was installed or executed for this investigation; the first two sources were cloned for reading under `/tmp/pi-collab-research-20260926`.

Decision after reviewing the local-only requirement: a skill plus direct Python-to-Pi CLI invocation is sufficient; MCP would add an unnecessary server and dependencies. Reuse our existing tested Python lifecycle, process-group termination and evidence helpers from the bake project; retain project constraints locally. Do not add a second model orchestration platform or copy a community status-polling workflow. No community source is copied merely for its README examples.

The original bake runner's Codex queue path and automatic Codex reviewer integrations are deliberately excluded. Without a verified native push/wake path, the plugin supports persistent background execution and explicit result collection, not unattended Codex continuation. Token savings require later comparable phase measurements.

## Native Sol-Luna — 2026-10-02

Question: can Sol 6.1 Medium and native Luna Max reduce Sol work across projects,
with Codex-Pi retained? This is a new route, not a change to the historical Pi
investigation above. No community installer, MCP service or routing code was run
or copied. Public reports were inspected; their experiments were not reproduced.

| Source | Evidence available | What it establishes |
| --- | --- | --- |
| [Official subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) | Supported model configuration, native orchestration and context separation | Capability support; explicitly warns of additional token work, not a savings benchmark |
| [donvito/codex-astra-luna-orchestrator](https://github.com/donvito/codex-astra-luna-orchestrator) | Ready-to-copy Sol Medium/Luna Max profile, role TOML, installation and usage-report tooling | A reusable community implementation exists; no inspected controlled savings experiment for this exact pairing |
| [Sol 6.1 + Luna report](https://www.reddit.com/r/codex/comments/1wuo421/psa_sol_61_slowness_might_get_fixed_in_next_few/) | Recent reports of using Sol Medium with Luna Max | Operational experience, not matched token/quality evidence |
| [Luna Max orchestration discussion](https://www.reddit.com/r/codex/comments/1vh9nc6/how_to_efficiently_use_luna_max_subagents/) | Reports that Sol orchestration itself consumes substantial usage | Supports reducing handoffs; older model generation, subjective usage evidence |
| [Mixed four-arm benchmark](https://github.com/suenot/codex-jev-router-benchmarks/blob/main/benchmarks/mixed-2026-09-26/REPORT.md) | Public method/data; six fixture tasks, three repeats across four arms, 72 completed runs; all arms passed 18/18 strict checks | Forced routed children cost 69.7% more than one Sol-xhigh agent in that fixture, despite being cheaper than fixed Sol-high children. The children used mixed settings, not our fixed 6.1 Medium/6 Luna Max policy; estimated API cost, not subscriptions |
| [Routing audit](https://github.com/suenot/codex-jev-router/blob/main/AUDIT.md) | Records parent/child costs and scope limits; includes a single local code-fix pair where delegation was cheaper but slower | Stronger than anecdotes, still author-published rather than independently replicated; no universal savings claim |
| [Effort-update issue #47843](https://github.com/openai/codex/issues/47843) | Reproduction code, randomized requests and trace checks for an experimental in-thread effort update | Fixing effort at initial spawn avoids relying on that reported mid-thread behavior; not reproduced here |

The bounded search found runnable mechanisms and trace-backed reports, but no
independently replicated multi-project benchmark for the exact requested pairing.
Do not describe profile tests, stars, token totals or a low weekly usage percentage
as verified completed-task savings. Some reports are about GPT-5.6, GPT-6 Sol or
different efforts; they do not establish GPT-6.1 Sol behavior.

Chosen implementation: one shared plugin Skill, one Luna Max per substantial
outcome, no inherited conversation, no nested review agents, same-child repair,
bounded evidence reads, and ordinary tests repaired within Luna. Small direct
lookups stay with Sol. Keep project contracts local and identities separate;
avoid copying community role/installer layers into every project. The offline
comparison helper retains missing/failed/unverified measurements without claims.
See [mechanism](design/sol-luna-native.md) and [native validation](validation/sol-luna-native-20261002.md).
