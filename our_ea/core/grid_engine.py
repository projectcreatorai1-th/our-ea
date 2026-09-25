"""Grid engine — VERIFIED direction + spacing, PARTIAL trigger anchor.

Direction (R-GRID-DIRECTION, E014, VERIFIED):
    BUY  side adds LOWER (100.00% observed)
    SELL side adds HIGHER (99.82% observed, 3 documented exceptions)

Spacing (R-GRID-SPACING, E013, VERIFIED):
    nominal 5.0 USD; observed distribution median 5.09, p5 4.72, p95 5.98.
    The engine uses the nominal step with an observation band for conformance
    checks — it does NOT pretend the historical value was exactly 5.0000.

Trigger anchor (R-GRID-TRIGGER, E026, PARTIAL):
    H_PREV_ENTRY vs H_EXTREME are mathematically indistinguishable on
    averaging-down ladders. Selection is a declared configuration choice
    (PARAM-GRID-ANCHOR); the chosen hypothesis id MUST be recorded on every
    grid decision (no silent hypothesis).
"""
from __future__ import annotations

from dataclasses import dataclass

H_PREV_ENTRY = "H_PREV_ENTRY"
H_EXTREME = "H_EXTREME"
COMPATIBLE_ANCHORS = (H_PREV_ENTRY, H_EXTREME)

# Observed spacing band (p5..p95 with margin) used only for conformance
# classification against observed data — never for execution decisions.
SPACING_BAND = (4.5, 6.0)


@dataclass(frozen=True)
class GridConfig:
    step_usd: float = 5.0
    anchor: str = H_PREV_ENTRY
    symbol: str = "GOLDmicro"

    def __post_init__(self):
        if self.anchor not in COMPATIBLE_ANCHORS:
            raise ValueError(
                f"anchor {self.anchor!r} not in compatible {COMPATIBLE_ANCHORS}")
        if not (1.0 <= self.step_usd <= 50.0):
            raise ValueError("GridStepUSD outside allowed range 1.0..50.0")


@dataclass(frozen=True)
class GridDecision:
    side: str                 # BUY | SELL
    action: str               # ADD | HOLD
    trigger_price: float      # price threshold that fired (0.0 on HOLD)
    anchor_price: float       # anchor the threshold was computed from
    anchor_hypothesis: str
    gap_usd: float            # distance from anchor (0.0 on HOLD)
    reason: str


class GridEngine:
    """Per-side grid trigger evaluation."""

    def __init__(self, config: GridConfig):
        self.config = config

    @classmethod
    def from_parameters(cls, params) -> "GridEngine":
        return cls(GridConfig(
            step_usd=float(params.require("GridStepUSD")),
            anchor=str(params.require("GridTriggerHypothesis")),
        ))

    def anchor_price(self, side: str, last_entry_price: float,
                     side_extreme: float) -> float:
        """Anchor per declared hypothesis (H_PREV_ENTRY default)."""
        if self.config.anchor == H_EXTREME:
            return side_extreme if side_extreme > 0.0 else last_entry_price
        return last_entry_price

    def next_add_price(self, side: str, anchor: float) -> float:
        """BUY adds `step` BELOW anchor; SELL adds `step` ABOVE anchor."""
        if side == "BUY":
            return round(anchor - self.config.step_usd, 2)
        if side == "SELL":
            return round(anchor + self.config.step_usd, 2)
        raise ValueError(f"invalid side {side!r}")

    def evaluate(self, side: str, price: float, last_entry_price: float,
                 side_extreme: float) -> GridDecision:
        """Fire when price crosses the next add threshold for the side."""
        anchor = self.anchor_price(side, last_entry_price, side_extreme)
        threshold = self.next_add_price(side, anchor)
        if side == "BUY" and price <= threshold:
            return GridDecision(side, "ADD", threshold, anchor,
                                self.config.anchor,
                                round(anchor - price, 2),
                                f"BUY grid add: price {price} <= {threshold} "
                                f"(anchor {anchor}, {self.config.anchor})")
        if side == "SELL" and price >= threshold:
            return GridDecision(side, "ADD", threshold, anchor,
                                self.config.anchor,
                                round(price - anchor, 2),
                                f"SELL grid add: price {price} >= {threshold} "
                                f"(anchor {anchor}, {self.config.anchor})")
        return GridDecision(side, "HOLD", 0.0, anchor, self.config.anchor,
                            0.0, f"{side} grid hold (threshold {threshold})")

    @staticmethod
    def conforms_observed_spacing(side: str, prev_price: float,
                                  new_price: float) -> str:
        """Classify an OBSERVED consecutive-entry gap (conformance only)."""
        gap = abs(new_price - prev_price)
        if gap < SPACING_BAND[0]:
            return "MISMATCH"
        if gap > SPACING_BAND[1]:
            return "MISMATCH"
        return "MATCH"
