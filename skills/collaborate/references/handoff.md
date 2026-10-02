# Event handling and recovery

Rules are in [the Skill](../SKILL.md); commands in [runtime](runtime.md). The supervisor refreshes the board about every 15 seconds and queues only actionable events as a delivery card (at most 1200 bytes: task, round, event id, kind, full candidate SHA, one line per acceptance item, `failed/limit owner`, Pi's truncated final report, one hint). An idle desktop task resumes; a busy one handles the card after its turn.

## Handle one event

Read the card's task, round and event id, reuse a `result` already collected for that round, verify the diff and original receipts, then decide:

```sh
python3 /abs/plugin/runtime/pi_board.py decide --repo /abs/repo --task TASK-1 --event-id EVENT_ID --decision accept --reviewed-head FULL_SHA --note 'Verified checks and review evidence'
```

`changes_requested` carries concrete findings; `reject` rejects the candidate; `resolve` closes a non-review incident or an anomaly echo. Acceptance needs the full real candidate commit; a decision on an old round never accepts the current one. If a card shows `+N pending event(s)`, run `show` and handle the rest in the same turn. A repeated delivered event is a receipt to deduplicate. A resource breach or deadline gets a bounded question and evidence, never an endless retry.

## Pause and uncertain delivery

```sh
python3 .../pi_board.py pause --repo REPO --task TASK --note 'User paused handoff'
python3 .../pi_board.py resume --repo REPO --task TASK --thread OWNER_UUID
python3 .../pi_board.py recover --thread OWNER_UUID
python3 .../pi_board.py rearm --repo REPO --task TASK --event-id EVENT
```

A pause stops dispatch, not Pi; ordinary prompts and progress questions never resume it. A queued card does not override a later pause. A delivery that timed out or crashed may already have arrived: keep the uncertain claim, inspect, and use explicit recovery only if needed. `rearm` may duplicate it (the queue has no idempotency key); never rearm a live inflight send, steal locks or claim exactly-once. Hooks only check pause and recovery. A dead supervisor cannot report itself.

## Updates

Install through the formal plugin mechanism; never edit managed caches, hook trust or app queue databases (hook review is the user's action in the app). A running task keeps its frozen helpers; a task from 0.5.x is finished with those helpers, not migrated. Registering a terminal task may enqueue a review event at once.

## Real desktop validation

Unit tests do not prove desktop delivery. Use one real tiny Pi task (write fixed bytes to one file, run a ten-second check, commit that file, exit; bound the round to three minutes) and observe completion, supervisor, the same desktop task and a visible reply. History: [CLI queue validation](../../../docs/validation/cli-queue-20260926.md).
