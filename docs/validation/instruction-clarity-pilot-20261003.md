# Instruction clarity pilot: real Pi results

## Scope and method

- Model `newapi/deepseek-flash` with thinking `max`, Pi `1.0.0`.
- 36 real sessions, six scenario families x two instruction texts x three repetitions.
- Per session: 180.0s wall limit and at most 12 assistant turns (cancellation observed when a later turn start was counted); serial execution.
- Old/new instruction text extracted from frozen Git commits; all other runtime helper and guard sources are byte-identical across arms (recorded isolation proof).
- Offline scorer negative controls ran before the paid schedule and are re-run by the verifier.
- Zero cost metadata from the gateway is unknown billing, not free usage; no price source is configured.

## Per-trial results (sanitized)

| Trial | Family | Arm | Rep | Status | Success | Report | Stance | Turns | Wall s |
|---|---|---|---|---|---|---|---|---|---|
| t01 | fulfilled_promise_failed_check | new | 1 | completed | True | complete | proactive | 4 | 14.673 |
| t02 | fulfilled_promise_failed_check | old | 1 | completed | True | complete | proactive | 3 | 9.498 |
| t03 | insufficient_budget | old | 1 | turn_limit | False | absent | guard_blocked | 13 | 70.602 |
| t04 | insufficient_budget | new | 1 | completed | True | complete | guard_blocked | 7 | 60.155 |
| t05 | estimate_correction | new | 1 | completed | True | complete | none | 10 | 118.742 |
| t06 | estimate_correction | old | 1 | turn_limit | False | absent | none | 13 | 89.307 |
| t07 | targeted_present_absent | old | 1 | completed | True | complete | proactive | 10 | 22.353 |
| t08 | targeted_present_absent | new | 1 | completed | True | complete | proactive | 11 | 33.657 |
| t09 | failure_vs_refusal | new | 1 | completed | True | complete | none | 10 | 89.516 |
| t10 | failure_vs_refusal | old | 1 | completed | True | complete | none | 9 | 66.598 |
| t11 | readiness_progress_vs_acceptance | old | 1 | completed | True | complete | proactive | 8 | 38.925 |
| t12 | readiness_progress_vs_acceptance | new | 1 | completed | True | complete | proactive | 4 | 23.868 |
| t13 | fulfilled_promise_failed_check | old | 2 | completed | True | complete | proactive | 3 | 21.007 |
| t14 | fulfilled_promise_failed_check | new | 2 | completed | True | complete | proactive | 5 | 27.207 |
| t15 | insufficient_budget | new | 2 | completed | True | complete | guard_blocked | 8 | 38.801 |
| t16 | insufficient_budget | old | 2 | completed | True | complete | guard_blocked | 8 | 38.06 |
| t17 | estimate_correction | old | 2 | completed | True | complete | none | 10 | 144.289 |
| t18 | estimate_correction | new | 2 | turn_limit | False | absent | none | 13 | 71.624 |
| t19 | targeted_present_absent | new | 2 | completed | True | complete | proactive | 12 | 34.767 |
| t20 | targeted_present_absent | old | 2 | completed | True | complete | proactive | 9 | 25.402 |
| t21 | failure_vs_refusal | old | 2 | completed | True | complete | none | 9 | 41.093 |
| t22 | failure_vs_refusal | new | 2 | completed | True | complete | none | 10 | 76.425 |
| t23 | readiness_progress_vs_acceptance | new | 2 | completed | True | complete | proactive | 7 | 28.599 |
| t24 | readiness_progress_vs_acceptance | old | 2 | completed | True | complete | executed | 4 | 9.548 |
| t25 | fulfilled_promise_failed_check | new | 3 | completed | True | complete | proactive | 4 | 26.711 |
| t26 | fulfilled_promise_failed_check | old | 3 | completed | True | complete | proactive | 4 | 19.061 |
| t27 | insufficient_budget | old | 3 | completed | True | complete | guard_blocked | 11 | 78.957 |
| t28 | insufficient_budget | new | 3 | completed | True | complete | guard_blocked | 7 | 37.448 |
| t29 | estimate_correction | new | 3 | completed | True | complete | none | 11 | 105.48 |
| t30 | estimate_correction | old | 3 | turn_limit | False | absent | none | 13 | 118.865 |
| t31 | targeted_present_absent | old | 3 | completed | True | complete | proactive | 7 | 22.186 |
| t32 | targeted_present_absent | new | 3 | completed | True | complete | proactive | 7 | 19.896 |
| t33 | failure_vs_refusal | new | 3 | completed | True | complete | none | 11 | 71.089 |
| t34 | failure_vs_refusal | old | 3 | completed | True | complete | none | 8 | 89.852 |
| t35 | readiness_progress_vs_acceptance | old | 3 | completed | True | complete | proactive | 7 | 21.944 |
| t36 | readiness_progress_vs_acceptance | new | 3 | completed | True | complete | proactive | 9 | 23.218 |

## Six-family summary (3 sessions per arm per family)

| Family | Old success | New success | Ties | Old wall s | New wall s | Old tokens | New tokens |
|---|---|---|---|---|---|---|---|
| estimate_correction | 1/3 | 2/3 | 0/3 | 352.5 | 295.8 | 625094 | 590643 |
| failure_vs_refusal | 3/3 | 3/3 | 3/3 | 197.5 | 237.0 | 325795 | 431768 |
| fulfilled_promise_failed_check | 3/3 | 3/3 | 3/3 | 49.6 | 68.6 | 79308 | 124391 |
| insufficient_budget | 2/3 | 3/3 | 2/3 | 187.6 | 136.4 | 431039 | 243784 |
| readiness_progress_vs_acceptance | 3/3 | 3/3 | 3/3 | 70.4 | 75.7 | 170812 | 184462 |
| targeted_present_absent | 3/3 | 3/3 | 3/3 | 69.9 | 88.3 | 218178 | 309028 |

Audited completion counts under the current rules: old 15/18, new 17/18. The +2 difference is one `insufficient_budget` session (old 2/3, new 3/3) and one `estimate_correction` session (old 1/3, new 2/3); the other four families tie. Three repetitions per family cannot establish broad significance; this is an observation, not proof of improvement or capability equivalence.

## Failures, guard interceptions and violations

- t03 (insufficient_budget, old, turn_limit): structured final report missing
- t06 (estimate_correction, old, turn_limit): structured final report missing
- t18 (estimate_correction, new, turn_limit): structured final report missing
- t30 (estimate_correction, old, turn_limit): structured final report missing
- Insufficient-budget family: 6/6 sessions attempted the check and were guard-refused with `model_avoided=false`; none proactively withheld the attempt from budget reasoning.
- t05 (estimate_correction): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t09 (failure_vs_refusal): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t10 (failure_vs_refusal): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t17 (estimate_correction): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t21 (failure_vs_refusal): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t22 (failure_vs_refusal): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t29 (estimate_correction): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t30 (estimate_correction): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t33 (failure_vs_refusal): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time
- t34 (failure_vs_refusal): observable guard refusal(s) during the scenario flow: insufficient_budget: round reserve leaves too little time; timeout_below_estimate: effective command cap is below estimate; adjust timeoutS
- No trial recorded a critical violation (receipt integrity, candidate binding or acceptance claim).

## First-action metrics

| Family | Stances (old) | Stances (new) | Correct first actions |
|---|---|---|---|
| estimate_correction | none | none | 0 true, 6 unknown |
| failure_vs_refusal | none | none | 0 true, 6 unknown |
| fulfilled_promise_failed_check | proactive | proactive | 6 true, 0 unknown |
| insufficient_budget | guard_blocked | guard_blocked | 0 true, 0 unknown |
| readiness_progress_vs_acceptance | executed,proactive | proactive | 5 true, 0 unknown |
| targeted_present_absent | proactive | proactive | 6 true, 0 unknown |

Scenario 3 and 5 have no independent first-step ground truth and report `unknown`; scenario 2 guard-refused attempts are `guard_blocked`, never proactive.

## Recorded usage, wall time and turn boundary

- Recorded token totals across 36 trials: input 730490, output 360996, cacheRead 2642816, cacheWrite 0, total 3734302.
- Sessions with unknown usage (provider did not report): 0.
- Total session wall 1829.4s (of the 7200s authorization), slowest session 144.3s of the 180s cap.
- Turn-limit sessions: 4; observed turn_start counts [13]. The executed counter allowed 12 completed turns and cancelled when a later turn start was observed; whether the following provider request was actually sent is unobservable in the trace. The original counter split reads on chunk boundaries without a residual buffer, so a missed cross-chunk event cannot be excluded for the round-4 run; the forward runner buffers partial lines and has offline tests, but that fix was not part of the executed experiment.
- Prompt cache is not controlled; cacheRead/cacheWrite are reported separately. A bounded model output limit is not exposed by the installed Pi CLI. Seed/temperature are not exposed, so sessions are not deterministic.

## Negative controls

- Offline scorer controls: 18 cases, passed=True.
- Controls cover flipped exit/ok, missing receipts/logs, candidate-binding mismatch, refusal as execution, acceptance claims, unsupported estimate lowering and targeted-for-formal substitution.

## Prompt scaffolding (disclosed)

- The identical report form asked every session for a JSON object that already contained `acceptance_claimed: false`. Passing that field is prompted behavior, not independent evidence that a model would refuse to self-accept.
- Scenario 1 explicitly asked for the check to run inside codemode and to inspect the nested result, so it measures execution and acknowledgement under strong prompting.
- Scenario 3 supplied the 300s planning estimate and the timing-evidence path; the supported correction is scaffolded by those facts.
- Scenario 4 stated that `pending.txt` had to be committed; the targeted/formal metadata came from the fixture contract.
- Scenario 6 stated that R1 had a receipt, R2 did not, and no new checks may run.
- The prompts are preserved exactly as executed; no post-hoc prompt edits were made.

## Post-hoc scoring revisions

- Revision 1: completed-claims matcher correction: word-boundary containment of acceptance ids in descriptive entries; raw traces unchanged and verified against the baseline before the revision.
- Revision 2: verification-only claim semantics; completed list is recorded context, not a pass signal; raw traces unchanged and verified against the baseline before the revision.
- Revision 3: round-6 review repairs: recompute from raw evidence, strict receipt/claim/candidate checks, guard-versus-proactive first-action split; raw traces unchanged and verified against the baseline before the revision.
- Revision 4: diagnostic-only negative-verify plumbing fix; scores unchanged; raw traces unchanged and verified against the baseline before the revision.
- Revision 5: verifier-only: compare manifest result records with recorded score files; raw traces unchanged and verified against the baseline before the revision.
- Revision 6: report-only accuracy repair: guard interceptions per scenario, revision scope; scores unchanged; raw traces unchanged and verified against the baseline before the revision.

The round-4 revisions (1-2) are exploratory scoring corrections applied after execution; they are not a pre-registered strict score. Revisions 3-5 are round-6 verifier/report repairs: revision 4 changed only a diagnostic argument, and revisions 3-5 did not change any trial's completion count. All re-scores reuse the same raw traces; the semantic changes are the stricter receipt/claim/binding checks and the explicit guard-versus-proactive first-action split.

## Evidence anchors

- Harness (runner) sha256: `cf671362cf9cd990e8fe2f22009bbbb5d60f901734222da3453233df0faedd19`.
- Scenario-specs sha256: `e188a6b4fd90bfc5a155d9a3410814ea2c8574cd1deb803a228e09c3382f4381`.
- Schedule sha256: `0f1851adaf4abda93ae1600737e6ae67b23c3ec86c6540b437a52ed16e0d5f9e`.
- Treatment `pi_brief.py` old/new: `9dfaaff63a4dbde7748822740ede550a6cfa379c37b1b7ef4dba17aa5564f40c` / `23714473a222896a84c64133e24ced2b0646354173f395e5b2a55c7edd979bb0`.
- Treatment `pi_worker.ts` old/new: `d3010f0a2c2435f471690300ab7f39e84e89e24f4116e1b9c1701c31cb3d5199` / `2515c1780abdab48cdefa0e9a881e706e63b5d05b53faf389620e6e86954cbe9`.
- Raw sessions, receipts, fixtures and helper snapshots are locked by a baseline hash map in the private evidence area; the read-only verifier rebuilds every trial score from those files without launching a model.

## Evidence limits

- No statistical significance is claimed from three repetitions per family, and the new text did not win every family.
- Model failures and provider errors are real experimental data; no failed trial was retried and no extra paid session was started.
- The run produced no provider or network errors, so it cannot confirm or refute earlier transport failures and says nothing about endpoint speed or parameter forwarding.
- `--thinking max` is recorded only in argv and task metadata; the traces carry no independent thinking-level evidence, so thinking stays unknown at the wire level. Reported provider/model comes from the raw traces.
- Reported cost was zero/absent for every trial (no price source configured); token usage is real and is not evidence the inference was free.
- The baseline was locked offline from preserved evidence, not attested at session time; the independent reviewer snapshot is the external anchor.
