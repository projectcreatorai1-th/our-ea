"""Event journal — append-only, hash-chained, replayable (INV-005, INV-010).

Every runtime action lands here exactly once. Events are immutable; the
journal refuses duplicate event_ids (idempotency) and chains hashes so
tampering is detectable. Exports to JSONL for the Analyzer event feed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

from our_ea.core.identifiers import canonical_hash, utc_now_iso

EVENT_TYPES = (
    "CYCLE_OPEN",
    "ENTRY",
    "GRID_ADD",
    "PARTIAL_CLOSE_INTENT",
    "PARTIAL_CLOSE",
    "BASKET_CLOSE_INTENT",
    "BASKET_CLOSE",
    "CYCLE_END",
    "MODEL_UNCERTAINTY",
    "RISK_BLOCK",
    "ORDER_SUBMIT",
    "ORDER_FILLED",
    "ORDER_REJECTED",
    "DUPLICATE_EVENT",
    "STATE_TRANSITION",
    "TICK_REJECTED",
    "ERROR",
    "SAFE_STOP",
    "RECOVERY",
    "RECONCILIATION",
    "KILL_SWITCH",
    "COMMAND",
    "CHECKPOINT",
    "LIVE_EXECUTION_REQUESTED",
    "SIGNAL_RECEIVED",
    "SIGNAL_ACCEPTED",
    "SIGNAL_REJECTED",
    "SNAPSHOT",
)


@dataclass(frozen=True)
class EAEvent:
    event_id: str
    event_type: str
    ts: str
    run_id: str
    cycle_id: str = ""
    correlation_id: str = ""
    decision_id: str = ""
    payload: dict = field(default_factory=dict)
    sequence: int = 0
    prev_hash: str = ""
    hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class EventJournal:
    """Append-only event ledger with sequence + hash chain."""

    def __init__(self, run_id: str, journal_path: str | None = None):
        self.run_id = run_id
        self.journal_path = journal_path
        self._events: list[EAEvent] = []
        self._seen_ids: set[str] = set()
        self._seq = 0
        self._last_hash = ""

    # ---------------------------------------------------------------- append
    def append(self, event_type: str, payload: dict | None = None,
               cycle_id: str = "", correlation_id: str = "",
               decision_id: str = "") -> EAEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"undeclared event type: {event_type}")
        self._seq += 1
        event_id = f"E{self._seq:08d}"
        if event_id in self._seen_ids:
            raise RuntimeError(f"duplicate event id {event_id}")
        ev = EAEvent(
            event_id=event_id,
            event_type=event_type,
            ts=utc_now_iso(),
            run_id=self.run_id,
            cycle_id=cycle_id,
            correlation_id=correlation_id,
            decision_id=decision_id,
            payload=dict(payload or {}),
            sequence=self._seq,
            prev_hash=self._last_hash,
        )
        h = canonical_hash({k: v for k, v in ev.to_dict().items() if k != "hash"})
        object.__setattr__(ev, "hash", h)
        self._last_hash = h
        self._events.append(ev)
        self._seen_ids.add(event_id)
        self._flush(ev)
        return ev

    def note_duplicate(self, idempotency_key: str, event_type: str) -> EAEvent:
        """Idempotent observation of a duplicate request (never executes twice)."""
        return self.append("DUPLICATE_EVENT", {
            "idempotency_key": idempotency_key, "original_type": event_type,
        })

    def _flush(self, ev: EAEvent) -> None:
        if not self.journal_path:
            return
        os.makedirs(os.path.dirname(self.journal_path) or ".", exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")

    # --------------------------------------------------------------- queries
    def events(self, event_type: str | None = None) -> list[EAEvent]:
        if event_type:
            return [e for e in self._events if e.event_type == event_type]
        return list(self._events)

    def by_id(self, event_id: str) -> EAEvent | None:
        for e in self._events:
            if e.event_id == event_id:
                return e
        return None

    @property
    def last_sequence(self) -> int:
        return self._seq

    @property
    def last_hash(self) -> str:
        return self._last_hash

    def verify_chain(self) -> bool:
        prev = ""
        for e in self._events:
            body = {k: v for k, v in e.to_dict().items() if k != "hash"}
            if canonical_hash(body) != e.hash or e.prev_hash != prev:
                return False
            prev = e.hash
        return True

    def snapshot(self) -> dict:
        return {
            "run_id": self.run_id,
            "sequence": self._seq,
            "last_hash": self._last_hash,
            "event_count": len(self._events),
            "chain_valid": self.verify_chain(),
        }

    def export_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_dict(), ensure_ascii=False)
                         for e in self._events)
