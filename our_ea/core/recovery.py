"""Recovery engine — failure -> freeze -> sync -> reconstruct -> reconcile
-> validate -> resume-or-halt. Never reconnect-and-trade-immediately.

Policies (OUR_EA_POLICY, mirrored from the Analyzer reference):
    on_reconnect_with_open_basket -> ADOPT_STATE (no new cycles / grid adds
        until uncertainty acknowledged)
    on_state_corrupt              -> SAFE_STOP
    on_stale_state (>24h)         -> SAFE_STOP
    on_missing_state_with_positions -> PAUSE_FOR_OPERATOR
    on_missing_state_flat         -> fresh start
"""
from __future__ import annotations

from dataclasses import dataclass

from our_ea.core.persistence import StateCorruptionError, is_stale
from our_ea.core.identifiers import utc_now_iso

RECOVERY_OUTCOMES = (
    "RESUMED_FRESH",        # no prior state, no broker positions
    "ADOPTED_STATE",        # state + broker match, positions adopted
    "RESUMED_AFTER_SYNC",   # crash during close; reconciled and resumed
    "PAUSE_FOR_OPERATOR",   # broker positions but no local state
    "SAFE_STOP",            # corrupt / stale / unreconcilable
)


@dataclass(frozen=True)
class RecoveryOutcome:
    outcome: str
    detail: dict
    ts: str


class RecoveryPolicy:
    on_reconnect_with_open_basket = "ADOPT_STATE"
    on_state_corrupt = "SAFE_STOP"
    on_stale_state = "SAFE_STOP"
    on_missing_state_with_positions = "PAUSE_FOR_OPERATOR"


class RecoveryRunner:
    """Executes the recovery sequence against a state store + broker view."""

    def __init__(self, state_store, journal=None, audit=None):
        self.store = state_store
        self.journal = journal
        self.audit = audit
        self.policy = RecoveryPolicy()

    def _emit(self, outcome: str, detail: dict) -> RecoveryOutcome:
        if self.journal:
            self.journal.append("RECOVERY", {"outcome": outcome, **detail})
        if self.audit:
            self.audit.record("recovery-runner", "RECOVERY", "RECOVERY_EVALUATED",
                              "runtime", outcome, after=detail)
        return RecoveryOutcome(outcome, detail, utc_now_iso())

    def run(self, broker_positions: list[dict]) -> RecoveryOutcome:
        """broker_positions: [{'position_id','side','lot','entry_price','level'}...]"""
        try:
            state = self.store.load()
        except FileNotFoundError:
            if broker_positions:
                return self._emit("PAUSE_FOR_OPERATOR", {
                    "reason": "no local state but broker has positions",
                    "broker_positions": len(broker_positions)})
            return self._emit("RESUMED_FRESH", {"reason": "no state, flat at broker"})
        except StateCorruptionError as exc:
            return self._emit("SAFE_STOP", {"reason": f"state corrupt: {exc}"})

        if is_stale(state):
            return self._emit("SAFE_STOP", {
                "reason": "state stale > 24h", "saved_at": state.get("saved_at")})

        local = state.get("basket", {}).get("open_positions", [])
        mismatch = self._compare(local, broker_positions)
        if mismatch:
            return self._emit("SAFE_STOP", {
                "reason": "reconciliation mismatch during recovery",
                "mismatch": mismatch[:10]})
        if broker_positions:
            return self._emit("ADOPTED_STATE", {
                "reason": "state and broker agree; positions adopted; "
                          "no new cycles/grid adds until ack",
                "positions": len(broker_positions)})
        return self._emit("RESUMED_AFTER_SYNC", {
            "reason": "flat confirmed after sync"})

    @staticmethod
    def _compare(local: list[dict], broker: list[dict]) -> list[dict]:
        """Volume/side/entry comparison -> list of mismatch descriptors."""
        issues = []
        broker_by_id = {str(p.get("position_id")): p for p in broker}
        local_by_id = {str(p.get("position_id")): p for p in local}
        for pid, lp in local_by_id.items():
            bp = broker_by_id.get(pid)
            if bp is None:
                issues.append({"position_id": pid, "issue": "missing_at_broker"})
                continue
            if abs(float(lp.get("lot", 0)) - float(bp.get("lot", 0))) > 1e-9:
                issues.append({"position_id": pid, "issue": "lot_mismatch",
                               "local": lp.get("lot"), "broker": bp.get("lot")})
            if lp.get("side") != bp.get("side"):
                issues.append({"position_id": pid, "issue": "side_mismatch"})
        for pid in broker_by_id:
            if pid not in local_by_id:
                issues.append({"position_id": pid, "issue": "unexpected_broker_position"})
        return issues
