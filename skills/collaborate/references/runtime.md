# Runtime commands

Usage only; rules [Skill](../SKILL.md); events [handoff](handoff.md); mechanisms/limits [IMPLEMENTATION.md](../../../runtime/IMPLEMENTATION.md). Never interpolate user text; one JSON line; Pi>=1.0.0; 0.5.x not migrated.

```sh
python3 RUNTIME/pi_task.py project --repo REPO
python3 RUNTIME/pi_task.py start --repo REPO --task TASK-1 --worktree WT --prompt-file BRIEF [--contract-file PHASE] [--read-only]
python3 RUNTIME/pi_board.py register --repo REPO --task TASK-1 --thread OWNER_UUID --transport cli-queue --title T --goal G
```

Start, register immediately. Default `offline`; `cli-queue` needs local Codex CLI `queue`+desktop app+exact owner UUID (`--codex-bin` executable); never guess owner/start another process; `PI_BIN` selects Pi. Config `.agents/codex-pi.json`: schemaVersion/model/thinking/constraints/checks/maxWorkers/timeoutSeconds; task-lifetime isolated worktree; launch argv, worker config, guard rules (`forbidden-path`/`outside-worktree`/`undecidable`/`worker-blocks.jsonl`): IMPLEMENTATION.md.

```sh
python3 .../pi_task.py status --repo REPO --task TASK
python3 .../pi_task.py result --repo REPO --task TASK
python3 .../pi_board.py show --repo REPO --task TASK
python3 .../pi_board.py metrics --repo REPO [--task TASK | --outcome ID | --outcomes [--cursor ID]]
python3 .../pi_board.py record-outcome --repo REPO --outcome ID --record-file FILE [--expected-revision N]
```

`result`: terminal view; collect once per terminal round; logs/receipts/session under `codex-pi/tasks/`; `show` adds absolute evidence paths. `record-outcome` semantics: [detail](../../../docs/validation/outcome-observations-0.8.5.md). `metrics --outcome`=read-only six-section projection; `--outcomes`=bounded listing, never denominator/success rate; closeout never acceptance/ownership/permission; Pi-native vs Main-reported separate; unknown≠zero; intervals use unions; interface/SDK cost≠billing; no record/read command scans a session or changes execution/decisions/budgets/pause/quality/notifications.

```sh
python3 .../pi_task.py continue --repo REPO --task TASK --prompt-file FILE [--contract-file PHASE]
python3 .../pi_task.py cancel --repo REPO --task TASK
```

`continue` keeps pinned session/worktree in a new immutable round; a paused task refuses until resume. `cancel` signals only the owned worker group; verify it ended before repair.

```sh
python3 .../pi_task.py readiness --repo REPO --task TASK
python3 .../pi_task.py phase-status --repo REPO --task TASK
python3 .../pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision accept --reviewed-head FULL_SHA --phase PHASE --contract-hash HASH
```

A phase freezes with `--contract-file`; PLAN stays authoritative, not a second PLAN. `check` ids exact, case-sensitive; progress=self-report never acceptance, ordinary progress stays on the board; sustained unrepaired failure may enqueue max twice/phase. Readiness: current-round exact-candidate receipts only; missing/failed/skipped/unknown never ready; no cross-round/head reuse; no PASS cached; a formal command may be a bounded component check; select commands at freeze; cite component evidence in review. `accept` binds phase/contract/candidate/live state; timeout≤`commandTimeoutSeconds`; `resourceLimits` stop only the owned group on known breach; incomplete measurement unknown.

```sh
python3 .../pi_board.py recover-store --repo REPO
python3 .../pi_board.py show --repo REPO --all [--cursor LAST_TASK]
python3 .../pi_board.py events --repo REPO --task TASK [--cursor LAST_SEQ] [--pending]
python3 .../pi_board.py decisions --repo REPO --task TASK [--cursor LAST_EVENT_ID]
python3 .../pi_task.py adopt-runtime --repo REPO --task TASK [--dry-run]
python3 .../pi_board.py decide --repo REPO --task TASK --event-id EVENT --decision changes_requested --failure-kind quality --note N
```

Installed CLI; conversion needs released writers, keeps original JSON/events/decisions/claims; adoption=separate immutable future runtime, never edits old tools/resumes/changes model/extends budget; 0.5 not adopted; `execution_failed` separate from quality review, exit-zero cannot be accepted; `--failure-kind external` needs a note naming the unlock condition; [recovery](../../../docs/validation/recovery-0.6.1-20261002.md).

```sh
python3 .../pi_board.py takeover --repo REPO --task TASK --event-id EVENT --reviewed-head FULL_SHA --note N
```

Frozen early-takeover only; latest delivery/incident event+contract+HEAD agree; leases+recorded groups released; active/unknown/stale/accepted/conflicting refuse; replay idempotent; `codex_takeover_required` carries the ownership receipt for `decide resolve`; takeover never accepts nor fabricates a rejection; resume cannot return it to Pi.

Simple calls direct; batching/branching/filtering in one script; nested `tools.*` keep the guard. Await all calls; inspect structured failures (promise≠success); parallel checks need independent resources+unique ids; dependent writes/commits serialize; `Promise.allSettled` only for declared independent checks; no `maxWorkers` change for speed. `checkExecution`: maxConcurrent 1..4, cpuSlots, memoryMiB; `checkResources`: parallelSafe, cpuSlots, memoryMiB, exclusiveKeys; default serial; parallel needs estimates+pool; argv-normalized metadata; conflicts refuse; count child threads/memory; shared resources exclusive; pressure/incomplete conservative; 1/2 lanes before 4; CPU/memory soft per-worker not host-wide.

`network`=exactly `proxyUrl`+`diagnostics`; `proxyUrl`=credential-free `http(s)://host:port`, root path, no query/fragment; validated before side effects; rejected never echoed; omission=inherited behavior; no auto discovery/retry/switch; `start` freezes, `continue` applies only it; key-less tasks keep inherited behavior+helpers. Diagnostics: one `--import`+three sidecars; only the direct Pi child writes bounded redacted JSONL (one writer, 64 KiB cap); inherited children never append (mechanics: IMPLEMENTATION.md); unreadable/missing=unknown not healthy; pre-TLS reset→transport; CONNECT 503→proxy tunnel failure; validate/switch the route; never promise permanent repair.
