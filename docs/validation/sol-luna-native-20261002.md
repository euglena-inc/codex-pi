# Native Sol-Luna validation — 2026-10-02

Scope: source delivery of the second plugin route, fixed requested Sol 6.1
Medium / GPT-6 Luna Max policy, native child execution/continuation and offline
multi-project comparison. Raw native sessions, child identities, local paths and
command receipts are private and are not published here. These summaries use
synthetic projects. No community implementation was installed or executed.

## Actual native execution

The main chat used the exposed native collaboration tools with explicit
`model="gpt-6-luna"`, `reasoning_effort="max"`, `fork_turns="none"`.

- Two fresh children were given different synthetic Git repositories, each with
  its own AGENTS.md, implementation, tests and private evidence. Both had the same
  baseline contents but separate project identities. Each completed stable
  deduplication, preserving order, empty strings and zero, and committed only its
  assigned project. Both candidates passed three tests and were clean.
- The main chat continued the ORIGINAL project-a child through `followup_task`
  for an explicit extension to list/dict equality. The child retained pre-fix
  failing regressions, repaired the implementation, passed five tests and created
  a new scoped commit. This tests native continuation, not a claimed quality
  rejection of the first narrower contract. Project-b stayed at its own candidate.
- The main chat checked both exact full candidate SHAs, clean status and source,
  and ran each project's real unittest entrypoint against the final candidate.
  Project-a passed five tests; project-b passed three. Different implementations
  and candidates remained in their designated projects. This proves the observed
  scope compliance, not an enforced filesystem sandbox.
- A third Luna child implemented the offline comparison helper in two disjoint
  files in the plugin repository. Main review found duplicate-key evidence
  ambiguity and a counter-bound problem; the SAME child repaired them on its
  first quality follow-up. Its thirteen CLI tests passed before integration.

The tools do not expose actual served-model/effort telemetry or per-agent usage.
Therefore actual-model verification and Sol/Luna token totals remain UNKNOWN.
The successful explicit requests and native executions are not proof that the
parent executed at Medium or that the service never rerouted a model.

## Offline comparison behavior

The comparator tests exercise positive/negative savings, incomplete and missing
usage, missing cache metrics, rejected/unverified outcomes, zero denominators,
cross-project/baseline/acceptance separation, duplicate route records and duplicate
JSON keys, invalid types/bounds, and malformed later inputs. Invalid input emits
no partial stdout or raw private content. It takes normalized declarations;
acceptance, actual models and exclusive usage attribution are not independently
proven by the tool. Synthetic arithmetic tests are not measured task savings.

## Unverified and intentionally distinct

- Parent model selection is a native client setting; the skill cannot force the
  root model/effort through an unavailable tool. It reports the limitation.
- Interrupt process release and same-child recovery after client restart were
  not tested. No unconditional recovery, hard token cap, or zero-wakeup promise.
- The two-delivery budget and takeover are Skill/main-record conventions, not
  persistent native admission guards. The plugin does not clone the Pi runtime.
- The installed plugin cache was not updated, and no hook trust or user config
  changed. Loading the packaged skill in a new installed-plugin chat remains a
  separate check. The existing marketplace packages the shared `skills/` root.
- No Pi-versus-Luna completed-task token/credit comparison was run. Source
  implementation, native smoke and savings measurement have different evidence.

Final source checks:

- `python3 -m unittest discover -s tests -p 'test_*.py' -q`: 297 tests,
  OK with one skip; 354.215 seconds. New comparator CLI tests are included.
- Skill-creator `quick_validate.py skills/sol-luna`: valid.
- Git staged diff check: clean; all local documentation references resolve.
- `python3 scripts/check_public_privacy.py`: PASS, 86 tracked files checked.
- Private native run records for the two synthetic projects remain separate in
  the comparator; unknown actual models/usage and absent Pi counterparts yield
  no savings claims.

Pi runtime logic and hooks are unchanged; only version metadata advances to 0.6.3.
