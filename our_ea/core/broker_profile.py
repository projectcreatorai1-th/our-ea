"""Broker profile — GOLDmicro constraints, volume normalization, margin.

Broker capability snapshot (Analyzer BROKER_CAPABILITY_SNAPSHOT.json):
min_lot 0.01, max_lot 100.0, lot_step 0.01, tick_size 0.01,
contract_size 1.0 (VERIFIED E019), margin_rate 0.002 (1:500).
"""
from __future__ import annotations

from dataclasses import dataclass

ORDER_VOLUME_INVALID = "ORDER_VOLUME_INVALID"


@dataclass(frozen=True)
class BrokerProfile:
    symbol: str = "GOLDmicro"
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    tick_size: float = 0.01
    contract_size: float = 1.0
    margin_rate: float = 0.002
    allowed_symbols: tuple = ("GOLDmicro",)

    def __post_init__(self):
        if self.min_lot <= 0 or self.lot_step <= 0:
            raise ValueError("broker profile lots must be positive")

    @classmethod
    def from_parameters(cls, params) -> "BrokerProfile":
        return cls(contract_size=float(params.require("ContractSize")))

    def symbol_allowed(self, symbol: str) -> bool:
        return symbol in self.allowed_symbols

    def normalize_volume(self, lot: float) -> float:
        if lot <= 0:
            raise ValueError(f"{ORDER_VOLUME_INVALID}: lot {lot} <= 0")
        if lot > self.max_lot:
            raise ValueError(
                f"{ORDER_VOLUME_INVALID}: lot {lot} > max {self.max_lot}")
        stepped = (lot + 1e-9) // self.lot_step * self.lot_step
        stepped = round(stepped, 8)
        if stepped < self.min_lot:
            raise ValueError(
                f"{ORDER_VOLUME_INVALID}: lot {lot} < min {self.min_lot}")
        return stepped

    def margin_for(self, lot: float, price: float) -> float:
        return round(lot * price * self.contract_size * self.margin_rate, 4)
