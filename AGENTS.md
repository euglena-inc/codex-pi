# Codex-Pi development

This repository is the canonical plugin source. Edit source here, never managed plugin caches. Project-specific rules belong to the consuming project.

Preserve existing uncommitted work. Test changed runtime behavior with checks selected by actual behavioral impact; preserve original failures and receipts. A new commit, round, reviewer or release label alone is not a reason to rerun full regression: reserve the complete Python suite for a justified broad integration boundary (for example shared test support used by many callers) and avoid rerunning child checks an enclosing command already includes. Desktop queue delivery and hook trust require their own actual validation; unit tests do not prove them.

Keep raw sessions, task boards, receipts, credentials and business-project evidence outside tracked files. Use synthetic identities and paths in fixtures. Keep public documentation explicit about sanitization and evidence limits.

Before committing or publishing, run `python3 scripts/check_public_privacy.py`; before publishing rewritten history, also run `python3 scripts/check_public_privacy.py --history`. Preserve private recovery copies outside this repository. Changes to published history require user authorization.
