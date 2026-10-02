# Pi codemode integration

## Outcome and baseline

Make new Codex-Pi workers use Pi 1.0.0 native codemode effectively while preserving task ownership, fixed model selection, immutable receipts, frozen helpers, same-session recovery and external review. Baseline: 57ea8135484c097f6aa38e22041ffb0ec43b3a71. This phase includes implementation, regression tests, real Pi engine validation and documentation. It does not publish, install plugins, change hook trust, introduce MCP servers or replace the supervisor/board. Historical task evidence remains immutable.

## Settled design

- Register the exported `createCodemodeExtension({models:false, mode:"on"})` from Pi through the explicit worker extension. Pi 1.0.0 publicly exports it. Keep `--no-extensions`, skills and templates isolation. Do not also register builtin:codemode. Add codemode to both task tool selections without expanding the underlying read-only selection. Mixed mode is deliberate: simple calls remain direct; batching, branching and filtering use codemode. No classifier, image or additional chat model capability is authorized. No dependency on user codemode settings; no copied upstream sandbox implementation.
- Native `tools.*` nested calls run through Pi's validation and `tool_call`/`tool_result` hooks and emit parentToolCallId. Recognize the codemode envelope, preserve the same guard for every nested call and fail closed on unsupported initialization. Extend load proof so a new worker cannot claim successful loading before native codemode registration succeeds. Preserve old frozen-worker evidence interpretation.
- Clamp an effective codemode deadline and output limit at the tool boundary, with the command cap as the ceiling and a bounded default. Parse/normalize the first-line options with upstream-compatible semantics rather than scanning JavaScript for forbidden strings. Invalid inputs fail closed. Script cancellation must propagate to nested tools; completed writes are not transactional. Keep the outer phase/supervisor deadline. Test omitted, excessive, invalid options and async cancellation with real execution.
- Add outputSchema and structuredContent to check/progress/readiness with explicit successful/error semantics, while retaining useful text/details for ordinary calls and existing evidence readers. A failed check still records its immutable receipt and structured exit/timeout/cancel data. Consumers must inspect semantic failure, not Promise fulfillment. Guard errors and malformed input must never look like successful empty results. Avoid unnecessary new tool surfaces.
- Nested tool events already exist: preserve counts and exit observations, understand parent/child relationships, and avoid double-counting. Model usage remains assistant usage because models globals are disabled; do not claim support for unmeasured auxiliary-model accounting. Test synthetic nested event streams including failures and cancellation. Acceptance always reads recorded receipts and candidate identity, never script text or store values.
- `store/load` is Pi-owned branch/session state for small cursors and summaries only. Verify native resume retains successful writes, failed scripts do not commit store changes, and no plugin recovery rewrites sessions. No new persistence layer.
- Add concise worker guidance to batch independent reads/checks, filter large output, await all calls, inspect structured failures, and serialize dependent writes/commits. Parallel checks must have independent resources and unique IDs. Codemode does not make the existing shell guard an OS sandbox.
- Freeze all added runtime helpers through the existing snapshot mechanism. Preserve old 0.6 task helpers and adoption semantics; changes to defaults affect newly frozen runtimes only. Use a minor feature version 0.7.0 consistently; no automatic migration of active tasks.

## Verification and evidence

Pi owns implementation, tests, repairs and scoped local commits. The ordinary Python suite must remain offline and use synthetic identities. Adapt the extension harness without substituting a fake sandbox for the real integration check.

Provide `scripts/validate_codemode.py` as a reproducible, bounded real Pi 1.0.0+ integration probe. It must exercise the actual installed Pi session/tool pipeline and QuickJS with temporary synthetic repositories/evidence, without paid providers or credentials (use SDK/fake provider session inputs as needed). It must fail nonzero if required Pi/Node is unavailable, not skip and report success. Cover native registration and models absence, structured success/failure, nested forbidden write, cancellation/timeout, and store success/failure/resume. Emit a compact summary; retain private raw evidence outside tracked files when requested. Each claimed property needs an independent expected observation and negative control. Probe engine availability alone is not end-to-end acceptance.

Required candidate checks, run with the check tool after the final commit:

- `python3 -m unittest discover -s tests -p 'test_*.py'`: complete regression, no failures. Relevant new tests must actually run.
- `python3 scripts/validate_codemode.py`: actual Pi/QuickJS probe, every required case passed, no skip.
- `python3 scripts/check_public_privacy.py`: zero findings; also run before every commit.

Main Codex then reviews exact candidate/source and receipts, runs a bounded real worker smoke with the candidate runtime and fixed configured model, and observes actual delivery to this desktop chat. That final smoke must demonstrate a codemode invocation, nested guarded tools and recorded checks; an ordinary task started under old frozen helpers is not proof of new-runtime behavior. A same-task comparison of representative independent reads/log filtering may measure round trips and token/time effects if bounded, but no savings percentage is required or may be invented. Publish sanitized validation facts and limitations only, never raw sessions, owner IDs, task boards, credentials or host-specific paths.

## Scope and recovery

Allowed: runtime, tests, scripts, canonical collaboration skill/reference wording, README, public design/validation docs and plugin version metadata. Preserve other features and privacy checks. No external messages except the authorized existing queue handoff. No additional agents or nested implementation workers. Full phase budget: four hours; individual formal checks: ten minutes. Fix ordinary failures autonomously. A genuine unavailable dependency or contradictory API requires a minimal reproduction and precise unlock condition, not a silent fallback. Unavailable/missing evidence is not a pass. Two reviewed complete deliveries then main-session takeover follow the collaboration contract.
