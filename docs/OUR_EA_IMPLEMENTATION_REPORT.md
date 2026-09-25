# OUR EA IMPLEMENTATION REPORT

Project: `OUR-EA/` · Runtime version 1.0.0 · 2026-09-25
Status: **IMPLEMENTED + TESTED (109/109) for OBSERVATION/PAPER modes;
DEMO = ENVIRONMENT_BLOCKED (no gateway server); LIVE = refused by design.**

## 1. Architecture as built

```
SNIPER Analyzer (frozen artifacts, sha256-verified)
        │  STRATEGY_SPEC/*.json · evidence model · replay dataset
        ▼
OUR EA ── our_ea/spec/repository.py  (only intake path)
        │
        ├─ core engines: lot / grid / basket / partial / state machine / rules
        ├─ risk guard (independent gate) + market-data integrity pipeline
        ├─ runtime/strategy.py  Tick→Validate→Signal→Decision→Risk→Intent→Adapter
        ├─ runtime/service.py   commands · modes · kill switch · health
        ├─ execution/           simulation · paper · MT5-via-gateway (LIVE ✗)
        └─ gateway/             contract v1.0.0 EA side · loopback/file transport
                 │ (EVENT_PUBLISH / EXECUTION_FEEDBACK / SIGNAL_ACCEPT|REJECT)
                 ▼
        1144 Gateway (server NOT deployed → loopback/file bridge in tests)
                 ▼
        MT5 → Broker   (only via gateway bridge; DEMO blocked until server exists)
```

## 2. Existing components reused (no duplication)

| Reused artifact | How |
|---|---|
| Strategy Specification (11 files) | consumed directly, hash-verified |
| Frozen evidence model (16 rules) | consumed directly, hash-pinned `124F0898…` |
| Replay dataset (812 observed cycles) | consumed for conformance, hash-verified `994955EA…` |
| Gateway contract v1.0.0 | mirrored field-for-field (Signal, EventEnvelope, events, feedback) |
| Parameter/state/invariant specs | engines + state machine built FROM the spec data, not hardcoded |
| OS command list (§3.3 master) | RuntimeService command surface implements the same 16 commands |

## 3. New components (this project)

`our_ea/` package (20 modules): spec repository, parameters, rules,
lot/grid/basket/partial engines, spec-driven state machine (17 states /
62 transitions from `state_transition_spec.json`), event journal
(hash-chained), audit log (hash-chained), market-data pipeline, broker
profile, idempotency ledger, risk guard, persistence (atomic+checksummed,
`OUR_EA_PERSISTENCE_V2`), recovery runner, reconciliation engine, replay
conformance, 3 execution adapters, gateway client + 2 transports,
StrategyCore, RuntimeService. Tests: 109 (5 suites). Demo runner with
verifiable artifacts (`runs/demo_1.0.0/`).

## 4. Strategy semantics implemented (all spec-pinned)

- **Entry (VERIFIED E015)**: flat → BUY 0.10 + SELL 0.10 simultaneously.
- **Lot ladder (VERIFIED E012/E028)**: `Lot(n)=floor(0.1·1.1^(n-1)/0.01)·0.01`.
- **Grid (VERIFIED E013/E14)**: BUY adds −5.00 USD, SELL adds +5.00 USD;
  anchor = declared hypothesis (default `H_PREV_ENTRY`), recorded per decision.
- **Basket close (PARTIAL E016/E027)**: hypothesis-configurable
  (`H_GROSS_1_00` default; `H_PER_LOT_0_50/0_85` allowed; `H_GROSS_1_68`
  REJECTED and refuses to load). Per-position close intents → full lineage.
- **Normal resume (VERIFIED E018)**: next tick re-opens after basket close.
- **Partial close (UNKNOWN E025)**: disabled by policy; enabling it emits
  `MODEL_UNCERTAINTY` and never executes (INV-008).
- **Emergency (UNKNOWN E021)**: replaced by OUR_EA_POLICY surveillance —
  max loss $50, max DD 30 %, depth/position/lot caps, consec-failure kill.

## 5. Parameter registry

23 parameters from `parameter_spec.json`; only the 3 spec-declared
configurable ones (`GridStepUSD`, `GridTriggerHypothesis`,
`BasketCloseHypothesis`) accept overrides, range-checked; everything else
immutable (`ParameterViolation`). `UPDATE_CONFIG` command enforces the same
rules and rebuilds engines from effective values.

## 6. Risk (independent gate)

Order of defense per tick: data-quality gate (impossible/inverted/spread/
volatility/stale) → surveillance (loss/DD/failure) → per-intent gate
(lot, spread, positions, depth, total-lot, margin) → idempotency → adapter.
Kill switch engages on operator KILL or surveillance breach; release is
operator-only and audited. Mismatch in reconciliation blocks all new orders
until a MATCH reconcile.

## 7. Execution & OMS semantics

Intents (`OPEN/CLOSE/CLOSE_ALL/CLOSE_PARTIAL`) with idempotency keys;
adapters idempotent per key (retry = original result). Order events journaled
(SUBMIT/FILLED/REJECTED); partial-close intents always SKIPPED. Paper adapter
carries a balance ledger with contract-size-1.0 accounting (validated against
observed deal data: BUY 0.10 @4382.88→4374.45 = −0.84).

## 8. Persistence / recovery / reconciliation

Crash-safe checkpoints every decision (tmp+fsync+checksum+atomic replace);
restore refuses spec-hash drift. Recovery runner implements
freeze→load→sync→compare→resume/adopt/pause/safe-stop. Reconciliation compares
EA vs broker position maps; MISMATCH ⇒ block-new-orders.

## 9. Gateway integration (summary — full report separate)

Contract v1.0.0 mirrored; EA publishes 18 event types + execution feedback;
accepts signals (research input only — never converts to orders) and the 16
OS commands. Transports: Loopback (test), File (JSONL inter-process bridge).
**Real server: ENVIRONMENT-BLOCKED.**

## 10. Monitoring / health

Dimensions: RISK, EXECUTION, RECONCILIATION, GATEWAY, OPERATIONAL →
GREEN/YELLOW/RED (no composite strategy score, per Phase 72). Status exposes
state machine, basket, adapter account, journal/audit chain validity,
gateway session.

## 11. Security

No secrets/endpoints hardcoded (test-enforced); audit trail hash-chained for
every privileged action incl. LIVE refusals; boundary tests forbid analyzer
runtime imports and pin the frozen-model hash.

## 12. Known limitations / unknowns / blocked

See `docs/KNOWN_LIMITATIONS_AND_BLOCKED.md` — headline: gateway server absent
(DEMO/LIVE blocked), partial-close rules UNKNOWN by evidence (never guessed),
emergency mechanism UNKNOWN (policy fallback only), rollback delegated to OS
(EA refuses self-rollback), single-symbol scope (GOLDmicro) per spec.

## 13. Test summary

109/109 pass (unit, safety, execution/gateway, runtime scenarios C/F/G/H/I/J,
conformance vs 812 observed cycles, boundary). Conformance: lot ladder
4451/4451 (100 %), direction 2824/2827 (99.89 %), spacing 2642/2827
(93.4 % in observed band), both-sides 812/812 — verdict PASS. Details in
`docs/TEST_REPORT.md` and `docs/STRATEGY_CONFORMANCE_REPORT.md`.
