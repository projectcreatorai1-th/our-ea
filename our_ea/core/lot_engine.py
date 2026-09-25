"""Lot engine — VERIFIED lot formula (R-LOT-FLOOR, E012/E028, MC-001).

    Lot(n) = floor(BaseLot * Multiplier^(n-1) / LotStep) * LotStep

Rounding is FLOOR (truncation to lot step), NOT round-half-up.
Observed range L1..L32; L33+ is extrapolation, never observed.
Overflow per OUR_EA_POLICY: clamp to [min_lot, max_lot] (CONFIGURED, not
historical behavior).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LotConfig:
    base_lot: float = 0.10
    multiplier: float = 1.1
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    observed_max_level: int = 32

    def __post_init__(self):
        if self.base_lot <= 0 or self.multiplier <= 0 or self.lot_step <= 0:
            raise ValueError("lot config must be positive")
        if self.min_lot > self.max_lot:
            raise ValueError("min_lot > max_lot")


class LotEngine:
    """Deterministic per-side level -> lot ladder."""

    def __init__(self, config: LotConfig):
        self.config = config

    @classmethod
    def from_parameters(cls, params) -> "LotEngine":
        return cls(LotConfig(
            base_lot=float(params.require("BaseLot")),
            multiplier=float(params.require("LotMultiplier")),
            lot_step=float(params.require("LotStep")),
        ))

    def lot_for_level(self, level: int) -> float:
        if level < 1:
            raise ValueError("level must be >= 1")
        raw = self.config.base_lot * (self.config.multiplier ** (level - 1))
        # FLOOR to lot step — guards float drift with a tiny epsilon
        stepped = (raw + 1e-9) // self.config.lot_step * self.config.lot_step
        stepped = round(stepped, 8)
        if stepped > self.config.max_lot:
            return self.config.max_lot
        if stepped < self.config.min_lot:
            return self.config.min_lot
        return stepped

    def is_observed_level(self, level: int) -> bool:
        return 1 <= level <= self.config.observed_max_level

    def ladder(self, levels: int) -> list[float]:
        return [self.lot_for_level(n) for n in range(1, levels + 1)]

    def validate(self, level: int, lot: float) -> bool:
        """A lot conforms iff it equals the formula output for that level."""
        return abs(lot - self.lot_for_level(level)) < 1e-9
