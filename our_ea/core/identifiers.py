"""Identity, hashing and time helpers shared by all OUR EA modules.

Conventions:
    * Internal event ids: E%08d sequence per journal (deterministic, replayable).
    * Cross-system ids: <PREFIX>-<12 hex upper> aligned with the SNIPER gateway
      contract (SIG-, COR-, EXE-, DEC-...).
    * Canonical hash: sha256 over sorted-key compact JSON (same scheme the
      Analyzer uses for strategy_spec.json — verified against pinned hash
      5C0E69375A03D1BD587D77FF779AEDFD97D65E0D2825D96D20D86AAA457EB3D9).
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

ID_PREFIXES = {
    "signal": "SIG",
    "correlation": "COR",
    "execution": "EXE",
    "decision": "DEC",
    "order": "ORD",
    "risk": "RSK",
    "run": "RUN",
    "session": "SESS",
    "request": "REQ",
    "trace": "TRC",
    "incident": "INC",
    "checkpoint": "CKP",
    "recovery": "REC",
}


def new_id(kind: str) -> str:
    prefix = ID_PREFIXES.get(kind, kind.upper()[:3])
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def canonical_hash(data) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode()).hexdigest().upper()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_ts(ts: str):
    """Parse ISO timestamp (tolerates trailing 'Z'). Returns aware datetime or None."""
    if not ts:
        return None
    try:
        t = ts.strip()
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def default_analyzer_root() -> str:
    """Locate the SNIPER-CashFlow-Analyzer project (frozen artifact source)."""
    env = os.environ.get("SNIPER_ANALYZER_ROOT")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.dirname(os.path.dirname(  # OUR-EA/ root
        os.path.abspath(__file__))))
    return os.path.abspath(os.path.join(here, "..", "SNIPER-CashFlow-Analyzer"))
