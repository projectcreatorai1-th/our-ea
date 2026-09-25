"""Execution layer — broker-neutral adapter contract.

OUR EA's strategy runtime never touches a venue directly: it produces
ExecutionIntent objects that a registered adapter executes. First
implementations: Simulation (deterministic), Paper (balance ledger),
MT5Gateway (via SNIPER Gateway bridge; LIVE permanently locked).
"""
from __future__ import annotations

from dataclasses import dataclass, field

MODES = ("SIMULATION", "PAPER", "DEMO", "LIVE")


class LiveLockError(RuntimeError):
    """LIVE is permanently locked until an explicit human decision (INV-007)."""


class ExecutionUnavailable(RuntimeError):
    """Adapter cannot serve the request in this environment."""


@dataclass(frozen=True)
class ExecutionIntent:
    intent_id: str
    action: str                 # OPEN | CLOSE | CLOSE_ALL | CLOSE_PARTIAL
    side: str                   # BUY | SELL | ""
    lot: float
    price: float | None         # None = market
    idempotency_key: str
    cycle_id: str = ""
    decision_id: str = ""
    risk_decision_id: str = ""
    position_id: str = ""       # CLOSE: which position
    exit_buy_price: float = 0.0 # CLOSE_ALL valuation: ask (SELL exits here)
    exit_sell_price: float = 0.0# CLOSE_ALL valuation: bid (BUY exits here)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "intent_id": self.intent_id, "action": self.action, "side": self.side,
            "lot": self.lot, "price": self.price,
            "idempotency_key": self.idempotency_key, "cycle_id": self.cycle_id,
            "decision_id": self.decision_id, "risk_decision_id": self.risk_decision_id,
            "position_id": self.position_id,
            "exit_buy_price": self.exit_buy_price,
            "exit_sell_price": self.exit_sell_price,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExecutionResult:
    intent_id: str
    idempotency_key: str
    status: str                 # FILLED | REJECTED | TIMEOUT | SKIPPED
    filled_lot: float = 0.0
    price: float = 0.0
    position_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "intent_id": self.intent_id, "idempotency_key": self.idempotency_key,
            "status": self.status, "filled_lot": self.filled_lot,
            "price": self.price, "position_id": self.position_id,
            "reason": self.reason,
        }


class ExecutionAdapter:
    """Port: venue-agnostic execution. Implementations must be idempotent on
    idempotency_key (a retried intent returns the original result)."""

    mode: str = "SIMULATION"

    def submit(self, intent: ExecutionIntent) -> ExecutionResult:
        raise NotImplementedError

    def open_positions(self) -> list[dict]:
        raise NotImplementedError

    def close_all(self, intent: ExecutionIntent) -> ExecutionResult:
        raise NotImplementedError

    def account_snapshot(self) -> dict:
        raise NotImplementedError


def resolve_mode(mode: str) -> str:
    from our_ea import LIVE_LOCKED
    m = (mode or "").upper()
    if m not in MODES:
        raise ValueError(f"unknown mode {mode!r}; valid: {MODES}")
    if m == "LIVE":
        # INV-007 — permanently locked; refusal is audited by callers
        raise LiveLockError(
            "LIVE execution permanently locked until explicit human decision")
    return m


def adapter_for(mode: str, **kwargs) -> ExecutionAdapter:
    from our_ea.execution.mt5_gateway import MT5GatewayAdapter
    from our_ea.execution.paper import PaperAdapter
    from our_ea.execution.simulation import SimulationAdapter
    m = resolve_mode(mode)
    if m == "SIMULATION":
        return SimulationAdapter(**kwargs)
    if m == "PAPER":
        return PaperAdapter(**kwargs)
    if m == "DEMO":
        return MT5GatewayAdapter(mode="DEMO", **kwargs)
    raise LiveLockError("unreachable")
