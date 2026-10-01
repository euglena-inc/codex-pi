# Public repository privacy

The public repository distributes plugin code, synthetic fixtures and sanitized documentation. Raw validation JSON/logs, private conversations, business-project status, real task/session identifiers and local credentials must remain in private storage. Original development evidence is retained separately and is not a distributable plugin component.

This repository starts from a fresh initial commit containing sanitized source only. Previous development history and raw validation files are not included. Documentation paths and desktop thread identifiers were replaced with synthetic examples. Commit authors and committers use the maintainer’s GitHub noreply identity. Sanitized historical summaries are contextual notes, not replayable execution receipts.

Run `python3 scripts/check_public_privacy.py` before committing. Run `python3 scripts/check_public_privacy.py --history` before publishing; it inspects reachable Git blobs and author/committer email metadata without printing matched values. This is a lightweight guard for known patterns, not a guarantee against all private information. Review new prose and logs for business or personal content as well.

Keep future clones on the rewritten history. An old clone must not merge or push the previous history back into the public repository. Existing installations and active task helper snapshots remain independent of source history.
