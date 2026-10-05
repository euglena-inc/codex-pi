# Hook presence and session closeout (P0-1 + P0-2)

Status: accepted for implementation by the main session on 2026-10-05, after
[codex-capability-paths.md](codex-capability-paths.md). Owner: main session (the board for
`DOC-BUDGET-20261004` is under a main takeover latch and this work continues that same ownership;
no second dispatch is opened for it).

## 1. Goal

Move two things out of the model's memory and into the harness, with no extra model turn:

1. **Presence** — at every `SessionStart` and `UserPromptSubmit` boundary the main session already
   pays for a hook. Make that hook state, in bounded text, which Codex-Pi work is *awaiting a main
   decision*, plus the pins of every task bound to this thread. Today the hook reports transport
   anomalies only, so a healthy review-ready round produces nothing and main must remember to poll.
2. **Continuity and closeout** — a `PreCompact` hook carries the pins into the compaction summary,
   and a `SessionEnd` hook prepares the outcome closeout for the tasks touched in that session, so
   the closeout is started by the harness instead of remembered by a human.

## 2. Non-goals

No delivery channel change: `codex queue` stays the only delivery path and this design never calls
the Codex CLI, never opens a daemon, never touches hook trust state or plugin caches. No acceptance,
no ownership change, no new status values. No `PostToolUse` hook (see
[codex-capability-paths.md](codex-capability-paths.md) §2.1). No measurement of loaded bytes yet
(P1-3) and no `codex review` check (P0-3).

## 3. Surfaces

New module `runtime/pi_presence.py`, the single home for the digest:

| Function | Contract |
| --- | --- |
| `MAX_CONTEXT_BYTES = 1600` | self-bound for one hook output; the host may apply a lower `additionalContextLimit`, so a truncated digest always keeps its marker |
| `presence_for_thread(thread, limit)` | bounded lines for one owner thread: pending decision events, per-task pins, merged transport anomalies; `{"lines": [...], "truncated": bool, "problem": str|None}` |
| `task_pin(card)` | one pin line: state, phase id, 12-hex contract hash or `unknown`, 12-hex candidate head or `unknown`, `owner`, `takeover`, `failedDeliveries` with `unknown` kept as `unknown` |
| `compact_digest(thread)` | pins for every task bound to the thread plus the transport recovery lines already produced by `pi_board.route_summary`, de-duplicated |
| `prepare_closeout(thread, repo=None, now=None, apply=False)` | writes one redacted closeout file per session, returns its path plus the exact `record-outcome` command that would file it; `apply=True` additionally files it |

Extended module `runtime/pi_handoff.py`: `dispatch_hook` gains `PreCompact` and `SessionEnd`, and the
`SessionStart`/`UserPromptSubmit` path now returns the presence digest instead of transport lines
alone. The module still never imports the Codex CLI and never starts a model.

New subcommand `pi_board.py closeout --repo REPO --session SESSION [--task TASK] [--apply]`:
`prepare` is the default and is read-only apart from its own closeout file; `--apply` files the
observation through the existing `pi_outcome` code path. It is documented in
`skills/collaborate/references/runtime-ops.md`, the on-demand tier, because a session normally
prepares it without help.

`hooks/hooks.json` registers five events, all pointing at the same entry point with a 3 s timeout:
`SessionStart`, `UserPromptSubmit`, `Interrupt`, `PreCompact`, `SessionEnd`.

## 4. Digest content and byte budget

Order is fixed so a truncation always loses the least useful line last:

1. `awaiting-main decision: task=… event=review_required round=… head=…` for every unhandled event of
   kind `review_required`, `phase_blocked` or `codex_takeover_required`, capped at 6 lines;
2. one pin line per task bound to the thread, capped at 6 tasks;
3. transport anomaly and pause lines reused from `pi_board.route_summary`;
4. footer `read-only presence; never acceptance; unknown is not zero`.

Header `Codex-Pi presence (read-only):`. Anything the store cannot answer is rendered `unknown`;
an unreadable store renders one `board state is <problem>` line and no invented values. A digest that
exceeds `MAX_CONTEXT_BYTES` is cut at a line boundary, gains `…(+N more)`, and reports
`truncated: true`; the footer survives because it is written last inside the budget.

## 5. Event contracts

| Event | Output | Writes | Never |
| --- | --- | --- | --- |
| `SessionStart`, `UserPromptSubmit` | `hookSpecificOutput.additionalContext` = presence digest, or `{}` when nothing is bound or pending | none | block, approve, rewrite, or start work |
| `PreCompact` | `hookSpecificOutput.additionalContext` = compact digest with explicit pins | none | block compaction |
| `SessionEnd` | `{"systemMessage": "codex-pi closeout prepared: <path>; not acceptance"}` | the closeout file | block the session end, file an observation without `--apply` |
| `Interrupt` | unchanged: persist the route pause | route pause record | resume anything |

`SessionEnd` output uses `systemMessage`, not `additionalContext`, because no further model turn
exists at that point.

## 6. Closeout file

`<repo>/.git/codex-pi/sessions/<sanitized-session>.closeout.json`, revision-protected by an
`updatedAt` compare-and-skip inside the same second, 0600 where the platform allows, and never
containing a prompt, a diff, a credential or an absolute path of another user's home:

```
{"schemaVersion": 1, "session": "<id>", "generatedAt": <epoch>,
 "outcome": {"status": "unknown", "candidate": "<head or null>",
             "members": [{"task": "…", "rounds": [1]}], "evidenceRefs": ["<repo-relative paths>"],
             "workComplete": false, "note": "prepared by the SessionEnd hook; never acceptance"},
 "recordCommand": "python3 <runtime>/pi_board.py record-outcome --repo <repo> --outcome <id> --record-file <path>"}
```

Rules: `status` stays `unknown` unless a reviewed acceptance decision exists for the task, in which
case it is `accepted` and the candidate is the reviewed head; `members[].rounds` lists only rounds the
board actually recorded, and an absent round index yields `"rounds": []` with
`"coverage": "unknown"`, never a guessed `[1]`; `workComplete` is always `false` in a prepared record,
because absence of work records is not proof of no intervention; the outcome id is
`PI-PRESENCE-<session prefix>` unless the task already has an outcome binding, so a preparation can
never overwrite a hand-authored record. `--apply` runs the same validator the manual command runs, so
an invalid preparation fails instead of writing a bad record.

## 7. Invariants that must not move

Unknown is never replaced by zero. A hook never accepts, decides, owns or permits. Hook code never
invokes the Codex CLI, never starts a model or daemon, and never edits hook trust or plugin caches.
Every hook path is read-only except `Interrupt` (route pause) and `SessionEnd` (its own closeout
file). Every hook path is bounded and must not raise: an internal failure yields a `systemMessage`
and exit 0, so a broken digest can never block a session. Redaction: contract hashes and heads are
truncated to 12 hex characters, task ids and phase ids pass the existing identifier sanitizer, and
paths are rendered repo-relative.

## 8. TDD matrix (all offline, deterministic, no model, no network, no skip)

| Test | Behaviour proven |
| --- | --- |
| `test_presence_line_lists_every_event_awaiting_a_decision` | a review-ready healthy card appears at the boundary without any polling |
| `test_pin_line_keeps_unknown_and_never_zero` | missing contract/candidate/round index render `unknown`, never `0` or `unknown→0` |
| `test_digest_is_bounded_and_marks_truncation` | 40 pending events still yield ≤ `MAX_CONTEXT_BYTES`, a `(+N more)` marker and `truncated: true` |
| `test_digest_reuses_transport_lines_without_duplication` | route pause appears once even though `route_summary` also emits it |
| `test_corrupt_store_degrades_to_one_problem_line` | unreadable store yields one line, no invented state, exit 0 |
| `test_hooks_registered_for_five_events_with_bounded_timeout` | `hooks/hooks.json` registers the five events, one command, timeout ≤ 5 s |
| `test_dispatch_returns_additional_context_for_session_and_prompt` | the documented output shape for `SessionStart`/`UserPromptSubmit` |
| `test_precompact_digest_carries_contract_and_candidate_pins` | the compaction summary text contains the exact 12-hex pins |
| `test_session_end_prepares_without_filing` | file created, `status: unknown`, `workComplete: false`, no outcome revision written |
| `test_session_end_apply_files_the_same_record` | `--apply` produces the identical payload the printed command would file |
| `test_prepared_record_rejects_a_fabricated_status` | forcing `status: accepted` without a reviewed decision fails validation |
| `test_hook_never_calls_codex_or_starts_a_process` | with a poisoned `PATH` and a process spy, no subprocess is spawned on any of the five paths |
| `test_closeout_is_idempotent_and_never_overwrites_a_manual_record` | second run is idempotent; a hand-authored outcome revision is untouched |
| `test_presence_runs_under_the_hook_budget` | wall time of a 6-task digest over a real store < 1.5 s |

Negative controls are part of the matrix: the guard must fail if the `additionalContext` key is
removed, if `unknown` becomes `0`, or if the closeout starts filing by default.

## 9. Verification ladder

Focused: `tests/test_presence.py`, `tests/test_handoff.py`, `tests/test_board.py`, `tests/test_docs.py`,
`tests/test_modules.py`. Then the full Python suite, because this changes runtime files. Then
`python3 scripts/check_public_privacy.py`. Byte budget stays enforced by `test_docs`.

Real behaviour that unit tests cannot prove and that needs the user's app: hook trust for the new
event definitions, one actual `SessionStart` showing a pending line in a live session, and one actual
compaction carrying the pins. Those two observations are reported as user-verified or as unknown,
never inferred from the offline suite.

## 10. Rollback

`hooks/hooks.json` is the switch: removing `PreCompact`/`SessionEnd` returns the previous behaviour
with no state migration, and `pi_presence.py` has no other caller. The closeout files are additive
artifacts under the repo-local private directory.
