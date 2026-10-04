# Event handling and recovery

Rules [Skill](../SKILL.md); commands [runtime](runtime.md). Supervisor refreshes locally (~15s); actionable events become one ≤1200-byte card (ids, kind, full SHA, item line, owner, report, hint); idle tasks resume, busy ones handle after their turn.

Read card ids; reuse a collected `result` for that round; verify diff+original receipts; then `changes_requested`=findings, `reject`=reject, `resolve`=non-review incident/anomaly echo, `accept`=full real candidate. An old-round decision never accepts the current; `+N pending event(s)`→`show` and handle all this turn; repeated delivery=dedup receipt; breach/deadline→bounded question, never endless retry.

Eligible new tasks: early ownership handoff via `pi_board.py takeover` with current event, full candidate, reason; it rechecks released writers/contract/HEAD, preserves real counts, emits the same takeover event resolved as an ownership receipt; independent acceptance required.

Pause stops dispatch, not Pi; prompts/progress never resume it; a queued card never overrides a later pause. An uncertain send may have arrived: keep the claim, inspect, recover only if needed; `rearm` may duplicate (no queue idempotency key), never a live inflight send, never steal locks; hooks check only pause/recovery; a dead supervisor cannot report itself.

Install via the formal mechanism only (hook review is the user's app action); a running task keeps its frozen helpers and 0.5.x tasks finish with them; registering a terminal task may enqueue a review event at once.

Unit tests don't prove desktop delivery: one real tiny bounded Pi task (fixed bytes, ten-second check, commit, exit; ≤3 min) must show completion, supervisor, same desktop task, visible reply; `register` reports `routePaused` with exact `resume` command; an interrupted idle desktop task keeps its card until reopened. [cli-queue](../../../docs/validation/cli-queue-20260926.md), [desktop](../../../docs/validation/desktop-0.6.0-20261002.md).
