# RECOVERY VALIDATION REPORT

## Sequence implemented (Phase 42 — never reconnect-and-trade)

Failure → freeze new orders → load checkpoint → sync broker → compare →
`RESUMED_FRESH | ADOPTED_STATE | RESUMED_AFTER_SYNC | PAUSE_FOR_OPERATOR |
SAFE_STOP`.

## Policies (OUR_EA_POLICY, mirrored from the analyzer reference)

| Situation | Outcome |
|---|---|
| no state, broker flat | RESUMED_FRESH |
| state + broker agree, positions open | ADOPTED_STATE — no new cycles/grid adds until acknowledged |
| broker positions, no local state | PAUSE_FOR_OPERATOR (never adopt blindly) |
| state corrupt (checksum/schema) | SAFE_STOP |
| state stale > 24 h | SAFE_STOP |
| reconciliation mismatch during recovery | SAFE_STOP |

## Crash-safety mechanics

Checkpoints on every decision: temp file → fsync → checksum envelope →
atomic `os.replace`. Load verifies schema (`OUR_EA_PERSISTENCE_V2`) +
checksum; any tamper → `StateCorruptionError` → SAFE_STOP. Restore refuses
persisted spec hashes that differ from the pinned contract. Restart never
re-mints the cycle id (identity is `account|symbol|C%06d` from the
checkpoint).

## Scenario coverage (tests)

- persistence roundtrip; tamper detection; schema-mismatch detection;
  staleness window.
- recovery outcomes: all six branches exercised
  (`tests/test_safety_layers.py::TestRecovery`).
- Scenario F (restart restores cycle state: state machine position,
  cycle_seq, basket identity, RECOVERY journal event).
- MT5GatewayAdapter without bridge refuses (ENVIRONMENT_BLOCKED) — recovery
  from broker disconnects honestly reports NOT_CONNECTED rather than
  pretending a resync.

## Blocked / not claimable here

Real broker-side resync (MT5 order/deal history pull) requires the gateway
bridge — currently blocked with the gateway server (BLOCK-1). The recovery
runner's broker view is fed by the adapter; with the paper/simulation
adapters this is exercised end-to-end, with MT5 it is env-blocked.
