# Codex capability paths for Codex-Pi

Research record: 2026-10-05, main session. Sources are the local installed Codex CLI
`0.157.1` (`--help` surfaces and `codex features list`), the JSON Schema generated locally by
`codex app-server generate-json-schema`, the official documentation pages
`developers.openai.com/codex/{hooks,plugins,subagents,app-server,skills,config-*}`, and this
repository's own history and validation reports. Identifier-like values were removed; quoted
figures come from sanitized reports already tracked in this repository. Nothing here is a claim
about model capability, speed or real token pricing.

## 1. What each release actually bought

| Version | Bottleneck addressed | Evidence in this repo | Residual problem |
| --- | --- | --- | --- |
| 0.1–0.4 | durable execution on the Python side: isolated worktrees, process groups, check receipts, revision-safe store, takeover | `docs/validation/phase-autonomy-o1-*`, `o2-eventfold-*`, `o4-release-hardening-*` | delivery depended on a human |
| 0.5 → 0.6 | delivery channel: `codex queue` plus board projection; Stop-hook delivery deleted | `docs/validation/cli-queue-20260926.md`, `docs/validation/desktop-0.6.0-20261002.md` | enqueue is not wake-up |
| 0.6.1 | crash recovery, store repair, unknown-round collection | `docs/validation/recovery-0.6.1-20261002.md` | closeout stayed manual |
| 0.7.0/0.7.1 | native codemode registration, check budgets and concurrency, targeted repair, test-environment isolation | `docs/validation/check-budget-0.7.1-*`, `check-concurrency-0.7.1-*` | resources are hand-declared |
| 0.8.0–0.8.3 | connection diagnostics, instruction-clarity pilot, bounded allocation with explicit takeover and cost scope, weak-model-first complete outcomes | `docs/validation/instruction-clarity-pilot-20261003.md`, `bounded-allocation-0.8.2.md` | weak-model discipline is still text |
| 0.8.5–0.8.7 | outcome observation that keeps a Pi failure and a Main completion together, frozen size budget, deferred on-demand tier | 42692 → 21358 loaded bytes (−49.97%), 4125 deferred | the budget measures caps, not measured load |

The instruction pilot is the key empirical anchor: six scenario families, two instruction texts,
three repetitions, 36 real sessions on the same model and gateway, differing only in instruction
text. Observed per-family outcomes included `estimate_correction` 1/3 → 2/3,
`insufficient_budget` 2/3 → 3/3, `failure_vs_refusal` 3/3 → 3/3, and
`fulfilled_promise_failed_check` 3/3 → 3/3 while wall time rose from 49.6 s to 68.6 s. Instruction
text alone moves a small number of behaviours and can regress timing; further work should therefore
prefer structural harness changes over more prose.

## 2. Official Codex surface relevant to this plugin

### 2.1 Hooks (`hooks` feature stable)

Nine events are documented: `PreToolUse`, `PostToolUse`, `PreCompact`, `UserPromptSubmit`,
`SubagentStop`, `Stop`, `Interrupt`, `SessionStart`, `SessionEnd`. A command hook receives one JSON
object on stdin and may return `continue: false` with `systemMessage`, a blocking
`decision: "block"`, an approval/rewrite decision, or
`hookSpecificOutput.additionalContext` to add model-visible context without blocking;
`additionalContextLimit` bounds that text. Exit code 2 with a reason on stderr also blocks. Trust is
recorded against the exact hook hash, so a changed hook is skipped until re-reviewed; plugin-bundled
hooks are not trusted automatically; background hooks cannot block; MCP hooks exist but are
unsupported with cloud orchestration.

This plugin registers three of the nine events (`Interrupt`, `SessionStart`, `UserPromptSubmit`) and
already emits `hookSpecificOutput.additionalContext` for transport recovery evidence.

`PostToolUse` was evaluated and deliberately not adopted in this step: it fires for every tool call
in the main session, so its constant cost is paid by every round to buy a signal that prompt- and
session-boundary presence already provides. Revisit only with measured evidence that a boundary-time
digest is insufficient.

### 2.2 Subagents (`multi_agent` stable, `multi_agent_v2` stable but off)

Codex can delegate independent work to specialized subagents, locally with per-agent model
configuration, and exposes a `SubagentStop` hook whose output can continue the subagent flow. The
official documentation states plainly that subagent workflows consume more tokens than comparable
single-agent runs. This is a third execution arm for a controlled comparison against Pi rounds and
against main-session completion, not a drop-in replacement for the durable board/contract/receipt
layer.

### 2.3 app-server protocol

JSON-RPC 2.0 over WebSocket or a Unix socket, with authentication, conversation history, approvals,
streamed agent events and mid-turn steering. It is labelled experimental and unsupported for
production workloads, and the installed desktop build exposed no default control socket when tested
(`docs/validation/cli-queue-20260926.md`). It is a candidate for read-only diagnosis of thread and
route state, not a delivery channel.

### 2.4 Skills and the loaded-context budget

Codex loads each skill's name, description and path in an initial list governed by a budget; when
too many skills are installed, descriptions are shortened first, front-loading the key use case and
trigger words, while the selected skill's full `SKILL.md` is still read. This is the same mechanism
the 0.8.6/0.8.7 size work targets, and it justifies the two-tier split and the front-loaded
description.

### 2.5 CLI commands this plugin does not use yet

`codex review` (non-interactive review, instructions from stdin with `-`), `codex doctor`
(installation, config, auth and runtime health), `codex agents` (browse sessions on the local
app-server daemon), `codex exec`, `codex features`, `codex fork/resume/archive`, `codex apply`,
`codex sandbox`, `codex cloud`.

### 2.6 Feature flags worth watching

`worktrees` stable, `memories` stable but disabled, `plugin_sharing` and `remote_plugin` stable,
`recommended_plugins` stable and off, `agent_message_board` under development, `code_mode_host`
stable, `daemon_auto_start` stable, `skill_search` stable, `external_agent_memory_import` under
development.

## 3. Remaining bottlenecks, each with evidence

- **B1 delivery is not wake-up.** A queued card was only consumed when the user opened the paused
  thread (`docs/validation/desktop-0.6.0-20261002.md`); `docs/community-research.md` records that no
  verified native push/wake path exists.
- **B2 discipline is carried by prose.** The whole model-visible rule set is 8402 bytes of
  "never/only/must" instructions, and the pilot above shows the weak model still misses behaviours
  such as "unknown is not zero".
- **B3 context load is asserted, not measured.** `tests/test_docs.py` pins byte caps; nothing
  measures what a round actually loads and emits, and `docs/design/context-workflow.md` refuses a
  savings claim without a provider-side comparison.
- **B4 closeout and metrics are manual.** `record-outcome` is hand-authored, `acceptance` stays
  `not_verified`, and external token/cost remains unknown, so "is the Pi route worth it" is still
  unanswered.

The hook-driven presence gap sits inside B2: today `SessionStart`/`UserPromptSubmit` show transport
anomalies only. A task whose round is review-ready with nothing anomalous produces no line, so main
must remember to poll the board; and none of the phase pins (contract hash, candidate HEAD, takeover
latch, failed-delivery count) survive an automatic compaction.

## 4. Prioritized paths

| Priority | Path | Basis | Expected effect | Cost / risk |
| --- | --- | --- | --- | --- |
| P0-1 | Board presence through hook `additionalContext`: pending decision events, review-ready/blocked rounds, takeover latch, pins at session and prompt boundaries | official hook output contract is stable; B2/B3 evidence | removes the "forgot to check the board" failure class without extra model turns; needs a real end-to-end check | small; must stay under `additionalContextLimit` and mark truncation |
| P0-2 | `PreCompact` pin digest and `SessionEnd` closeout preparation | `PreCompact`/`SessionEnd` exist; B4 and this repo's compaction-handoff research | surviving pins across compaction; closeout prepared automatically, never fabricated | small-medium; must never imply acceptance or write a status it cannot support |
| P0-3 | `codex review` as an optional independent reviewer check in a phase contract | official non-interactive review command | an independent, non-Pi, non-self review signal bound to the candidate | small-medium; spends real tokens, must be opt-in and inside the declared cost scope |
| P1-1 | `codex doctor` plus `codex plugin list/add` inside the formal install check | official health diagnosis | turns "installed but hooks untrusted" into a check result | small |
| P1-2 | Derive check-resource estimates from stored check receipts instead of hand-declared values, unknown when no history | 0.7.1 receipts and admission | fewer wrong declarations degenerating to serial | small; advisory only |
| P1-3 | Measure per-round loaded bytes (skill tier hit, tool output volume) and publish it in `metrics` | B3; completes 0.8.6/0.8.7 | replaces estimates with a trend line and can falsify the tiering claim | medium |
| P2-1 | Three-arm comparison (Pi round / main completes / native subagent) with frozen criteria, run once | `multi_agent` stable; the pilot's methodology | the only design that can answer whether the Pi route pays | medium; needs a harness and hard budget limits |
| P2-2 | Read-only app-server diagnosis of thread/route state | official protocol; local socket absence | better `routePaused` diagnosis, no reliability promise | small; experimental surface |
| P2-3 | Track `agent_message_board`, `memories`, `worktrees` graduation | feature flags | if they mature, project the native object instead of keeping a parallel one | deferral, not work |
| P3 | State-machine model tests for phase/takeover transitions; module decomposition (`pi_phase.py` 81939, `pi_board.py` 77509 bytes) | runtime inventory | catches composite state bugs the 510 example tests miss | medium |

## 5. Paths deliberately rejected

Turning Codex-Pi into an MCP server (the in-repo community research already decided direct Python
plus a skill is sufficient and an extra server buys nothing). Using app-server, private IPC or
database writes as a wake mechanism (explicitly forbidden by this project's history, and unsupported
by the current desktop build). Storing business state in `memories`. Using `PreToolUse` to police a
Pi worker: Pi is an external process and its tool calls never reach these hooks. Continuing to chase
the next −20 % of documentation bytes, which the pilot suggests is worth single-digit percentage
points at best and can regress.

## 6. Next experiment worth paying for

P0-1 and P0-2 combined, judged afterwards by three frozen criteria: a real cli-queue round where main
sees pending review work without invoking `show`; a compaction whose summary still carries the
contract hash and candidate HEAD that `phase-status` can cross-check; and a negative control where
removing the additionalContext output fails the guard.
