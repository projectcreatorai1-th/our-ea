"""MT5-via-Gateway adapter — the ONLY sanctioned path to a real venue.

Architecture rule: OUR EA never opens a broker connection itself; execution
flows OUR EA -> SNIPER Gateway -> MT5 -> Broker. The 1144 Gateway server is
NOT deployed in this environment, therefore:

    * mode DEMO  -> ExecutionUnavailable until a gateway bridge is connected
                    (honest ENVIRONMENT_BLOCKED, never simulated as success)
    * mode LIVE  -> LiveLockError always (INV-007)

When the Gateway server exists, connect_bridge() accepts a transport-level
bridge object implementing send/recv of gateway contract messages; order
routing then uses ORDER_SENT / EXECUTION_RESULT messages per contract v1.0.0.
"""
from __future__ import annotations

from our_ea.execution.base import (
    ExecutionAdapter, ExecutionIntent, ExecutionResult,
    ExecutionUnavailable, LiveLockError)


class MT5GatewayAdapter(ExecutionAdapter):
    mode = "DEMO"

    def __init__(self, mode: str = "DEMO", symbol: str = "GOLDmicro"):
        if (mode or "").upper() == "LIVE":
            raise LiveLockError(
                "LIVE execution permanently locked until explicit human decision")
        self.mode = (mode or "DEMO").upper()
        self.symbol = symbol
        self.bridge = None

    # ---------------------------------------------------------------- bridge
    def connect_bridge(self, bridge) -> None:
        """Attach a gateway bridge (transport speaking contract v1.0.0)."""
        required = ("send", "recv")
        missing = [m for m in required if not callable(getattr(bridge, m, None))]
        if missing:
            raise ExecutionUnavailable(
                f"bridge missing methods {missing} (gateway contract v1.0.0)")
        self.bridge = bridge

    @property
    def bridge_connected(self) -> bool:
        return self.bridge is not None

    def _require_bridge(self) -> None:
        if self.bridge is None:
            raise ExecutionUnavailable(
                "ENVIRONMENT_BLOCKED: SNIPER Gateway server not deployed; "
                "MT5 execution unavailable without the gateway bridge")

    # ----------------------------------------------------------------- api
    def submit(self, intent: ExecutionIntent) -> ExecutionResult:
        self._require_bridge()
        raise ExecutionUnavailable(
            "gateway bridge present but ORDER routing not enabled in this "
            "environment (1144 Gateway server pending deployment)")

    def open_positions(self) -> list[dict]:
        self._require_bridge()
        return []

    def close_all(self, intent: ExecutionIntent) -> ExecutionResult:
        self._require_bridge()
        raise ExecutionUnavailable("gateway ORDER routing not enabled")

    def account_snapshot(self) -> dict:
        if self.bridge is None:
            return {"mode": self.mode, "mt5": "NOT_CONNECTED",
                    "reason": "ENVIRONMENT_BLOCKED (no gateway server)"}
        return {"mode": self.mode, "mt5": "BRIDGE_ATTACHED",
                "symbol": self.symbol}
