"""Shared test harness — locates the analyzer artifacts and builds services."""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from our_ea.core.audit import AuditLog                      # noqa: E402
from our_ea.core.events import EventJournal                 # noqa: E402
from our_ea.core.identifiers import default_analyzer_root   # noqa: E402
from our_ea.core.persistence import StateStore              # noqa: E402
from our_ea.execution.paper import PaperAdapter             # noqa: E402
from our_ea.runtime.strategy import StrategyCore            # noqa: E402
from our_ea.spec.repository import SpecRepository           # noqa: E402


def make_repo() -> SpecRepository:
    return SpecRepository(default_analyzer_root())


def make_core(tmpdir: str, adapter=None, overrides=None,
              execute_enabled: bool = True) -> StrategyCore:
    repo = make_repo()
    repo.verify()
    journal = EventJournal("RUN-TEST", os.path.join(tmpdir, "events.jsonl"))
    audit = AuditLog("test", os.path.join(tmpdir, "audit.jsonl"))
    store = StateStore(os.path.join(tmpdir, "runtime_state.json"))
    adapter = adapter or PaperAdapter(balance=500.0)
    return StrategyCore(repo, journal, audit, adapter, store,
                        account_id="TEST-001", symbol="GOLDmicro",
                        parameter_overrides=overrides,
                        execute_enabled=execute_enabled)


def tmp_dir() -> str:
    return tempfile.mkdtemp(prefix="our_ea_test_")
