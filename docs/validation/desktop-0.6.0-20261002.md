# 0.6.0 desktop delivery check (2026-10-02)

> Sanitized: identifiers below are placeholders. Raw logs, receipts and the desktop transcript stay in a private archive.

Runtime: source `main` at the 0.6.0 merge. Pi 1.0.0, `deepseek/deepseek-flash`. PATH `codex-cli 0.157.1`. Synthetic repository with a main checkout and a task worktree. Owner: a new, empty desktop task created by the user for this check (`00000000-0000-4000-8000-0000000000a1`).

1. `start` then `register --transport cli-queue`. Pi wrote and committed one file in its worktree and recorded `check ok exit=0`. Round `completed`.
2. Nothing was delivered. The owner thread had been interrupted 40 s before registration, so its route carried an `interrupt` pause and `dispatch_task` correctly returned "session paused by interrupt". Registration did not report this, which led to the fix in item 5.
3. With the user's authorization: `resume --thread`, then `register` again. The queue command exited 0 (`Queued message … for thread …`). The card was 721 bytes, and `codex-io.jsonl` recorded `kind=packet bytes=721`.
4. The desktop queue held the item while the interrupted thread was not open. When the user opened the thread, the app consumed the item and started a new turn automatically, and the main model replied to the card. The queue row was gone afterwards.
5. Fix: `register` now returns `routePaused` and a `warning` with the exact `resume` command when the owner thread is paused (`test_register_on_paused_thread_warns_and_does_not_deliver`).

Limits:
- One idle owner thread after an aborted turn. Delivery waited for the thread to be opened.
- The busy-boundary and idle-arrival paths were validated on 2026-09-26 ([cli-queue](cli-queue-20260926.md)) and were not repeated.
- The installed plugin (0.5.3 hooks) was unchanged during this check.
