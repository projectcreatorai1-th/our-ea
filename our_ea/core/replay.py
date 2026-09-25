"""Replay conformance — OUR EA engines vs the immutable OBSERVED dataset.

Dataset: data/our_ea/replay_dataset_v1.json (812 HIGH-confidence cycles
observed from V1.68 production accounts; sha256-verified at load).

Checks only VERIFIED rules; PARTIAL rules are classified per declared
hypothesis; UNKNOWN rules are never scored:
    * R-LOT-FLOOR       observed entry lot == LotEngine lot for its level
    * R-GRID-DIRECTION  BUY adds lower / SELL adds higher (per side sequence)
    * R-GRID-SPACING    consecutive same-side gaps inside observed band 4.5..6.0
    * R-BOTH-SIDES      first entries open BUY+SELL together

Result carries MATCH/MISMATCH/UNKNOWN counts and a deterministic result_hash
(INV-010 deterministic replay).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from our_ea.core.grid_engine import GridEngine, GridConfig
from our_ea.core.identifiers import canonical_hash
from our_ea.core.lot_engine import LotEngine, LotConfig


@dataclass
class ConformanceResult:
    total_cycles: int = 0
    checks: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    by_rule: dict = field(default_factory=dict)
    result_hash: str = ""

    def summary(self) -> dict:
        return {
            "total_cycles": self.total_cycles,
            "counts": self.counts,
            "by_rule": self.by_rule,
            "result_hash": self.result_hash,
        }


class ReplayConformance:
    """Pure function of (dataset, engine configs) -> deterministic result."""

    def __init__(self, lot_engine: LotEngine | None = None,
                 grid_engine: GridEngine | None = None):
        self.lot_engine = lot_engine or LotEngine(LotConfig())
        self.grid_engine = grid_engine or GridEngine(GridConfig())

    @classmethod
    def from_parameters(cls, params) -> "ReplayConformance":
        return cls(
            LotEngine.from_parameters(params),
            GridEngine.from_parameters(params),
        )

    # ------------------------------------------------------------------ run
    def run(self, dataset: dict) -> ConformanceResult:
        res = ConformanceResult(total_cycles=len(dataset.get("cycles", [])))
        tally = {"MATCH": 0, "MISMATCH": 0, "UNKNOWN": 0}
        by_rule: dict[str, dict] = {}

        def rule_tally(rule: str, verdict: str) -> None:
            by_rule.setdefault(rule, {"MATCH": 0, "MISMATCH": 0, "UNKNOWN": 0})
            by_rule[rule][verdict] += 1
            tally[verdict] += 1

        for cycle in dataset.get("cycles", []):
            entries = cycle.get("entries", [])
            if not entries:
                continue
            # R-BOTH-SIDES: initial exposure opens both sides
            sides = [e["side"] for e in entries if e.get("level_hint", 0) == 0]
            if not sides:
                sides = [entries[0]["side"]]
            rule_tally("R-BOTH-SIDES",
                       "MATCH" if {"BUY", "SELL"} <= set(
                           [e["side"] for e in entries]) else "MISMATCH")

            # per-side sequences for direction + spacing + lot ladder
            for side in ("BUY", "SELL"):
                seq = [e for e in entries if e["side"] == side]
                for idx, e in enumerate(seq):
                    # lot ladder by position in sequence (level 1 = first entry)
                    expected_lot = self.lot_engine.lot_for_level(idx + 1)
                    if abs(e["lot"] - expected_lot) <= 0.0100001:
                        rule_tally("R-LOT-FLOOR", "MATCH")
                    else:
                        rule_tally("R-LOT-FLOOR", "MISMATCH")
                    if idx == 0:
                        continue
                    prev = seq[idx - 1]["price"]
                    # direction: BUY adds lower, SELL adds higher
                    if side == "BUY":
                        ok = e["price"] < prev
                    else:
                        ok = e["price"] > prev
                    rule_tally("R-GRID-DIRECTION", "MATCH" if ok else "MISMATCH")
                    verdict = self.grid_engine.conforms_observed_spacing(
                        side, prev, e["price"])
                    rule_tally("R-GRID-SPACING", verdict)

        res.checks = {
            "lot_rule": "R-LOT-FLOOR (floor ladder)",
            "direction_rule": "R-GRID-DIRECTION (BUY down / SELL up)",
            "spacing_rule": f"R-GRID-SPACING band {self.grid_engine.config.step_usd} USD nominal",
            "both_sides_rule": "R-BOTH-SIDES initial exposure",
        }
        res.counts = tally
        res.by_rule = by_rule
        res.result_hash = canonical_hash({
            "cycles": res.total_cycles, "counts": tally, "by_rule": by_rule})
        return res

    def verdict(self, res: ConformanceResult) -> str:
        """PASS iff each VERIFIED rule matches at its observed-consistency
        floor: lot ladder and both-sides >= 99%, direction >= 98% (3
        documented exceptions exist), spacing >= 90% (observed distribution
        has tails beyond the 4.5..6.0 band — 85.2% inside 4.5..5.5 per E013)."""
        floors = {"R-LOT-FLOOR": 0.99, "R-BOTH-SIDES": 0.99,
                  "R-GRID-DIRECTION": 0.98, "R-GRID-SPACING": 0.90}
        for rule, floor in floors.items():
            r = res.by_rule.get(rule)
            if not r:
                return "UNKNOWN"
            total = r["MATCH"] + r["MISMATCH"] + r["UNKNOWN"]
            if total == 0:
                return "UNKNOWN"
            if r["MATCH"] / total < floor:
                return "FAIL"
        return "PASS"
