# TEST REPORT — OUR EA 1.0.0 (2026-09-25)

Runner: `python run_tests.py` (stdlib unittest, zero dependencies).

## Totals (master-spec §105 format)

| Metric | Before | After | Delta |
|---|---|---|---|
| TOTAL | 0 (project did not exist) | **109** | +109 |
| PASS | — | **109** | +109 |
| FAIL | — | **0** | 0 |
| BLOCKED (env) | — | 0 in-suite; 2 gates blocked at integration level (BLOCK-1/2) | — |
| UNKNOWN | — | 0 | 0 |
| REGRESSION (sibling repos) | Analyzer main suite: 1 pre-existing failure (their `app.py` baseline audit, unrelated — their repo untouched by this session, git-verified) | same | 0 new |

## Suite breakdown

| Suite file | Tests | Covers |
|---|---|---|
| `test_spec_and_engines.py` | 21 | spec repo hash pinning, parameter registry (configurable/ranges), lot floor ladder (incl. L32 float guard), grid direction/spacing/anchor hypotheses, basket accounting vs observed deal, rejected hypothesis refusal, frozen rule shape, state machine (declared/undeclared/safe-stop) |
| `test_safety_layers.py` | 26 | risk gate per-limit blocks, kill switch triggers (loss/DD/failures), idempotency, market-data integrity (6 rejection classes), broker volume/margin, persistence (roundtrip/tamper/schema/staleness), recovery 6 branches, reconciliation match/mismatch/unexpected, journal+audit hash chains |
| `test_execution_and_gateway.py` | 25 | paper adapter (P/L, idempotency, close-all side pricing, partial always SKIPPED, slippage), simulation failure injection, LIVE lock (3 paths), MT5 adapter env-block + bridge contract, gateway handshake/version fail-closed/heartbeat/publish whitelist/feedback, signal dedup+schema, file transport |
| `test_runtime_scenarios.py` | 26 | **Scenario C** decision chain in journal; **E2E paper run** (cycle close + immediate re-entry E018 + lot ladder L2/L3 + OBSERVATION no-orders); **Scenario F** restart restore; **Scenario G** deterministic replay; **Scenario H** kill blocks (+ surveillance short-circuit); **Scenario I** risk reject no-order (depth cap, spread at both layers); **Scenario J** duplicate protection; command surface (PAUSE/RESUME/HALT/STATUS/HEALTH/RECONCILE block-clear/UPDATE_CONFIG/DEPLOY/ROLLBACK-refusal); mode ladder + LIVE refusal audited; gateway E2E event flow; signal intake research-only |
| `test_conformance_and_boundary.py` | 11 | observed-dataset conformance (4 rules, thresholds), deterministic result hash, verdict PASS; boundary: no analyzer runtime imports, no hardcoded secrets, LIVE lock constant, frozen-model hash pins, tampered-spec fail-closed |

## Critical-test evidence pointers

- Demo paper run artifacts: `runs/demo_1.0.0/` — `events.jsonl` (57 events,
  chain valid), `audit.jsonl` (chain valid), `runtime_state.json`
  (checksummed checkpoint), `deployment_manifest.json` (sealed),
  `summary.json` (health all GREEN, balance 500 → 502.256).
- Conformance result hash `A08CEDB3E5D9E567…` (re-run stable).
