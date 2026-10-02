# Comparing Codex-Pi and Sol-Luna

Compare complete accepted outcomes under the same goal, baseline and acceptance.
Use separate clean worktrees with the same starting commit. Parallel execution
is useful only with independent shared resources; otherwise run sequentially.
Do not merge one candidate into the other, give one worker the other's answer,
or let one route's failures change only the other's acceptance. Include planning,
review, repair, integration and takeover in each run's measured window. If both
routes share a Sol turn, its usage cannot be assigned exactly: record Sol usage
unknown for the runs unless a reliable per-request attribution exists. Separate
owner chats, explicitly requested by the user, or separate attributable turns
are preferable for measurement. A textual side-by-side comparison does not
require running either route.

Record case/pair, baseline, acceptance key, route, candidate, outcome, model
verification, exclusive Sol usage, worker usage/cost, elapsed time, quality
rejections and takeover in private evidence. The acceptance key names the same
fixed acceptance contract; it is not a new admission gate. Freeze model/effort
settings and report them with results. Keep real session identities and raw logs
outside this public repository.

`modelVerified: true` means evidence covers the intended parent and worker models
AND efforts throughout the measured window (Sol 6.1 medium / Luna max for this
route), with any service rerouting accounted for. A successful spawn request or
the current configured model alone is insufficient. Keep settings/provenance in
the private evidence; the comparator trusts, rather than verifies, this declaration.

`scripts/compare_routes.py` uses normalized per-run records, not raw sessions.
It computes Sol-token changes only for complete, accepted, model-verified,
exclusive measurements. It does not prove those declarations or collect usage.
For repeated measurements use a different pair per repetition. Failed, blocked,
incomplete and unverifiable runs remain visible without a savings claim.

Minimal record (synthetic values; both routes need their own record):

```json
{
  "schemaVersion": 1,
  "project": "synthetic-project-a",
  "pair": "trial-1",
  "case": "ordered-dedup",
  "baseline": "1111111111111111111111111111111111111111",
  "acceptanceKey": "ordered-dedup-v1",
  "route": "sol-luna",
  "candidate": "2222222222222222222222222222222222222222",
  "outcome": "accepted",
  "modelVerified": true,
  "solUsage": {
    "complete": true,
    "basis": "exclusive-requests",
    "inputTokens": 1000,
    "cachedInputTokens": 500,
    "outputTokens": 100
  }
}
```

Unknown `solUsage` is `null` or has `complete: false`; never fill missing fields
with zero. Input includes cached input. Output must use the runtime's output
counter with its documented semantics; do not add reasoning output again. Record
reasoning separately in private evidence if supplied. Report input,
uncached input and output separately, plus their input+output total. Missing cache
usage does not prevent a known total but prevents an uncached-input comparison.

Collect native `thread/tokenUsage/updated` events when the client exposes them,
using before/after cumulative counters rather than summing cumulative updates.
Verify reset, compaction, parent/child aggregation and model-rerouting semantics
before calling the data exclusive and complete. Do not infer Sol usage from
thread length, account-wide limit percentages or Pi's `Codex-facing bytes`.
Pi's existing `pi_board.py metrics` supplies worker metrics; Luna's native usage
must be separately observed. The current chat's collaboration tools do not expose
per-agent token counters, so a native smoke alone leaves token savings unknown.

```sh
python3 /abs/plugin/scripts/compare_routes.py /abs/private/pi-run.json /abs/private/luna-run.json
```

Each output line describes one matching project/pair/case/baseline/acceptance key.
Duplicate route records in a group are rejected rather than overwritten. Inputs
are all validated before output; errors identify the input number without echoing
raw content or paths. Duplicate JSON keys are rejected at any nesting; supplied
token counters must be signed-int64 nonnegative integers, not booleans. Each file
can contain one record or an array. Additional private evidence fields are not printed.
Savings are relative to Codex-Pi:
`100 * (pi - luna) / pi`, with a negative value meaning increased Sol usage.
Nonmatching baselines/contracts are separate incomplete pairs, never compared.
Do not report a percentage for an unknown or zero baseline. Total expenses, Codex
credits and speed need their own measured worker usage, actual billing rules and
resource controls; fewer Sol tokens does not establish a cheaper or faster result.

Native sources checked on 2026-10-02:

- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents):
  native delegation, model settings and continuation; multiple agents can increase
  total tokens. This plugin does not install custom agent TOML into global config.
- [Config reference](https://learn.chatgpt.com/docs/config-file/config-reference):
  optional subagent model/effort defaults; explicit route selection remains needed.
- [App-server](https://learn.chatgpt.com/docs/app-server): version-specific schema
  generation and token events. An installed CLI's catalog can differ from desktop.
- [Models](https://learn.chatgpt.com/docs/models): model availability and effort
  vary by client/account. Prompts and API prices are not proof of Codex usage.
