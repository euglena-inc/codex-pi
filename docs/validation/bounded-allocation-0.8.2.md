# Bounded allocation and explicit takeover: 0.8.2

2026-10-04. Canonical source builds on the 0.8.1 Qwen release. This verification uses synthetic repositories, tasks and identities; it contains no business-project sessions, task boards, receipts or credentials.

## Result and scope

Codex can choose direct completion or bounded design followed by a complete Pi outcome. New tasks freeze early-takeover eligibility alongside the quality limit. The explicit `pi_board.py takeover` command retains the exact candidate, dirty files, original task/session/round/helper evidence and budgets, records a main-decision cause, and transfers ownership without manufacturing a rejection or acceptance. The two-quality-failure limit remains the automatic hard stop. Historical metadata without eligibility is refused by the new command.

The existing admission and writer leases guard the handoff transaction. Recorded process leaders/groups must have exited; unknown execution, unavailable process identity, active leases, stale rounds, differing HEAD, unverified/changed contracts and accepted outcomes cannot grant a new handoff. Identical replay returns the existing receipt; conflicting replay is refused. The existing takeover event, archive policy checkpoint, pause/resume and continuation guard are reused.

Cost fields identify their scope and source. Pi assistant and auxiliary reports remain available, including a reported zero. Report completeness does not prove billing completeness. Non-finite, negative and boolean reported costs do not enter the board's known sum. Plugin-output bytes are not Codex tokens. Full workflow cost remains unknown; existing plan/brief references point toward project acceptance records but do not attest external cost coverage.

## Verification

Targeted command:

```sh
PYTHONPATH=tests python3 -m unittest test_takeover test_archive test_runtime_efficiency.BoardMetricsTest test_modules.LayoutTest
```

42 tests passed. The lifecycle tests run actual start/register/decide/takeover/refresh/pause/resume/continue CLI processes and temporary Git worktrees with the offline Pi double. They verify zero/one real quality failure, later separate quality review, persistent handoff cause, byte-preserved original evidence, dirty preservation, immutable budget, replay and refusal of another Pi round. Negative controls cover legacy metadata, accepted/stale/unknown outcomes, current contracts, a held task lease and a real live recorded process. The live-PID fixture restores the original state before its normal cleanup. Archive tests keep the real failure count through more than one projection window and later acceptance records. Metrics tests independently check zero/invalid costs and byte-unit coverage. Module checks include frozen-helper execution without the canonical plugin directory.

Full regression:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/check_public_privacy.py
```

The final full regression passed all 468 tests in 427.615 seconds. The first complete run retained two failures from obsolete assertions (the release version and the expanded cost metadata); both assertions were updated and the entire suite was rerun. Public privacy verification passed for all 115 tracked files; matched values were withheld. `git diff --check` also passed. Raw logs and the recovery copy remain outside the tracked repository.

## Evidence limits

No model/provider request is made by these checks. The offline double proves lifecycle and ownership behavior, not a selected model's coding capability, response speed or cost advantage. This change makes evidence coverage explicit; it does not automatically measure Codex or external work or ingest verified billing. Policy prose cannot establish reasoning quality.

No new live desktop queue or hook-trust acceptance is claimed. The existing queue transport and hooks are reused; these tests cannot replace a visible desktop delivery observation. Recorded process-group inspection cannot attest arbitrary unrecorded detached processes. Existing raw summaries and historical frozen tasks are not rewritten.
