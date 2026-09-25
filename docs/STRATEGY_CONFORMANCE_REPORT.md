# STRATEGY CONFORMANCE REPORT — OUR EA vs observed V1.68 behavior

Method: `our_ea/core/replay.py::ReplayConformance` runs OUR EA's lot/grid
engines over the immutable observed dataset
(`data/our_ea/replay_dataset_v1.json`, 812 HIGH-confidence cycles from 4
production accounts, sha256 `994955EA…` verified at load) and classifies each
checkable point against the VERIFIED rules. Deterministic output hash.

## Results (rule → MATCH / MISMATCH of checkable points)

| Rule | MATCH | MISMATCH | Rate | Evidence expectation (frozen model notes) |
|---|---|---|---|---|
| R-LOT-FLOOR (floor ladder) | 4451 | 0 | **100.00 %** | "4451/4451 clean-cycle checks" — exact match |
| R-GRID-DIRECTION (BUY↓/SELL↑) | 2824 | 3 | **99.89 %** | 100 %/99.82 % observed, 3 documented exceptions |
| R-GRID-SPACING (nominal 5 USD) | 2642 | 185 | **93.45 %** | distribution median 5.09, p5 4.72, p95 5.98; 85.2 % inside 4.5–5.5 (our band 4.5–6.0) |
| R-BOTH-SIDES (initial exposure) | 812 | 0 | **100.00 %** | 99.4 % both-sides in evidence; dataset is HIGH-confidence cycles |

Totals: 10,729 MATCH / 188 MISMATCH / 0 UNKNOWN-fabricated.
Result hash `A08CEDB3E5D9E567…` (deterministic; re-run equality tested).
**Verdict: PASS** (floors: 99 % lot/both-sides, 98 % direction, 90 % spacing).

## Interpretation rules honored

- Only VERIFIED rules are scored as conformance gates.
- PARTIAL rules (grid anchor, basket trigger) are implemented as *declared
  configuration choices* with the hypothesis id recorded on every decision —
  never silently chosen.
- UNKNOWN rules (partial close trigger/volume) are never scored and never
  executed (INV-008); enabling them yields MODEL_UNCERTAINTY events.
- REJECTED rule (H_GROSS_1_68 = \$1.68 fixed basket close) refuses to load.

## Runtime-level conformance evidence (paper demo)

`runs/demo_1.0.0/` (deterministic tick path 4000→3988.5→4003):

- cycle opens BUY 0.10 + SELL 0.10; grid adds BUY L2 0.11, L3 0.12 at
  −5.00 USD steps (journal `GRID_ADD` events with anchor+hypothesis);
- basket closes when gross ≥ hypothesis threshold; per-position close
  lineage journaled; realized balance 500 → 502.256;
- same tick sequence on two independent instances produces identical
  decision digests (Scenario G).

## Analyzer cross-check

The Analyzer's own ReplayEngine reports 77.55 % event-level match on this
dataset (their classification includes PARTIAL/UNKNOWN branches). OUR EA's
conformance numbers are not comparable 1:1 (different unit: rule-level
points vs events) — both are reported honestly; neither fabricates the other.
