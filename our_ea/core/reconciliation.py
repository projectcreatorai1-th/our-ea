"""Reconciliation — OUR EA view vs broker view (Phase 40).

Outcomes per scope: MATCH | MISMATCH | UNKNOWN.
Any MISMATCH blocks new orders until resolved (block_new_orders=True).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from our_ea.core.identifiers import utc_now_iso


@dataclass
class ReconciliationResult:
    run_id: str
    ts: str
    status: str                      # MATCH | MISMATCH | UNKNOWN
    scopes: dict = field(default_factory=dict)
    differences: list = field(default_factory=list)
    block_new_orders: bool = False

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "ts": self.ts, "status": self.status,
            "scopes": self.scopes, "differences": self.differences,
            "block_new_orders": self.block_new_orders,
        }


class ReconciliationEngine:
    def __init__(self, journal=None):
        self.journal = journal

    def reconcile(self, ea_positions: list[dict], broker_positions: list[dict],
                  run_id: str = "") -> ReconciliationResult:
        result = ReconciliationResult(run_id=run_id or "-", ts=utc_now_iso(),
                                      status="MATCH")
        ea_by_id = {str(p.get("position_id")): p for p in ea_positions}
        br_by_id = {str(p.get("position_id")): p for p in broker_positions}

        for pid, ep in ea_by_id.items():
            bp = br_by_id.get(pid)
            if bp is None:
                self._diff(result, pid, "POSITION_MISSING_AT_BROKER",
                           local=ep.get("lot"), broker=None)
                continue
            if abs(float(ep.get("lot", 0)) - float(bp.get("lot", 0))) > 1e-9:
                self._diff(result, pid, "VOLUME_MISMATCH",
                           local=ep.get("lot"), broker=bp.get("lot"))
            if ep.get("side") != bp.get("side"):
                self._diff(result, pid, "SIDE_MISMATCH",
                           local=ep.get("side"), broker=bp.get("side"))
            if abs(float(ep.get("entry_price", 0)) - float(bp.get("entry_price", 0))) > 1e-6:
                self._diff(result, pid, "PRICE_MISMATCH",
                           local=ep.get("entry_price"), broker=bp.get("entry_price"))

        for pid in br_by_id:
            if pid not in ea_by_id:
                self._diff(result, pid, "UNEXPECTED_BROKER_POSITION", local=None,
                           broker=br_by_id[pid].get("lot"))

        if result.differences:
            result.status = "MISMATCH"
            result.block_new_orders = True

        result.scopes = {
            "positions": {"ea": len(ea_by_id), "broker": len(br_by_id)},
            "differences": len(result.differences),
        }
        if self.journal:
            self.journal.append("RECONCILIATION", result.to_dict())
        return result

    @staticmethod
    def _diff(result: ReconciliationResult, pid: str, issue: str,
              local=None, broker=None) -> None:
        result.differences.append({
            "position_id": pid, "issue": issue,
            "ea": local, "broker": broker,
        })
