"""Paper adapter — balance ledger, market-valued P/L, no venue.

Fills OPEN at the given price, tracks positions, computes realized P/L on
close with contract_size accounting (E019: GOLDmicro = 1.0). Idempotent on
idempotency_key. Supports configurable slippage for execution-realism tests.
"""
from __future__ import annotations

from our_ea.core.basket_engine import BasketAccounting
from our_ea.execution.base import ExecutionIntent, ExecutionResult


class PaperAdapter:
    mode = "PAPER"

    def __init__(self, balance: float = 500.0, slippage: float = 0.0,
                 contract_size: float = 1.0):
        self.contract_size = contract_size
        self.accounting = BasketAccounting(contract_size)
        self.balance = balance
        self.start_balance = balance
        self.slippage = slippage
        self.realized_total = 0.0
        self._next_pos = 2000
        self._open: dict[str, dict] = {}
        self._results: dict[str, ExecutionResult] = {}

    # ----------------------------------------------------------------- api
    def submit(self, intent: ExecutionIntent) -> ExecutionResult:
        prior = self._results.get(intent.idempotency_key)
        if prior is not None:
            return prior
        handler = {
            "OPEN": self._open_position,
            "CLOSE": self._close_position,
            "CLOSE_ALL": self._close_all,
            "CLOSE_PARTIAL": self._reject_partial,
        }.get(intent.action)
        if handler is None:
            res = ExecutionResult(intent.intent_id, intent.idempotency_key,
                                  "REJECTED", reason=f"unsupported {intent.action}")
        else:
            res = handler(intent)
        self._results[intent.idempotency_key] = res
        return res

    # -------------------------------------------------------------- actions
    def _fill_price(self, side: str, price: float) -> float:
        slip = self.slippage
        if slip <= 0:
            return price
        # adverse slippage: buys fill higher, sells lower
        return round(price + slip if side == "BUY" else price - slip, 2)

    def _open_position(self, intent: ExecutionIntent) -> ExecutionResult:
        if intent.lot <= 0 or intent.price is None or intent.price <= 0:
            return ExecutionResult(
                intent.intent_id, intent.idempotency_key, "REJECTED",
                reason="invalid open parameters")
        pos_id = f"PP{self._next_pos}"
        self._next_pos += 1
        fill = self._fill_price(intent.side, intent.price)
        self._open[pos_id] = {
            "position_id": pos_id, "side": intent.side, "lot": intent.lot,
            "entry_price": fill, "level": 0, "cycle_id": intent.cycle_id,
        }
        return ExecutionResult(intent.intent_id, intent.idempotency_key,
                               "FILLED", intent.lot, fill, pos_id,
                               "paper fill")

    def _close_position(self, intent: ExecutionIntent) -> ExecutionResult:
        pos = self._open.get(intent.position_id)
        if pos is None:
            return ExecutionResult(intent.intent_id, intent.idempotency_key,
                                   "REJECTED",
                                   reason=f"unknown position {intent.position_id}")
        if intent.price is None or intent.price <= 0:
            return ExecutionResult(intent.intent_id, intent.idempotency_key,
                                   "REJECTED", reason="close price required")
        pl = self.accounting.position_pl(pos["side"], pos["entry_price"],
                                         intent.price, intent.lot)
        self.balance = round(self.balance + pl, 4)
        self.realized_total = round(self.realized_total + pl, 4)
        del self._open[pos["position_id"]]
        return ExecutionResult(intent.intent_id, intent.idempotency_key,
                               "FILLED", intent.lot, intent.price,
                               pos["position_id"], f"closed pl={pl:+.4f}")

    def _close_all(self, intent: ExecutionIntent) -> ExecutionResult:
        if intent.exit_buy_price <= 0 or intent.exit_sell_price <= 0:
            return ExecutionResult(intent.intent_id, intent.idempotency_key,
                                   "REJECTED",
                                   reason="exit_buy_price/exit_sell_price required")
        total_pl = 0.0
        for pos in list(self._open.values()):
            exit_px = intent.exit_sell_price if pos["side"] == "BUY" \
                else intent.exit_buy_price
            pl = self.accounting.position_pl(
                pos["side"], pos["entry_price"], exit_px, pos["lot"])
            total_pl = round(total_pl + pl, 4)
            del self._open[pos["position_id"]]
        self.balance = round(self.balance + total_pl, 4)
        self.realized_total = round(self.realized_total + total_pl, 4)
        return ExecutionResult(intent.intent_id, intent.idempotency_key,
                               "FILLED", 0.0, intent.exit_buy_price, "",
                               f"closed all pl={total_pl:+.4f}")

    @staticmethod
    def _reject_partial(intent: ExecutionIntent) -> ExecutionResult:
        return ExecutionResult(
            intent.intent_id, intent.idempotency_key, "SKIPPED",
            reason="partial close UNKNOWN (R-PARTIAL-TRIGGER) — never executed")

    # -------------------------------------------------------------- views
    def open_positions(self) -> list[dict]:
        return list(self._open.values())

    def close_all(self, intent: ExecutionIntent) -> ExecutionResult:
        return self.submit(intent)

    def account_snapshot(self) -> dict:
        return {
            "mode": self.mode,
            "balance": self.balance,
            "start_balance": self.start_balance,
            "realized_total": self.realized_total,
            "open_positions": len(self._open),
        }
