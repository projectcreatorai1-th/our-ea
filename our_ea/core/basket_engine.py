"""Basket engine — cycle identity, accounting, close trigger (PARTIAL).

Identity (deterministic, restart-stable):
    cycle_id  = "<account>|<symbol>|C%06d"
    basket_id = "<cycle_id>|B"

Trigger (R-BASKET-TRIGGER, E016/E027, PARTIAL): three compatible hypotheses;
H_GROSS_1_68 is REJECTED (90.7% violation, E027) and refuses to load.
The selected hypothesis id is recorded on every close decision.

Accounting (R-CONTRACT-SIZE, E019, VERIFIED): contract_size = 1.0 for
GOLDmicro. P/L for BUY = (price - entry) * lot * contract_size; SELL inverse.
Verified against observed deal data (entry 4382.88 -> close 4374.45,
lot 0.1 -> profit -0.84).
"""
from __future__ import annotations

from dataclasses import dataclass, field

H_GROSS_1_00 = "H_GROSS_1_00"
H_PER_LOT_0_50 = "H_PER_LOT_0_50"
H_PER_LOT_0_85 = "H_PER_LOT_0_85"
COMPATIBLE_BASKET_HYPOTHESES = (H_GROSS_1_00, H_PER_LOT_0_50, H_PER_LOT_0_85)
REJECTED_BASKET_HYPOTHESES = ("H_GROSS_1_68",)

BASKET_STATES = ("OPEN", "ACTIVE", "PARTIAL", "CLOSING", "CLOSED", "RECOVERY")


def make_cycle_id(account_id: str, symbol: str, seq: int) -> str:
    return f"{account_id}|{symbol}|C{seq:06d}"


def make_basket_id(cycle_id: str) -> str:
    return f"{cycle_id}|B"


@dataclass(frozen=True)
class BasketTriggerConfig:
    hypothesis: str = H_GROSS_1_00

    def __post_init__(self):
        if self.hypothesis in REJECTED_BASKET_HYPOTHESES:
            raise ValueError(
                "H_GROSS_1_68 REJECTED as V1.68 behavior (E027: 90.7% violations)")
        if self.hypothesis not in COMPATIBLE_BASKET_HYPOTHESES:
            raise ValueError(f"unknown basket hypothesis {self.hypothesis!r}")


@dataclass
class PositionRef:
    position_id: str
    side: str                  # BUY | SELL
    lot: float
    entry_price: float
    level: int                 # ladder level on its side (1-based)
    opened_at: str = ""
    closed_at: str = ""
    close_price: float = 0.0
    realized_pl: float = 0.0

    def floating_pl(self, price: float, contract_size: float) -> float:
        if self.side == "BUY":
            return round((price - self.entry_price) * self.lot * contract_size, 4)
        return round((self.entry_price - price) * self.lot * contract_size, 4)


class Basket:
    """One trading cycle (grid basket). Identity immutable, state mutable."""

    def __init__(self, cycle_id: str, symbol: str, opened_at: str,
                 contract_size: float = 1.0):
        self.cycle_id = cycle_id
        self.basket_id = make_basket_id(cycle_id)
        self.symbol = symbol
        self.opened_at = opened_at
        self.contract_size = contract_size
        self.state = "OPEN"
        self.positions: list[PositionRef] = []
        self.realized_pl = 0.0
        self.closed_at = ""

    # ------------------------------------------------------------- mutation
    def add(self, position: PositionRef) -> None:
        self.positions.append(position)
        self.state = "ACTIVE"

    def close_position(self, position_id: str, price: float, ts: str) -> float:
        for p in self.positions:
            if p.position_id == position_id and not p.closed_at:
                p.close_price = price
                p.closed_at = ts
                p.realized_pl = p.floating_pl(price, self.contract_size)
                self.realized_pl = round(self.realized_pl + p.realized_pl, 4)
                return p.realized_pl
        raise KeyError(f"open position not found: {position_id}")

    def mark_closed(self, ts: str) -> None:
        self.state = "CLOSED"
        self.closed_at = ts

    # -------------------------------------------------------------- queries
    def open_positions(self) -> list[PositionRef]:
        return [p for p in self.positions if not p.closed_at]

    def side_positions(self, side: str) -> list[PositionRef]:
        return [p for p in self.open_positions() if p.side == side]

    def last_entry_price(self, side: str) -> float:
        pts = self.side_positions(side)
        return pts[-1].entry_price if pts else 0.0

    def side_extreme(self, side: str) -> float:
        """Extreme adverse excursion anchor: min entry for BUY, max for SELL."""
        pts = self.side_positions(side)
        if not pts:
            return 0.0
        prices = [p.entry_price for p in pts]
        return min(prices) if side == "BUY" else max(prices)

    def side_level(self, side: str) -> int:
        return len(self.side_positions(side))

    def total_open_lots(self) -> float:
        return round(sum(p.lot for p in self.open_positions()), 4)

    def is_flat(self) -> bool:
        return not self.open_positions()

    def floating_pl(self, buy_price: float, sell_price: float) -> float:
        """Gross floating P/L over open positions (close-side valuation)."""
        total = 0.0
        for p in self.open_positions():
            px = sell_price if p.side == "BUY" else buy_price
            total += p.floating_pl(px, self.contract_size)
        return round(total, 4)

    def gross_pl(self, buy_price: float, sell_price: float) -> float:
        """Floating + realized inside this cycle (basket trigger domain)."""
        return round(self.floating_pl(buy_price, sell_price) + self.realized_pl, 4)

    def summary(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "basket_id": self.basket_id,
            "state": self.state,
            "open_positions": len(self.open_positions()),
            "buy_level": self.side_level("BUY"),
            "sell_level": self.side_level("SELL"),
            "total_open_lots": self.total_open_lots(),
            "realized_pl": self.realized_pl,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
        }


class BasketAccounting:
    """Valuation helpers separated from basket state (reused by adapters)."""

    def __init__(self, contract_size: float = 1.0):
        self.contract_size = contract_size

    def position_pl(self, side: str, entry: float, exit_: float, lot: float) -> float:
        if side == "BUY":
            return round((exit_ - entry) * lot * self.contract_size, 4)
        return round((entry - exit_) * lot * self.contract_size, 4)


class BasketCloseEngine:
    """Evaluates the PARTIAL basket close trigger under a declared hypothesis."""

    def __init__(self, config: BasketTriggerConfig):
        self.config = config

    @classmethod
    def from_parameters(cls, params) -> "BasketCloseEngine":
        return cls(BasketTriggerConfig(
            hypothesis=str(params.require("BasketCloseHypothesis"))))

    def threshold(self, total_open_lots: float) -> float:
        h = self.config.hypothesis
        if h == H_GROSS_1_00:
            return 1.00
        if h == H_PER_LOT_0_50:
            return round(total_open_lots * 0.50, 4)
        if h == H_PER_LOT_0_85:
            return round(total_open_lots * 0.85, 4)
        raise ValueError(f"hypothesis {h!r} not implemented")

    def evaluate(self, gross: float, total_open_lots: float) -> tuple[bool, float, str]:
        """Returns (should_close, threshold, reason)."""
        thr = self.threshold(total_open_lots)
        if gross >= thr:
            return True, thr, (
                f"basket close: gross {gross:.4f} >= {thr:.4f} "
                f"({self.config.hypothesis}, lots {total_open_lots:.2f})")
        return False, thr, (
            f"basket hold: gross {gross:.4f} < {thr:.4f} "
            f"({self.config.hypothesis}, lots {total_open_lots:.2f})")
