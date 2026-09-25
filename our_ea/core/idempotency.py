"""Idempotency — duplicate requests must never create duplicate orders (INV-005).

Key: account|symbol|cycle_id|event_type|sequence
A retried request with a seen key returns the original result instead of
executing again (Phase 23 / Scenario J).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IdempotencyKey:
    account_id: str
    symbol: str
    cycle_id: str
    event_type: str
    sequence: int

    def build(self) -> str:
        return f"{self.account_id}|{self.symbol}|{self.cycle_id}|{self.event_type}|{self.sequence}"


class IdempotencyLedger:
    def __init__(self):
        self._seen: dict[str, object] = {}

    def check_and_register(self, key: str, result_factory) -> tuple[bool, object]:
        """Returns (is_new, result). Registers result on first sight."""
        if key in self._seen:
            return False, self._seen[key]
        result = result_factory()
        self._seen[key] = result
        return True, result

    def seen(self, key: str) -> bool:
        return key in self._seen

    def size(self) -> int:
        return len(self._seen)
