# ANALYZER INTEGRATION REPORT (closed-loop research ↔ runtime)

## What flows Analyzer → OUR EA (research → runtime)

Frozen artifacts, hash-verified at every startup by `our_ea/spec/repository`:

1. `STRATEGY_SPEC/strategy_spec.json` — master contract (three-way hash
   consistency: spec ⇄ spec_version ⇄ ea_build_contract).
2. `STRATEGY_SPEC/parameter_spec.json` — 23 parameters (SSOT; 3 configurable).
3. `STRATEGY_SPEC/state_transition_spec.json` — 17 states / 62 transitions
   (the EA state machine is DATA-DRIVEN from this file).
4. `STRATEGY_SPEC/invariant_spec.json` — INV-001..010 enforced in tests.
5. `STRATEGY_SPEC/ea_build_contract.json` — build gate; all 13 required
   modules implemented.
6. `data/evidence_model/V1.68-EVIDENCE-MODEL-v1.0.json` — 16 frozen rules
   (hash-pinned forever).
7. `data/our_ea/replay_dataset_v1.json` — 812 observed cycles for
   conformance (hash-verified).

No Analyzer runtime code is imported (boundary enforced by test; the frozen
model JSON is the only sanctioned artifact, per the Analyzer's own rules).

## What flows OUR EA → Analyzer (runtime → research)

Through the gateway contract (loopback/file today, server when deployed):

- Event envelopes for the full decision→order→execution→position chain
  (18 publishable gateway event types), each with correlation_id + sequence.
- `ExecutionFeedback` per fill — requested/executed price, slippage,
  spread, latency, fill count → Analyzer TCA/drift planes.
- `SIGNAL_ACCEPTED`/`SIGNAL_REJECTED` lifecycle events for research signals.

The EA's journal (`events.jsonl`, hash-chained) + audit (`audit.jsonl`) are
exportable Analyzer inputs — deterministic replay digest available
(`StrategyCore.decision_digest()`).

## Governance loop position

OUR EA implements the bottom of the closed loop:
LIVE → OBSERVE → (events feed) → ANALYZER ANALYZES → PROPOSES → OS
APPROVES → NEW SPEC VERSION → redeploy. The EA never self-modifies:
spec updates arrive as re-verified artifacts (a spec whose MAJOR version
changed refuses to boot until the EA is revalidated — semver rule from the
contract). Rollback is refused locally and delegated to the OS.

## Incident log (integration proof)

During this build session the Analyzer regenerated `STRATEGY_SPEC/*.json`
(provenance-only change). OUR EA failed closed until the contract's own
re-pinned hash was picked up through the three-way consistency check —
live evidence that the frozen-artifact gate works in both directions.
