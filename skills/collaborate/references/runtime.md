# Runtime commands

Usage only; rules [Skill](../SKILL.md); events [handoff](handoff.md); mechanisms/limits [IMPLEMENTATION.md](../../../runtime/IMPLEMENTATION.md). Expand `RUNTIME/` to `python3 <absolute plugin runtime dir>/` before running; never interpolate user text. One JSON line; Pi>=1.0.0; 0.5.x not migrated.

```sh
RUNTIME/pi_task.py project --repo REPO
RUNTIME/pi_task.py start --repo REPO --task TASK-1 --worktree WT --prompt-file BRIEF [--contract-file PHASE] [--read-only]
RUNTIME/pi_board.py register --repo REPO --task TASK-1 --thread OWNER_UUID --transport cli-queue --title T --goal G
```

Start, register immediately. Default `offline`; `cli-queue` needs local Codex CLI `queue`+desktop app+exact owner UUID (`--codex-bin` executable); never guess owner/start another process; `PI_BIN` selects Pi. Config `.agents/codex-pi.json`: schemaVersion/model/thinking/constraints/checks/maxWorkers/timeoutSeconds; task-lifetime isolated worktree; launch argv, worker config, guard rules (`forbidden-path`/`outside-worktree`/`undecidable`/`worker-blocks.jsonl`): IMPLEMENTATION.md.

```sh
RUNTIME/pi_task.py status --repo REPO --task TASK
RUNTIME/pi_task.py result --repo REPO --task TASK
RUNTIME/pi_board.py show --repo REPO --task TASK
RUNTIME/pi_board.py metrics --repo REPO [--task TASK | --outcome ID | --outcomes [--cursor ID]]
RUNTIME/pi_board.py record-outcome --repo REPO --outcome ID --record-file FILE [--expected-revision N]
```

`result`: compact terminal view; collect it once per terminal round; raw logs, receipts and the native session stay under `codex-pi/tasks/`; `show` adds the absolute evidence paths a card omits. `metrics --outcome` is the read-only six-section projection and `--outcomes` a bounded listing, never a denominator or success rate; a closeout is never acceptance, ownership or permission; Six-section limits, known/unknown coverage and the no-mutation guarantee: [detail](../../../docs/validation/outcome-observations-0.8.5.md).

```sh
RUNTIME/pi_task.py continue --repo REPO --task TASK --prompt-file FILE [--contract-file PHASE]
RUNTIME/pi_task.py cancel --repo REPO --task TASK
```

`continue` keeps pinned session/worktree in a new immutable round; a paused task refuses until resume. `cancel` signals only the owned worker group; verify it ended before repair.

```sh
RUNTIME/pi_task.py readiness --repo REPO --task TASK
RUNTIME/pi_task.py phase-status --repo REPO --task TASK
RUNTIME/pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision accept --reviewed-head FULL_SHA --phase PHASE --contract-hash HASH
```

A phase freezes with `--contract-file`; the PLAN stays authoritative and the contract is not a second PLAN. Call `check` with the exact case-sensitive `checkId`. Readiness consumes current-round exact-candidate receipts only; missing/failed/skipped/unknown is never ready; there is no cross-round or cross-head PASS reuse and no cached PASS, so select commands at freeze time and cite applicable component evidence in review; a formal command may be a bounded component check and its `targetedCommand` never covers it. `accept` binds phase/contract/candidate/live state and refuses any mismatch; a command timeout may not exceed `commandTimeoutSeconds`; declared `resourceLimits` stop only the owned group on a known breach and an incomplete measurement stays unknown.

```sh
RUNTIME/pi_board.py recover-store --repo REPO
RUNTIME/pi_board.py show --repo REPO --all [--cursor LAST_TASK]
RUNTIME/pi_board.py events --repo REPO --task TASK [--cursor LAST_SEQ] [--pending]
RUNTIME/pi_board.py decisions --repo REPO --task TASK [--cursor LAST_EVENT_ID]
RUNTIME/pi_task.py adopt-runtime --repo REPO --task TASK [--dry-run]
RUNTIME/pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision changes_requested --failure-kind quality --note N
```

Installed CLI; conversion needs released writers, keeps original JSON/events/decisions/claims; adoption rules and 0.5 limits: [recovery](../../../docs/validation/recovery-0.6.1-20261002.md); `execution_failed` is separate from quality review and an exit-zero error is never accepted; `--failure-kind external` needs a note naming the unlock condition; [recovery](../../../docs/validation/recovery-0.6.1-20261002.md).

```sh
RUNTIME/pi_board.py takeover --repo REPO --task TASK --event-id EVENT --reviewed-head FULL_SHA --note N
```

Frozen early-takeover only; latest delivery/incident event+contract+HEAD agree; leases+recorded groups released; active/unknown/stale/accepted/conflicting refuse; replay idempotent; `codex_takeover_required` carries the ownership receipt for `decide resolve`; takeover never accepts nor fabricates a rejection; resume cannot return it to Pi.

```sh
RUNTIME/pi_board.py pause --repo REPO --task TASK [--note N]
RUNTIME/pi_board.py resume --repo REPO --task TASK [--thread OWNER_UUID]
RUNTIME/pi_board.py recover [--thread OWNER_UUID]
RUNTIME/pi_board.py rearm --repo REPO --task TASK [--event-id EVENT]
RUNTIME/pi_board.py refresh --repo REPO --task TASK
RUNTIME/pi_task.py progress --repo REPO --task TASK --activity A --step S [--show]
```

`pause` stops dispatch, not Pi; only the owner resumes explicitly; `recover` reads bounded route evidence and `rearm` may duplicate a send (no idempotency key). `refresh` is the local projection the supervisor calls; `progress` is Pi's own self-report tool, never a main-session decision.

Simple calls direct; batching/branching/filtering in one script; nested `tools.*` keep the same guard. Await every call and inspect structured failures (a fulfilled promise≠success); parallel checks need independent resources+unique ids; dependent writes/commits serialize; `Promise.allSettled` only for declared independent checks; never change `maxWorkers` to speed local checks. `checkExecution`: maxConcurrent 1..4, cpuSlots, memoryMiB; `checkResources`: parallelSafe, cpuSlots, memoryMiB, exclusiveKeys; default serial; parallel needs complete estimates+pool; metadata follows normalized argv and conflicts refuse; shared resources stay exclusive; measure 1/2 lanes before 4; bounds are soft per-worker, not host-wide (admission mechanics: IMPLEMENTATION.md).

`network`=exactly `proxyUrl`+`diagnostics`; `proxyUrl`=credential-free `http(s)://host:port`, root path, no query/fragment, validated before side effects and never echoed back; omission keeps inherited behavior, no auto discovery/retry/route switch; `start` freezes it, `continue` applies only it, key-less tasks keep inherited behavior+helpers. Diagnostics append one `--import` plus sidecar variables; only the direct Pi child writes bounded redacted JSONL under a 64 KiB cap (mechanism and classification: IMPLEMENTATION.md). An unreadable or missing sidecar is unknown, never healthy. Pre-TLS reset→transport; CONNECT 503→proxy tunnel failure; validate or switch the user's own route; never promise permanent repair.
