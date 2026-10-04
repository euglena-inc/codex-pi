# Outcome observation verification

The 0.8.5 CLI records Main-reported closeout observations in private immutable JSON revisions and reads six metric sections from existing bounded metadata. Recording is not acceptance and does not change execution or ownership.

The targeted suite exercises the real CLI against temporary Git repositories. Controls include native `pi_summary.total_metrics` output, auxiliary costs counted once, mixed-model attribution, partial external costs, finite sums, overlapping intervals, selected-round archive decisions, receipt deduplication and conflicts, missing sources, identity changes, intermediate symlinks, idempotence/CAS and publication limits. Revision count and cumulative bytes are checked before publication; an identical replay at the limit still succeeds.

Two implementation deliveries were reviewed and returned for material correctness defects. Main preserved both candidates and their successful execution receipts, verified writer release, then completed the source validation and aggregation repairs. Earlier green runs are not acceptance of the repaired candidate. Exact candidate, commands, receipt hashes and review evidence remain in private task storage.

Validation commands: `PYTHONPATH=tests python3 -m unittest test_outcome_metrics -v`, then the frozen full Python suite on the clean integration candidate, public privacy scan and whitespace check. These tests do not prove desktop queue delivery, hook trust, billing accuracy or model strategy savings. No new claim is made about those behaviors. Actual retrospective observations are recorded only from independently accepted project evidence; missing Main token/cost measurements remain unknown.
