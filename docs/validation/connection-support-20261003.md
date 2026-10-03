# Connection support validation (2026-10-03)

Scope: the optional project `network` policy (`proxyUrl`, `diagnostics`) and the
bounded transport diagnostics observer from
[the design](../design/pi-connection-support.md). This record is mechanism
evidence only. It does not prove that a proxy repairs connectivity, does not
judge model quality and makes no claim about NewAPI billing.

All examples use synthetic addresses (`proxy.invalid`). Raw logs, sessions,
diagnostics sidecars and receipts stay in the private task evidence directory;
only sanitized conclusions are kept here.

## Candidate

- Repair commit reviewed here: `0e1f4373ab14ec41d49994410a469b2b4c39e43d`.
  The original implementation was `bf5c564`; `268df64` and `4d8292f` were
  documentation-only follow-ups.
- Frozen-policy, helper-snapshot and environment wiring: `runtime/pi_core.py`,
  `runtime/pi_task.py`, `runtime/pi_supervisor.py`,
  `runtime/pi_network_diagnostics.mjs`.
- Deterministic entrypoints: `scripts/validate_connection_support.py`,
  `scripts/connection_support_probe.mjs`, `tests/test_connection_support.py`.

## Review repair

A complete quality review reproduced three shared defects on the previous
candidate; all are fixed and covered by negative controls.

1. Missing evidence no longer points to the proxy. A code-only
   `{code:'ECONNRESET'}` event used to become `proxy_connect_failure`; the
   classifier now scans a bounded error/cause chain and returns proxy evidence
   only from a usable (non-empty, ≤4096-character) message. The repro now
   yields `connection_reset`, and an oversized message containing proxy text is
   likewise `connection_reset`, never proxy.
2. Explicit evidence in a wrapped cause outranks ambiguous outer codes. An
   outer `Error('Connection error.', code=UND_ERR_ABORTED)` wrapping a cause
   named `AbortError` with `Proxy response (503) !== 200 when HTTP Tunneling`
   used to become `abort_cleanup`; it now yields
   `proxy_connect_failure` with status `503`.
3. Ambiguous abort codes no longer prove healthy cleanup.
   `UND_ERR_ABORTED`/`DESTROYED`/`CLOSED` without an `AbortError` name become
   `transport_error` before headers and `post_header_error` after them; only an
   explicit `AbortError` is `abort_cleanup` and excluded from the failure count.

A direct reproduction run on the repair candidate measured: 4 sequential
1000-event writers produced one 65359-byte file (≤ 65536) with 391 records and
an explicit truncation signal; 4 concurrent inherited children grew the file by
0 bytes; code-only reset → `connection_reset`; wrapped proxy → `503`.

## Deterministic evidence

`python3 scripts/validate_connection_support.py` exited 0 on repair candidate
`0e1f437` (check receipt `validate-connection-support-repair-eeb375d7ee8c`,
clean tree) with:

- config matrix: 2 accepted shapes, 12 rejected shapes, no rejected value
  echoed;
- synthetic diagnostics-channel injection: 19 ordered records covering all ten
  safe classes, the CONNECT `503` status preserved, both review repros and the
  missing/oversized-message and pre/post-header negatives, no raw URL/message
  fragment in the sidecar, `abort_cleanup` outside the failure count;
- real local fault server: connection reset before headers produces exactly one
  pre-header transport failure and the healthy request adds none;
- single-writer bound: one primary flood produces 257 lines including a
  `truncated` marker; four concurrent inherited floods append 0 bytes;
- write failure at a directory path: process exit 0, no crash;
- fake-Pi integration module (`tests/test_connection_support.py`, 34 tests)
  passed inside the validator and standalone.

The module covers absent-config compatibility, invalid-config rejection before
spawn without echoing the value, frozen new-task policy versus edited project
settings, legacy tasks without the `network` key, exact child environment
(overridden proxy forms, untouched `NO_PROXY`, unrelated variables and existing
`NODE_OPTIONS`), paths with spaces (percent-encoded `file://` import),
helper-snapshot completeness including the new preload, adversarial
secret-bearing error text, disabled-mode silence, and reader negatives for
oversized, non-UTF-8, truncated, forged-classification, forged-field and
forged-state input (all unreadable with empty counts and no value reflection).

## Real Pi smoke

Three bounded sessions (the authorized maximum) ran through the check tool with
the pinned route `newapi/deepseek-flash`, `thinking=max`, the read tool and the
candidate preload loaded via `NODE_OPTIONS`. The old localhost HTTP proxy was
deliberately not exported for this LAN endpoint, per the authorized route
change; no model fallback and no automatic rerun happened. All three completed
a real read-tool round trip with the exact synthetic file content and
`stopReason=stop`.

- Session 1: receipt `smoke-pi-newapi-eb72104e60ca`, implementation commit
  `bf5c564`, exit 0, wall 1.9 s. Sidecar had `observer_ready` and 2 post-header
  `abort_cleanup` records with `failures=0`.
- Session 2: receipt `smoke-pi-newapi-final-bc66325937c6`, commit `268df64`
  (the later `4d8292f` commit is documentation only), exit 0, wall 1.9 s,
  `observer_ready` with no error records.
- Session 3 (repair): receipt `smoke-pi-newapi-repair-a2b8f1279498`, repair
  commit `0e1f437`, exit 0, wall 1.4 s, `observer_ready` with no error records
  and no invalid sidecar line.

Raw stdout/stderr and all three sidecars were preserved privately under the
round checks directories; only the compact summaries above are public. These
sessions prove the candidate preload loads inside the real Pi CLI on this route
and that a successful tool session is not misclassified. They do not prove the
route is permanently stable, and an observed `abort_cleanup` count is not a
failure count.

## Known limits and unknowns

- No proxy behavior was changed globally and no automatic retry, route or model
  switch was added. A proxy `CONNECT 503` or pre-TLS reset can still happen; the
  observer only classifies it.
- Diagnostics are written only by the supervisor's direct Pi child, so an
  inherited tool child's own Node transport is outside the diagnostic scope; it
  still inherits the proxy policy. A manual run without the supervisor identity
  writes nothing.
- The sidecar is observational data of untrusted origin; the reader validates
  it, and a missing, oversized, truncated, non-UTF-8 or forged sidecar is
  unknown/unreadable, never healthy. Unknown event shapes stay `unknown`.
- NewAPI's default zero cost metadata is not evidence of free usage or billing
  equivalence, and Pi usage totals were not compared with any invoice.
- The offline Node probe injects undici diagnostics events and local socket
  faults; it cannot reproduce every provider or gateway failure shape.
- A process exit of zero with a provider error remains an execution failure
  through the existing terminal-evidence path.
- Old tasks without a frozen `network` key keep their inherited environment and
  their frozen helpers; the feature applies to newly started tasks. Enabling it
  for an existing private project requires adding the `network` block to that
  project's own config before starting a new task.
