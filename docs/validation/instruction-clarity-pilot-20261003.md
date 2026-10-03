# Instruction clarity pilot: real Pi results

## Scope and method

- Model `newapi/deepseek-flash` with thinking `max`, Pi `1.0.0`.
- 36 real sessions, six scenario families x two instruction texts x three repetitions.
- Per session: 180.0s wall limit and at most 12 assistant turns (enforced cancellation); serial execution.
- Old/new instruction text extracted from frozen Git commits; all other runtime helper and guard sources are byte-identical across arms (recorded isolation proof).
- Scorer negative controls ran offline before the paid schedule and are part of the harness tests.
- Zero cost metadata from the gateway is unknown billing, not free usage; no price source is configured.

## Per-trial results (sanitized)

| Trial | Family | Arm | Rep | Status | Success | Key subscore | Turns | Wall s |
|---|---|---|---|---|---|---|---|---|
| t01 | fulfilled_promise_failed_check | new | 1 | completed | True | claims_withhold_success=True | 4 | 14.673 |
| t02 | fulfilled_promise_failed_check | old | 1 | completed | True | claims_withhold_success=True | 3 | 9.498 |
| t03 | insufficient_budget | old | 1 | turn_limit | False | avoided_or_guard_refused=True | 13 | 70.602 |
| t04 | insufficient_budget | new | 1 | completed | True | avoided_or_guard_refused=True | 7 | 60.155 |
| t05 | estimate_correction | new | 1 | completed | True | claims_consistent=True | 10 | 118.742 |
| t06 | estimate_correction | old | 1 | turn_limit | False | claims_consistent=False | 13 | 89.307 |
| t07 | targeted_present_absent | old | 1 | completed | True | claims_consistent=True | 10 | 22.353 |
| t08 | targeted_present_absent | new | 1 | completed | True | claims_consistent=True | 11 | 33.657 |
| t09 | failure_vs_refusal | new | 1 | completed | True | claims_distinguish=True | 10 | 89.516 |
| t10 | failure_vs_refusal | old | 1 | completed | True | claims_distinguish=True | 9 | 66.598 |
| t11 | readiness_progress_vs_acceptance | old | 1 | completed | True | claims_consistent_with_expected=True | 8 | 38.925 |
| t12 | readiness_progress_vs_acceptance | new | 1 | completed | True | claims_consistent_with_expected=True | 4 | 23.868 |
| t13 | fulfilled_promise_failed_check | old | 2 | completed | True | claims_withhold_success=True | 3 | 21.007 |
| t14 | fulfilled_promise_failed_check | new | 2 | completed | True | claims_withhold_success=True | 5 | 27.207 |
| t15 | insufficient_budget | new | 2 | completed | True | avoided_or_guard_refused=True | 8 | 38.801 |
| t16 | insufficient_budget | old | 2 | completed | True | avoided_or_guard_refused=True | 8 | 38.06 |
| t17 | estimate_correction | old | 2 | completed | True | claims_consistent=True | 10 | 144.289 |
| t18 | estimate_correction | new | 2 | turn_limit | False | claims_consistent=False | 13 | 71.624 |
| t19 | targeted_present_absent | new | 2 | completed | True | claims_consistent=True | 12 | 34.767 |
| t20 | targeted_present_absent | old | 2 | completed | True | claims_consistent=True | 9 | 25.402 |
| t21 | failure_vs_refusal | old | 2 | completed | True | claims_distinguish=True | 9 | 41.093 |
| t22 | failure_vs_refusal | new | 2 | completed | True | claims_distinguish=True | 10 | 76.425 |
| t23 | readiness_progress_vs_acceptance | new | 2 | completed | True | claims_consistent_with_expected=True | 7 | 28.599 |
| t24 | readiness_progress_vs_acceptance | old | 2 | completed | True | claims_consistent_with_expected=True | 4 | 9.548 |
| t25 | fulfilled_promise_failed_check | new | 3 | completed | True | claims_withhold_success=True | 4 | 26.711 |
| t26 | fulfilled_promise_failed_check | old | 3 | completed | True | claims_withhold_success=True | 4 | 19.061 |
| t27 | insufficient_budget | old | 3 | completed | True | avoided_or_guard_refused=True | 11 | 78.957 |
| t28 | insufficient_budget | new | 3 | completed | True | avoided_or_guard_refused=True | 7 | 37.448 |
| t29 | estimate_correction | new | 3 | completed | True | claims_consistent=True | 11 | 105.48 |
| t30 | estimate_correction | old | 3 | turn_limit | False | claims_consistent=False | 13 | 118.865 |
| t31 | targeted_present_absent | old | 3 | completed | True | claims_consistent=True | 7 | 22.186 |
| t32 | targeted_present_absent | new | 3 | completed | True | claims_consistent=True | 7 | 19.896 |
| t33 | failure_vs_refusal | new | 3 | completed | True | claims_distinguish=True | 11 | 71.089 |
| t34 | failure_vs_refusal | old | 3 | completed | True | claims_distinguish=True | 8 | 89.852 |
| t35 | readiness_progress_vs_acceptance | old | 3 | completed | True | claims_consistent_with_expected=True | 7 | 21.944 |
| t36 | readiness_progress_vs_acceptance | new | 3 | completed | True | claims_consistent_with_expected=True | 9 | 23.218 |

## Six-family summary

| Family | Old success/3 | New success/3 | Ties/3 |
|---|---|---|---|
| estimate_correction | 1/3 | 2/3 | 1/3 |
| failure_vs_refusal | 3/3 | 3/3 | 3/3 |
| fulfilled_promise_failed_check | 3/3 | 3/3 | 3/3 |
| insufficient_budget | 2/3 | 3/3 | 2/3 |
| readiness_progress_vs_acceptance | 3/3 | 3/3 | 3/3 |
| targeted_present_absent | 3/3 | 3/3 | 3/3 |

Strict family success counts: old 15/18, new 17/18. Three repetitions per family cannot establish broad significance; treat this as an observation, not proof of improvement.

## Recorded usage and limits

- Recorded token totals across 36 trials: input 730490, output 360996, cacheRead 2642816, cacheWrite 0, total 3734302.
- Sessions with unknown usage (provider did not report): 0.
- Provider/model reported per trial is recorded in the evidence manifest; the runner does not print or store credentials.
- Prompt cache is not controlled; cacheRead/cacheWrite are reported separately.
- A bounded model output limit is not exposed by the installed Pi CLI; no output cap was applied.
- Seed/temperature are not exposed; sessions are not deterministic.

## Negative controls

- Offline scorer controls: 13 cases, passed=True.
- Controls cover flipped exit/ok, missing receipts, head/hash mismatch, refusal as execution, acceptance claims, unsupported estimate lowering and targeted-for-formal substitution.

## Provenance and scoring corrections

- Harness (runner) sha256 at scoring time: `d34a74e2ce55d1fc23c24e959d0509db3c3f1a3ab29c7811323e8f8d6a447dbf`.
- Scenario-specs sha256: `e188a6b4fd90bfc5a155d9a3410814ea2c8574cd1deb803a228e09c3382f4381`.
- Schedule sha256: `0f1851adaf4abda93ae1600737e6ae67b23c3ec86c6540b437a52ed16e0d5f9e`.
- Treatment: `pi_brief.py` old/new = `9dfaaff63a4dbde7748822740ede550a6cfa379c37b1b7ef4dba17aa5564f40c` / `23714473a222896a84c64133e24ced2b0646354173f395e5b2a55c7edd979bb0`; `pi_worker.ts` old/new = `d3010f0a2c2435f471690300ab7f39e84e89e24f4116e1b9c1701c31cb3d5199` / `2515c1780abdab48cdefa0e9a881e706e63b5d05b53faf389620e6e86954cbe9`. Isolation proofs: `pi_brief.py` statements outside the text builders byte-identical and all changed lines inside `compose_brief`/`compose_contract`; `pi_worker.ts` token streams identical with string/template/regex bodies blanked and all changed lines inside the `check`/`progress`/`readiness` instruction blocks.
- Whole-command wall: 1854.6s of the authorized 7200s; one session active at a time; 36 sessions, no retries and no extra paid pre-runs. Per-session wall limit 180s (slowest observed 144.3s) and 12 assistant turns, enforced by cancellation. Four sessions hit the 13th-turn cancellation (`estimate_correction` x3 and one `insufficient_budget`); their partial traces are preserved and scored as failures because no final report was produced.
- Scorer corrections after execution (raw sessions, receipts and trace hashes untouched; all 36 trials re-scored deterministically from the preserved traces): (1) the `completed` list in the requested report form is open-ended, so item status is judged only from the explicit `verification` map plus receipts and traces; (2) the rejection of an arbitrary unsupported estimate lowering was already decided by receipt/marker evidence. The two rescore events are recorded in the private manifest with before/after harness hashes.
- Private raw evidence (append-only sessions, receipts, fixture artifacts and hashes) lives under the task's allowed private evidence area; the read-only verifier checks the manifest, treatment proofs, per-trial traces and score hashes without launching a model.

## Interpretation and limitations

- Observed strict family success: old text 15/18, new text 17/18. The difference is two `estimate_correction` sessions; all four failures were turn-limit cancellations without a final report, not wrong conclusions. With three repetitions per family this is a weak observation; no statistical significance or proven improvement is claimed, and the new text did not win every family.
- The failures are valid unfavorable results: the partial traces show the guard refusal, marker absence and supported correction were handled, but the sessions exhausted the turn budget before the requested final report, so the required evidence is incomplete.
- No provider or network errors occurred; consequently this run cannot confirm or refute the earlier transport failures and says nothing about endpoint speed or whether every request parameter is forwarded identically.
- Reported cost was zero/absent for every trial (no price source configured). Token usage above is real; it is not evidence that inference was free.

## Evidence limits

- Model failures and provider or network errors are real experimental data; no failed trial was retried to obtain a better score.
- Treatments differ only in instruction text; the same guard and tool implementation ran in both arms.
- The private raw evidence directory keeps append-only sessions, receipts and hashes outside tracked files; this report contains no local paths or raw transcripts.
- No statistical significance is claimed from three repetitions per family.
