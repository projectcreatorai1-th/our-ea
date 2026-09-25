"""Persistence — crash-safe checkpointed runtime state (OUR_EA_PERSISTENCE_V2).

Save = temp file + fsync + checksum envelope + atomic os.replace.
Load verifies schema + checksum; corruption -> StateCorruptionError ->
runtime enters SAFE_STOP (never trades on untrusted state).
"""
from __future__ import annotations

import json
import os

from our_ea.core.identifiers import canonical_hash, parse_ts, utc_now_iso

SCHEMA_VERSION = "OUR_EA_PERSISTENCE_V2"
STALE_AFTER_SEC = 24 * 3600.0


class StateCorruptionError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path: str):
        self.path = path

    def save(self, state: dict) -> None:
        state = dict(state)
        state["schema_version"] = SCHEMA_VERSION
        state["saved_at"] = utc_now_iso()
        state.pop("checksum", None)
        state["checksum"] = canonical_hash(state)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def load(self) -> dict:
        if not os.path.isfile(self.path):
            raise FileNotFoundError(self.path)
        with open(self.path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        if state.get("schema_version") != SCHEMA_VERSION:
            raise StateCorruptionError(
                f"schema {state.get('schema_version')} != {SCHEMA_VERSION}")
        checksum = state.pop("checksum", "")
        if canonical_hash(state) != checksum:
            raise StateCorruptionError("checksum mismatch")
        return state

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def delete(self) -> None:
        if os.path.isfile(self.path):
            os.remove(self.path)


def is_stale(state: dict, now: str = "") -> bool:
    saved = parse_ts(state.get("saved_at", ""))
    now_dt = parse_ts(now or utc_now_iso())
    if saved is None or now_dt is None:
        return True
    return (now_dt - saved).total_seconds() > STALE_AFTER_SEC
