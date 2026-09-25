"""Immutable audit log — WHO/WHAT/WHEN/WHY for every critical action.

Records are hash-chained (prev_hash) append-only. Used for operator
commands, risk kills, mode changes, LIVE refusals, deployments and
reconciliation outcomes. Independent from the event journal.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict

from our_ea.core.identifiers import canonical_hash, utc_now_iso


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    ts: str
    actor: str
    actor_type: str          # OPERATOR | SYSTEM | RISK | GATEWAY | RECOVERY
    action: str
    resource: str
    reason: str
    before: dict
    after: dict
    version: str
    prev_hash: str
    hash: str = ""


class AuditLog:
    def __init__(self, version: str, path: str | None = None):
        self.version = version
        self.path = path
        self._records: list[AuditRecord] = []
        self._last_hash = ""

    def record(self, actor: str, actor_type: str, action: str, resource: str,
               reason: str = "", before: dict | None = None,
               after: dict | None = None) -> AuditRecord:
        rec = AuditRecord(
            audit_id=f"A{len(self._records) + 1:08d}",
            ts=utc_now_iso(),
            actor=actor,
            actor_type=actor_type,
            action=action,
            resource=resource,
            reason=reason,
            before=dict(before or {}),
            after=dict(after or {}),
            version=self.version,
            prev_hash=self._last_hash,
        )
        h = canonical_hash({k: v for k, v in asdict(rec).items() if k != "hash"})
        object.__setattr__(rec, "hash", h)
        self._last_hash = h
        self._records.append(rec)
        self._flush(rec)
        return rec

    def _flush(self, rec: AuditRecord) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    def records(self, action: str | None = None) -> list[AuditRecord]:
        if action:
            return [r for r in self._records if r.action == action]
        return list(self._records)

    def verify_chain(self) -> bool:
        prev = ""
        for r in self._records:
            body = {k: v for k, v in asdict(r).items() if k != "hash"}
            if canonical_hash(body) != r.hash or r.prev_hash != prev:
                return False
            prev = r.hash
        return True

    def snapshot(self) -> dict:
        return {"records": len(self._records), "chain_valid": self.verify_chain()}
