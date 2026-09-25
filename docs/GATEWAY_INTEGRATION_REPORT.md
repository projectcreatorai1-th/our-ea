# GATEWAY INTEGRATION REPORT — OUR EA side

## Contract consumed

`GATEWAY_CONTRACT_VERSION 1.0.0` / `SIGNAL_SCHEMA_VERSION 1.0.0` /
`EVENT_SCHEMA_VERSION 1.0.0` — mirrored field-for-field from the Analyzer's
`core/gateway/contracts.py` (the ecosystem's contract authority). No new
contract invented; no second gateway built (master rule §98).

## EA-side surface (`our_ea/gateway/`)

- **Handshake**: `CONTRACT_REQUEST → CONTRACT_RESPONSE`; fails closed on
  major.minor mismatch or `accepted=false` (state → ERROR).
- **Heartbeat**: interval 30 s; missed ack → DEGRADED → reconnect path
  re-requests state snapshot.
- **Publish**: `EVENT_PUBLISH` with `EventEnvelope` (event_id, sequence,
  correlation_id, source=OUR-EA, target=1144-GATEWAY). EA may publish 18 of
  the 24 gateway event types (enforced whitelist).
- **Execution feedback**: `EXECUTION_FEEDBACK` (ExecutionFeedback — TCA
  input for the Analyzer: requested/executed price, slippage, spread,
  latency, fill count).
- **Signal intake**: `SIGNAL_PUBLISH` → schema+expiry validation →
  `SIGNAL_ACCEPTED`/`SIGNAL_REJECTED` + journal. **A signal is research
  input; it never becomes an order** (execution authority stays with the EA).
- **Command intake**: 16 OS commands (REGISTER…REQUEST_HEALTH) dispatched to
  RuntimeService, all audited.
- **Idempotency**: inbound dedup by `event_id` / `signal_id`.

## Transports

| Transport | Purpose | Status |
|---|---|---|
| Loopback | in-process contract simulation (tests) | implemented |
| File (JSONL) | inter-process bridge for ecosystem integration tests | implemented |
| WebSocket/HTTP | production transport to the real gateway server | **ENVIRONMENT-BLOCKED** |

## MT5 path (Phase 25–26 rule)

`MT5GatewayAdapter` is the only venue path: requires a gateway bridge
object (contract v1.0.0 send/recv). Without it every operation raises
`ExecutionUnavailable("ENVIRONMENT_BLOCKED…")` — never simulated success.
LIVE raises `LiveLockError` at construction.

## Verification

- `tests/test_execution_and_gateway.py`: handshake, version-mismatch
  fail-closed, heartbeat, publish whitelist, feedback, signal dedup/schema,
  file-transport roundtrip, MT5 adapter env-block.
- `tests/test_runtime_scenarios.py::TestGatewayE2EFlow`: paper run →
  ORDER_SENT / POSITION_OPENED / POSITION_CLOSED reach the gateway with
  correlation ids + sequences; signal intake recorded, no orders from signals.

## Blocked items

Real end-to-end transport (OS ⇄ gateway ⇄ EA ⇄ MT5) awaits deployment of the
1144 Gateway server — no URL/port/auth exists anywhere in the ecosystem
today (confirmed by audit of both repos). See
`docs/KNOWN_LIMITATIONS_AND_BLOCKED.md` BLOCK-1.
