"""Simulation adapter — deterministic fills, no money.

Used by replay/simulation harnesses. Supports failure injection for
recovery and duplicate-protection tests. Idempotent on idempotency_key.
"""
from __future__ import annotations

from our_ea.execution.base import ExecutionIntent, ExecutionResult


class SimulationAdapter:
    mode = "SIMULATION"

    def __init__(self, fail_intents: int = 0, contract_size: float = 1.0):
        self.contract_size = contract_size
        self._fail_remaining = fail_intents
        self._next_pos = 1000
        self._open: dict[str, dict] = {}
        self._results: dict[str, ExecutionResult] = {}

    def submit(self, intent: ExecutionIntent) -> ExecutionResult:
        prior = self._results.get(intent.idempotency_key)
        if prior is not None:
            return prior
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            res = ExecutionResult(intent.intent_id, intent.idempotency_key,
                                  "REJECTED", 0.0, 0.0, "", "injected failure")
            self._results[intent.idempotency_key] = res
            return res
        if intent.action == "OPEN":
            pos_id = f"P{self._next_pos}"
            self._next_pos += 1
            self._open[pos_id] = {
                "position_id": pos_id, "side": intent.side, "lot": intent.lot,
                "entry_price": intent.price or 0.0, "level": 0,
                "cycle_id": intent.cycle_id,
            }
            res = ExecutionResult(intent.intent_id, intent.idempotency_key,
                                  "FILLED", intent.lot, intent.price or 0.0,
                                  pos_id, "simulated fill")
        elif intent.action == "CLOSE":
            self._open.pop(intent.position_id, None)
            res = ExecutionResult(intent.intent_id, intent.idempotency_key,
                                  "FILLED", intent.lot, intent.price or 0.0,
                                  intent.position_id, "closed (sim)")
        elif intent.action == "CLOSE_ALL":
            self._open.clear()
            res = ExecutionResult(intent.intent_id, intent.idempotency_key,
                                  "FILLED", 0.0, intent.exit_buy_price, "",
                                  "all closed (sim)")
        else:
            res = ExecutionResult(intent.intent_id, intent.idempotency_key,
                                  "REJECTED", 0.0, 0.0, "",
                                  f"unsupported action {intent.action}")
        self._results[intent.idempotency_key] = res
        return res

    def open_positions(self) -> list[dict]:
        return list(self._open.values())

    def close_all(self, intent: ExecutionIntent) -> ExecutionResult:
        return self.submit(intent)

    def account_snapshot(self) -> dict:
        return {"mode": self.mode, "open_positions": len(self._open)}
