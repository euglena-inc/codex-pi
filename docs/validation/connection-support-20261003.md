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

- Implementation commit: `bf5c564908377463e72f090a1f3de3bedb69cb69`.
- Frozen-policy, helper-snapshot and environment wiring: `runtime/pi_core.py`,
  `runtime/pi_task.py`, `runtime/pi_supervisor.py`,
  `runtime/pi_network_diagnostics.mjs`.
- Deterministic entrypoints: `scripts/validate_connection_support.py`,
  `scripts/connection_support_probe.mjs`, `tests/test_connection_support.py`.

## Deterministic evidence

`python3 scripts/validate_connection_support.py` exited 0 on the candidate
(check receipt `validate-connection-support-8da3f0a109bd`, clean tree) with:

- config matrix: 2 accepted shapes, 12 rejected shapes, no rejected value
  echoed;
- synthetic diagnostics-channel injection: all ten safe classes observed, the
  proxy CONNECT `503` status preserved, no raw URL/message fragment in the
  sidecar, `abort_cleanup` carries no failure status;
- real local fault server: connection reset before headers produces exactly one
  pre-header transport failure and the healthy request adds none;
- bound: 1000 injected errors produced 257 JSONL lines including one
  `truncated` marker (≤ 65536 bytes);
- write failure at a directory path: process exit 0, no crash;
- fake-Pi integration module (`tests/test_connection_support.py`, 26 tests)
  passed inside the validator and standalone.

The module covers absent-config compatibility, invalid-config rejection before
spawn without echoing the value, frozen new-task policy versus edited project
settings, legacy tasks without the `network` key, exact child environment
(overridden proxy forms, untouched `NO_PROXY`, unrelated variables and existing
`NODE_OPTIONS`), paths with spaces (percent-encoded `file://` import),
helper-snapshot completeness including the new preload, missing/unreadable
sidecar handling, adversarial secret-bearing error text, bounded output and
disabled-mode silence.

## Real Pi smoke

One bounded session (of the authorized maximum three) ran through the check tool
with the pinned route `newapi/deepseek-flash`, `thinking=max`, the read tool and
the candidate preload loaded via `NODE_OPTIONS`. The old localhost HTTP proxy
was deliberately not exported for this LAN endpoint, per the authorized route
change; no model fallback and no automatic rerun happened.

- Check receipt `smoke-pi-newapi-eb72104e60ca`, candidate head `bf5c564`, clean
  tree, exit 0, wall 1.9 s, 38686 stdout bytes, 1 tool round trip
  (`tool_execution_start`/`end`), final assistant text exactly the synthetic
  file content, `stopReason=stop`.
- Sidecar present with `observer_ready` and 2 post-header records both
  classified `abort_cleanup` with `failures=0`: the ordinary response-stream
  cleanup abort is observed but never counted as a connection failure, exactly
  as designed.
- Raw stdout/stderr and sidecar were preserved privately under this round's
  checks directory; only the compact summary above is public.

This proves the candidate preload loads inside the real Pi CLI on this route and
that a successful tool session is not misclassified. It does not prove the route
is permanently stable, and the observed `abort_cleanup` count is not a failure
count.

## Known limits and unknowns

- No proxy behavior was changed globally and no automatic retry, route or model
  switch was added. A proxy `CONNECT 503` or pre-TLS reset can still happen; the
  observer only classifies it. Real LAN forward authentication and NewAPI
  behavior beyond this one session were not exercised here.
- NewAPI's default zero cost metadata is not evidence of free usage or billing
  equivalence, and Pi usage totals were not compared with any invoice.
- The offline Node probe injects undici diagnostics events and local socket
  faults; it cannot reproduce every provider or gateway failure shape, and an
  unknown event stays `unknown`.
- Diagnostics are observational and never acceptance; a missing sidecar is
  unknown, not healthy. A process exit of zero with a provider error remains an
  execution failure through the existing terminal-evidence path.
- Old tasks without a frozen `network` key keep their inherited environment and
  their frozen helpers; the feature applies to newly started tasks. Enabling it
  for an existing private project requires adding the synthetic-shaped
  `network` block to that project's own config before starting a new task.
