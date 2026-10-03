# Worker instruction clarity

## Goal and boundary

Improve the English instructions consumed by Pi without changing execution behavior. Rewrite the worker brief/contract, native tool descriptions, and dispatch template where compound sentences obscure actions or conditions. This is one complete local implementation and review phase. Publishing and installed-plugin updates are outside this phase.

Use the principles of explicit actors, one independently understandable action per sentence, nearby conditions, stable domain terms, and preserved modality. The upstream reference is https://github.com/danyuchn/asd-ste100-skill/tree/7d4a135a199a5d7447c4886bcd7ffe742a627bc9 . Do not vendor its text or linter, install it, add dependencies, or claim certified STE compliance. Do not impose word counts or mechanically merge distinct domain verbs.

## Owners and implementation

`runtime/pi_brief.py` owns generated instructions. `runtime/pi_worker.ts` owns native tool descriptions. `skills/collaborate/references/task-packet.md` owns dispatch guidance. Edit these canonical sources. Preserve existing generated field labels, APIs, identifiers, commands and dynamic interpolation. Existing frozen task helpers and round evidence remain immutable.

Rewrite only instructions and descriptions in runtime files, not control flow, schemas, budgets, guards or error/result protocols. Keep runtime and template guidance consistent. Add a short writing convention to the existing task packet rather than a new skill or process gate. Avoid expanding the main collaboration Skill or duplicating its policy. Update tests only if existing wording assertions require equivalent replacements or a concrete semantic gap needs a focused public-interface test; no prose snapshots or word-count tests.

## Required meaning

- Codex reviews; Pi implements and reports. Pi cannot claim acceptance. Preserve model pinning, no fallback and no nested models.
- Preserve read-only/writable scope, allowed external directories, forbidden checkouts, repository constraints and the distinction between a guard and an OS sandbox.
- A check receipt binds the actual command, exit, log hash, candidate HEAD and dirty state. Progress is self-report; readiness is mechanical evidence review, not acceptance.
- Executed failed checks retain receipts. Admission refusals return structured failure with receipt:null and do not spawn or write a receipt. A fulfilled Promise does not prove check success. Callers inspect every structured result.
- Estimated duration is not a guarantee. Use the larger valid call/contract estimate, or the effective request/contract cap when neither estimate exists. The execution window is limited by the request/contract cap, actual remaining round budget minus 60 seconds, and a verifiable enclosing codemode deadline minus cleanup grace. Preserve fractional seconds, refusal behavior and direct-call recovery when the enclosing deadline cannot be verified. Never suggest inventing smaller estimates or expanding authorization.
- Only a command matching a contract item that declares targetedCommand needs the corresponding final:true and clean-worktree rule. targetedCommand is optional. A targeted check never substitutes for formal acceptance. Do not make optional metadata appear mandatory.
- Await every nested call. Batch independent operations only. Preserve resource admission, unique check IDs, sequential dependent writes/commits, successful-script store/load semantics and the fact that completed calls are not rolled back.
- Missing, failed, skipped and unknown evidence are not a pass. Preserve the single eligible same-session continuation and the existing escalation boundaries.

## Review and verification

Before editing, map each rewritten paragraph to its actual implementation and record a compact original-to-revised meaning comparison in the delivery report. In particular inspect check admission and result handling, not only old prose. If prose conflicts with behavior, make wording accurately describe existing behavior and report the discrepancy. A required behavior change is a scope question, not an implied authorization to refactor.

Use affected existing tests for local feedback. Run the full Python unittest suite on the final clean committed candidate through the check tool. Run the public privacy checker before every commit and again on the final candidate. Inspect the final diff for changes outside the declared text-only runtime scope. The main Codex reviewer independently checks conditions, negation, actor, authorization and uncertainty against source.

No new desktop queue, hook trust or runtime algorithm is introduced, so this phase does not claim new desktop validation. Tests and review can prove regression coverage and retained instructions; they cannot prove an improvement in model success rate, tokens or cost. Report these limits explicitly. Do not add a benchmark or synthetic success claim.

## Completion and recovery

Pi delivers scoped commits, a clean worktree, full-suite and privacy receipts, a concise semantic comparison and any unresolved limits. Codex reviews the exact candidate and integrates only after acceptance. A failed check is repaired within scope with affected checks rerun. Missing external prerequisites or a true design/authority contradiction are reported with evidence. Keep original evidence and use the same Pi session for any requested repair.
