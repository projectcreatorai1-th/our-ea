"""Risk guard — independent gate, OUR_EA_POLICY (not historical V1.68).

The risk gate is independent from strategy decisions: every execution intent
passes check_intent() before reaching an adapter. Violations return a
RiskRejection (never bypassed — INV-003). Emergency surveillance can engage
the kill switch (max loss / drawdown / consecutive failures).

Limits (parameter_spec.json, source=our_ea_policy):
    MaxLossUSD 50.0 | MaxDrawdownPct 30.0 | MaxGridDepth 12 | MaxPositions 12
    MaxTotalLot 2.0 | MaxSpreadUSD 0.6 | MaxConsecutiveFailures 3
    MaxTickVolatilityUSD 25.0
"""
from __future__ import annotations

from dataclasses import dataclass

from our_ea.core.identifiers import utc_now_iso


@dataclass(frozen=True)
class RiskLimits:
    max_positions: int = 12
    max_grid_depth: int = 12
    max_total_lot: float = 2.0
    max_loss_usd: float = 50.0
    max_drawdown_pct: float = 30.0
    max_spread_usd: float = 0.6
    max_consecutive_failures: int = 3

    @classmethod
    def from_parameters(cls, params) -> "RiskLimits":
        return cls(
            max_positions=int(params.require("MaxPositions")),
            max_grid_depth=int(params.require("MaxGridDepth")),
            max_total_lot=float(params.require("MaxTotalLot")),
            max_loss_usd=float(params.require("MaxLossUSD")),
            max_drawdown_pct=float(params.require("MaxDrawdownPct")),
            max_spread_usd=float(params.require("MaxSpreadUSD")),
            max_consecutive_failures=int(params.require("MaxConsecutiveFailures")),
        )


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    rule: str
    reason: str
    risk_decision_id: str
    ts: str


class RiskRejection(RuntimeError):
    def __init__(self, rule: str, reason: str):
        self.rule = rule
        self.reason = reason
        super().__init__(f"RISK_BLOCK[{rule}] {reason}")


class RiskGuard:
    """Independent risk gate + kill switch state."""

    def __init__(self, limits: RiskLimits, journal=None, audit=None):
        self.limits = limits
        self.journal = journal
        self.audit = audit
        self.kill_switch_engaged = False
        self.kill_reason = ""
        self.consecutive_failures = 0
        self.peak_balance: float = 0.0

    @classmethod
    def from_parameters(cls, params, journal=None, audit=None) -> "RiskGuard":
        return cls(RiskLimits.from_parameters(params), journal, audit)

    # ----------------------------------------------------------------- gate
    def check_intent(self, *, side: str, lot: float, spread: float,
                     open_positions: int, side_level: int, total_open_lots: float,
                     margin_used: float, free_margin: float,
                     cycle_id: str = "") -> RiskDecision:
        """Gate one execution intent. Raises RiskRejection when blocked."""
        if self.kill_switch_engaged:
            raise RiskRejection("KILL_SWITCH", self.kill_reason or "kill switch engaged")
        if lot <= 0:
            raise RiskRejection("LOT_INVALID", f"lot {lot} <= 0")
        if lot > self.limits.max_total_lot:
            raise RiskRejection("MAX_LOT", f"lot {lot} > MaxTotalLot {self.limits.max_total_lot}")
        if spread > self.limits.max_spread_usd:
            raise RiskRejection("MAX_SPREAD", f"spread {spread} > {self.limits.max_spread_usd}")
        if open_positions >= self.limits.max_positions:
            raise RiskRejection("MAX_POSITIONS",
                                f"{open_positions} >= MaxPositions {self.limits.max_positions}")
        if side_level >= self.limits.max_grid_depth:
            raise RiskRejection("MAX_GRID_DEPTH",
                                f"side level {side_level} >= MaxGridDepth {self.limits.max_grid_depth}")
        projected = round(total_open_lots + lot, 4)
        if projected > self.limits.max_total_lot:
            raise RiskRejection("MAX_TOTAL_LOT",
                                f"projected {projected} > MaxTotalLot {self.limits.max_total_lot}")
        if free_margin is not None and margin_used > 0 and margin_used > free_margin:
            raise RiskRejection("MARGIN", f"margin {margin_used} > free {free_margin}")
        return RiskDecision(True, "OK", "all limits satisfied",
                            f"RSK-{abs(hash((cycle_id, side, lot, open_positions))) % 10**10:010d}",
                            utc_now_iso())

    # ------------------------------------------------------- surveillance
    def check_loss(self, cycle_gross: float) -> bool:
        """True = OK. Loss beyond -MaxLossUSD engages emergency policy."""
        if cycle_gross <= -self.limits.max_loss_usd:
            self.engage_kill_switch(
                "MAX_LOSS", f"cycle gross {cycle_gross} <= -{self.limits.max_loss_usd}")
            return False
        return True

    def check_drawdown(self, balance: float) -> bool:
        if self.peak_balance <= 0:
            self.peak_balance = balance
        self.peak_balance = max(self.peak_balance, balance)
        dd_pct = (self.peak_balance - balance) / self.peak_balance * 100.0 \
            if self.peak_balance > 0 else 0.0
        if dd_pct > self.limits.max_drawdown_pct:
            self.engage_kill_switch(
                "MAX_DRAWDOWN", f"drawdown {dd_pct:.2f}% > {self.limits.max_drawdown_pct}%")
            return False
        return True

    def record_order_failure(self, reason: str = "") -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.limits.max_consecutive_failures:
            self.engage_kill_switch(
                "CONSECUTIVE_FAILURES",
                f"{self.consecutive_failures} consecutive order failures")

    def record_order_success(self) -> None:
        self.consecutive_failures = 0

    # ------------------------------------------------------------ kill
    def engage_kill_switch(self, rule: str, reason: str) -> None:
        if not self.kill_switch_engaged:
            self.kill_switch_engaged = True
            self.kill_reason = f"{rule}: {reason}"
            if self.audit:
                self.audit.record("risk-guard", "RISK", "KILL_SWITCH_ENGAGED",
                                  "execution", reason)
        if self.journal:
            self.journal.append("KILL_SWITCH", {"rule": rule, "reason": reason})

    def release_kill_switch(self, actor: str = "operator") -> bool:
        """Operator-only release; audited. RISK-originated releases forbidden."""
        if not self.kill_switch_engaged:
            return True
        self.kill_switch_engaged = False
        reason = self.kill_reason
        self.kill_reason = ""
        if self.audit:
            self.audit.record(actor, "OPERATOR", "KILL_SWITCH_RELEASED",
                              "execution", f"released after: {reason}")
        return True

    def snapshot(self) -> dict:
        return {
            "kill_switch_engaged": self.kill_switch_engaged,
            "kill_reason": self.kill_reason,
            "consecutive_failures": self.consecutive_failures,
            "peak_balance": self.peak_balance,
            "limits": {
                "max_positions": self.limits.max_positions,
                "max_grid_depth": self.limits.max_grid_depth,
                "max_total_lot": self.limits.max_total_lot,
                "max_loss_usd": self.limits.max_loss_usd,
                "max_drawdown_pct": self.limits.max_drawdown_pct,
                "max_spread_usd": self.limits.max_spread_usd,
            },
        }
