# Runtime commands

Usage only; rules [Skill](../SKILL.md); events [handoff](handoff.md); rarely-needed store repair, conversion, adoption and network triage [operations](runtime-ops.md); mechanisms/limits [IMPLEMENTATION.md](../../../runtime/IMPLEMENTATION.md). Expand `RUNTIME/` to `python3 <absolute plugin runtime dir>/` before running; never interpolate user text. One JSON line; Pi>=1.0.0; 0.5.x not migrated.

```sh
RUNTIME/pi_task.py project --repo REPO
RUNTIME/pi_task.py start --repo REPO --task TASK-1 --worktree WT --prompt-file BRIEF [--contract-file PHASE] [--read-only]
RUNTIME/pi_board.py register --repo REPO --task TASK-1 --thread OWNER_UUID --transport cli-queue --title T --goal G
```

Start, register immediately. Default `offline`; `cli-queue` needs local Codex CLI `queue`+desktop app+exact owner UUID (`--codex-bin` executable); never guess owner/start another process; `PI_BIN` selects Pi. Config `.agents/codex-pi.json`: schemaVersion/model/thinking/constraints/checks/maxWorkers/timeoutSeconds; task-lifetime isolated worktree; launch argv, worker config and guard rules: IMPLEMENTATION.md.

```sh
RUNTIME/pi_task.py status --repo REPO --task TASK
RUNTIME/pi_task.py result --repo REPO --task TASK
RUNTIME/pi_board.py show --repo REPO --task TASK
RUNTIME/pi_board.py metrics --repo REPO [--task TASK | --outcome ID | --outcomes [--cursor ID]]
RUNTIME/pi_board.py record-outcome --repo REPO --outcome ID --record-file FILE [--expected-revision N]
```

`result`: compact terminal view; collect it once per terminal round; raw logs, receipts and the native session stay under `codex-pi/tasks/`; `show` adds the absolute evidence paths a card omits. `metrics`/`record-outcome` are read-only closeout views, never acceptance, ownership, permission or a denominator: [operations](runtime-ops.md).

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
RUNTIME/pi_board.py show --repo REPO --all [--cursor LAST_TASK]
RUNTIME/pi_board.py events --repo REPO --task TASK [--cursor LAST_SEQ] [--pending]
RUNTIME/pi_board.py decisions --repo REPO --task TASK [--cursor LAST_EVENT_ID]
RUNTIME/pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision changes_requested --failure-kind quality --note N
```

`execution_failed` is separate from quality review and an exit-zero error is never accepted; `--failure-kind external` needs a note naming the unlock condition. Store repair, 0.6 conversion and runtime adoption commands: [operations](runtime-ops.md).

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

Simple calls direct; batching/branching/filtering in one script; nested `tools.*` keep the same guard. Await every call and inspect structured failures (a fulfilled promise≠success); parallel checks need independent resources+unique ids; dependent writes/commits serialize; `Promise.allSettled` only for declared independent checks; never change `maxWorkers` to speed local checks. Admission fields, serial default and lane measurement: [operations](runtime-ops.md).

`network`=exactly `proxyUrl`+`diagnostics`: credential-free, validated before side effects, never echoed back; omitted keeps inherited behavior with no auto discovery, retry or route switch; an unreadable or missing diagnostic sidecar is unknown, never healthy. Value rules, triage and the redacted-JSONL cap: [operations](runtime-ops.md).
