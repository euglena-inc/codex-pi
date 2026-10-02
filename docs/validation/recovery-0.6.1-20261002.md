# 0.6.1 board and execution recovery

Sanitized record: identities, repository paths, provider sessions, product ledgers and raw transcripts remain in a private recovery archive. Public tests use synthetic repositories and identities.

## Failure and result

A shared JSON board approached its 256 KiB limit with 26 historical cards. Adding a terminal event failed even after history pruning. Two real rounds ended with final assistant `stopReason=error`, `Connection error`, while the Pi process returned 0; no formal receipts or new candidate commit were delivered.

The plugin now retains events, exact decisions, quota ledgers and claim history in its own transactional SQLite store. Task projections, record blobs, read windows and cursor pages are bounded; the aggregate history is not silently deleted and can grow on disk. Uncertain claims remain uncertain; replay of an old decision stays immutable and quality checkpoints survive projection changes. Legacy JSON is retained byte for byte during explicit writer-free conversion. Obsolete controls fail closed.

One terminal-evidence implementation distinguishes process exit from assistant completion. Errors, aborted/incomplete activity and an unverifiable tail cannot reuse an earlier answer, satisfy readiness or be classified as a quality delivery. Timeout, ownership and resource facts remain visible. Registration reports projection errors instead of returning silent success. Queue delivery rechecks pause, owner and exact unhandled events after claiming and before spawn.

Private read-only calibration against the real board/streams verified 26 owners, pauses and takeover states; 25 historical one-failure pins remain one. Both provider-error rounds retain original files/exit 0 and are effectively failed with zero quality failures. This is calibration, not product acceptance or a repair of the provider network.

## Verification

Behavioral tests cover near-limit conversion, retained pending pages and old decision replay, mixed pins, transaction rollback, interrupted publication/retry, unregistered writers, late pause, hundreds of queue tasks/claims, uncertainty and exact rearm, notification quotas, helper-generation integrity and a same-session dirty-worktree continuation.

Desktop queue receive/reply for the installed candidate: pending separate real validation. A script PASS or queue exit 0 is insufficient; installation and hook trust also require separate evidence.

## Restore a paused 0.6 task

1. Preserve source state and verify released task/supervisor locks and owned process groups; do not signal an unknown PID.
2. With the installed board CLI, `recover-store --repo REPO`; no worker is resumed and no round/helper/contract is rewritten. Inspect `show`, `events`, `decisions` and original archives.
3. Use `adopt-runtime --repo REPO --task TASK --dry-run` to check exact owner/session/worktree, pin, remaining budget and writer state. Actual adoption creates a separate hash-bound future runtime and leaves every original snapshot intact.
4. The owning main task resolves execution incidents, decides whether an authorized retry remains possible, explicitly resumes a pause, and continues the same task/session/worktree. An exhausted or unknown deadline requires an actual new user decision, not an automatic extension. No recovery step authorizes a product acceptance or another phase.

Use the installed control CLI after conversion, not the original JSON-based board helper. 0.5.x workers are not adopted. Provider connectivity, detached processes outside the owned group, disk exhaustion, app scheduling and human hook trust remain separate limits. Hooks must be reviewed in the desktop app; managed caches, trust hashes and the app queue database are never edited.
