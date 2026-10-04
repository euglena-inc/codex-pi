# Runtime operations (on demand)

Load this file only when one of its conditions applies; an ordinary dispatch, check or review round never needs it. Rules stay in [Skill](../SKILL.md), everyday usage in [runtime](runtime.md), mechanisms in [IMPLEMENTATION.md](../../../runtime/IMPLEMENTATION.md). Same expansion: `RUNTIME/` = `python3 <absolute plugin runtime dir>/`, never interpolate user text.

Load when:

- a task store failed, needs opening in a converted form, or a pre-0.6 task must be adopted into the current runtime;
- a round failed before Pi could report anything and the cause looks like proxy, transport or route loss rather than a work result;
- a contract declares or changes parallel check resources, or a check was refused as resource-conflicting;
- an outcome is being closed out, recorded, or compared against another outcome.

## Outcome observations and closeout

`record-outcome` stores a Main-reported observation of an outcome at `--expected-revision`, appending a revision rather than editing a past one. `metrics --outcome` renders the six sections read-only and `--outcomes` lists them bounded; neither is a denominator, a success rate, an acceptance, an ownership decision or permission to execute. Pi-native facts and Main-reported facts stay in separate fields, unknown is never replaced by zero, timing intervals are unions rather than sums, and a reported interface cost is not a billing figure. No record or read command scans a session or changes execution, decisions, budgets, pause or quality counts ([detail](../../../docs/validation/outcome-observations-0.8.5.md)).

## Check resource admission

`checkExecution`: `maxConcurrent` 1..4, `cpuSlots`, `memoryMiB`. `checkResources` per command: `parallelSafe`, `cpuSlots`, `memoryMiB`, `exclusiveKeys`. The default is serial; a parallel declaration needs complete estimates plus a pool, metadata follows the normalized argv, and a conflict refuses the run. Shared resources stay exclusive, bounds are soft per-worker and never host-wide, and measuring 1 and 2 lanes before 4 is the expected order (admission mechanics: [IMPLEMENTATION.md](../../../runtime/IMPLEMENTATION.md)).

## Store repair, 0.6 conversion and runtime adoption

```sh
RUNTIME/pi_board.py recover-store --repo REPO
RUNTIME/pi_task.py adopt-runtime --repo REPO --task TASK [--dry-run]
```

`recover-store` repays a lost write journal from the retained revision history before any other command touches the store. Installed CLI only; conversion requires released writers and keeps the original JSON, events, decisions and one-writer claims; it never rewrites history to look healthier. `adopt-runtime` moves a still-open task onto a newer released runtime as a separate immutable choice: it never edits the old tooling, resumes a frozen session, changes the pinned model or extends an existing budget; `--dry-run` reports the effect without committing. The 0.5 series was never adopted, and 0.5.x tasks finish with the helpers they started on ([recovery evidence](../../../docs/validation/recovery-0.6.1-20261002.md)).

## Network policy and failure classification

`network` contains exactly `proxyUrl` plus `diagnostics`. `proxyUrl` is a credential-free `http(s)://host:port` with a root path and no query or fragment; it is validated before any side effect and never echoed back. Omitting it keeps inherited behavior: no automatic discovery, no retry, no route switching. `start` freezes the value; `continue` applies only it; a key-less task keeps inherited behavior and helpers.

Diagnostics append one `--import` plus sidecar variables. Only the direct Pi child writes bounded redacted JSONL under a 64 KiB cap; the mechanism, environment switch and classification live in [IMPLEMENTATION.md](../../../runtime/IMPLEMENTATION.md). An unreadable or missing sidecar is unknown, never healthy.

Reading a round that died early: a reset before TLS handshake is transport loss; a `CONNECT` 503 is proxy tunnel failure; both mean validate or switch the user's own route. Never promise permanent repair, and never claim a network cause the retained evidence does not show.
