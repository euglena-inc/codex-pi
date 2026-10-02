# Two complete Pi deliveries with main-session global analysis

## Goal and allocation

Codex uses its capacity for whole-task design and review while Pi implements,
integrates, tests and repairs ordinary failures. Before dispatching the first
complete outcome, Codex reviews the full authorized result and remaining plan:
shared invariants, affected entry points, difficult states, dependencies, likely
downstream problems, evaluation validity, budgets and the closing sequence.

The first reviewed quality failure requires a fresh global analysis by the same
Codex main session, using the failed candidate and actual evidence. Codex produces
one coherent repair solution for the entire outcome and updates downstream design
decisions, without executing unauthorized later phases. The SAME Pi session and
worktree gets one more complete delivery. If that delivery also has a reviewed
quality failure, Codex performs the same global analysis again, then directly
implements and verifies the result after proving writer release. No third Pi
delivery of the failed outcome, no model-switch workaround and no budget reset.

Internal Pi red tests, ordinary repairs, progress messages, external prerequisites
and the already bounded pre-review receipt补齐 are not reviewed quality failures.
Count one exact negative quality decision per distinct delivery round; duplicate
events, contract revisions and phase renames cannot reset the count. A successful
acceptance before takeover starts a fresh count for an authorized next outcome.
Existing task pins and takeover latches remain immutable. New tasks use limit 2;
historical pins 1/2 and the old unpinned default 3 remain readable. A malformed
pin cannot silently increase its allowance. Do not edit active snapshots.

## Lessons and scope of global analysis

Previous main-session takeovers found further omissions one at a time despite
green component tests: historical reads, revocation during an awaited read,
derived-source access, dependency digest coverage, UI integration, receipts and
evaluation bookkeeping. The failure was incomplete scope enumeration, not just
insufficient retries or insufficient full-suite runs.

At the first failure and again before takeover, Codex must enumerate the whole
affected working set rather than stop at the first reproduced defect. Build a
compact coverage matrix from actual source: entry point × data/owner kind ×
relevant state transition or ordering. Include restarts, replay, revocation,
unknown evidence and revalidation after waits when relevant. Identify every
affected entry in that scope as verified, reachable-but-unverified or excluded
with reason and evidence. Check both implementation and the evaluation path:
negative controls, request accounting, retained test identities and final output.
Use project contracts to distinguish blockers from follow-ups. A missing required
check or unknown required evidence remains a blocker; 'only reproduced bugs block'
must not waive acceptance, authority or material unresolved semantics.

Resolve existing authorized design choices early. Do not rewrite unrelated modules
or introduce a generic registry solely to satisfy this matrix. Reuse project PLAN
and design sources. Consolidate findings by shared cause into one repair batch.
Rerun affected checks while repairing; freeze the final candidate before formal
full checks and any single-use held-out evaluation. New relevant failures or a
code change can require another check; never promise a pass by enforcing 'once'.
Do not erase failed checks, repeat a spent held-out test, conflate process exit
with acceptance or claim a time/savings guarantee without evidence.

## Minimal runtime support

Keep existing board events and deterministic supervision. Do not add heartbeat,
new model workers, new services or model-based checks of the plan. A new task pins
limit 2 and a global-replan requirement. Its compact reviewPolicy tells the main
session when replan is required and when takeover is required.

After the first quality rejection, continue/auto-continue/internal worker must
not start another Pi process until there is a main-session repair plan bound to
that exact failure. Add `continue --repair-plan-file` for a bounded Markdown file.
Freeze the plan in the new round, include it in the immutable brief, and record
its hash, failed event/round/task identity and dispatch round. Do not trust a stale,
missing, modified or unparseable receipt. The structural gate cannot prove analysis
quality or code acceptance. No extra model wake is needed to validate these fields.

Reuse an existing repair document where possible, with these nonempty sections:

- `## Findings`: all consolidated failures, reproductions and shared invariants.
- `## Coverage`: affected entries/states and a source-backed coverage matrix.
- `## Remaining plan`: downstream risks, dependencies, resolved choices and unknowns.
- `## Solution`: coherent complete repair scope, protected work and integration choices.
- `## Validation`: targeted and final checks, evidence reuse, budgets, single-use tests,
  blockers/follow-ups and stopping/acceptance conditions.

Keep this short and link valid evidence. No empty headings or copied log dumps.
Once approved plan evidence exists, existing internal repairs can reuse it until
acceptance or a new failure identity; it cannot approve another phase's failure.
Pause/interrupt and external gates stay independent and cannot be bypassed by
supplying a plan. A second quality failure latches takeover, even if a plan remains.

## Implementation and verification boundary

Update runtime, focused offline behavioral tests, skill, task-packet/runtime/handoff
references, README and release metadata coherently for 0.5.3. Keep model selection
and authentication unchanged; this task's worker is DeepSeek Flash/max. Preserve
preexisting terminal-notification fixes and command guard work. Source analysis
from another project informs these rules only; do not write that project's tree,
contact its chat, terminate its processes or import private transcripts/identities.

Use real temporary Git repositories and the offline Pi double to test the entire
transition: first delivery → first rejection → blocked missing/incomplete/stale
plan → exact frozen plan → same-session second delivery → accept or takeover.
Cover pause, duplicate decisions, external blockers, unknown/tampered evidence,
phase/contract changes, historical pins, automatic continuation and direct worker
bypass. Keep runtime/doc defaults consistent. Run the full Python suite once on
the final candidate after focused failures are repaired, and the privacy guard.
Retain test receipts bound to the exact clean candidate and raw evidence privately.
Do not install, push or edit managed caches/hooks as part of Pi implementation.

Delivery includes exact commits, scope, check results and original receipt links,
what this structural gate does not prove, and any remaining blocking issue.
