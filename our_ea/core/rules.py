"""Rule registry — read-only view over the frozen V1.68 evidence model.

Canonical rule ids are stable strings derived from the model's rule names
(same canonicalization the Analyzer's core/our_ea reference uses):
    "Lot Formula (floor ladder)"        -> R-LOT-FLOOR
    "Base Lot = 0.10"                   -> R-BASE-LOT
    "Grid Spacing ~= 5 USD"             -> R-GRID-SPACING
    "Grid Direction (BUY down / SELL up)" -> R-GRID-DIRECTION
    "Both-Sides Initial Exposure"       -> R-BOTH-SIDES
    "Contract Size GOLDmicro = 1.0"     -> R-CONTRACT-SIZE
    "Partial Close EXISTS (deal-level)" -> R-PARTIAL-EXISTS
    "Grid Trigger Semantics"            -> R-GRID-TRIGGER
    "Basket Trigger Semantics"          -> R-BASKET-TRIGGER
    "Partial Trigger"                   -> R-PARTIAL-TRIGGER
    "Partial Volume Rule"               -> R-PARTIAL-VOLUME
    "Partial Level Rule"                -> R-PARTIAL-LEVEL
    "Normal Resume (<=2s)"              -> R-NORMAL-RESUME
    "Emergency Mechanism"               -> R-EMERGENCY
    "Restart Recovery"                  -> R-RESTART-RECOVERY
    "AccumulatorTargetUSD = 1.68 (per basket)" -> R-ACCUM-1-68 (REJECTED)

Statuses: VERIFIED / PARTIAL / UNKNOWN / REJECTED (INV-008: UNKNOWN can never
become execution; REJECTED is never implemented as V1.68 behavior).
"""
from __future__ import annotations

from dataclasses import dataclass

_CANONICAL = {
    "lot formula (floor ladder)": "R-LOT-FLOOR",
    "base lot = 0.10": "R-BASE-LOT",
    "grid spacing ~= 5 usd": "R-GRID-SPACING",
    "grid direction (buy down / sell up)": "R-GRID-DIRECTION",
    "both-sides initial exposure": "R-BOTH-SIDES",
    "contract size goldmicro = 1.0": "R-CONTRACT-SIZE",
    "partial close exists (deal-level)": "R-PARTIAL-EXISTS",
    "grid trigger semantics": "R-GRID-TRIGGER",
    "basket trigger semantics": "R-BASKET-TRIGGER",
    "partial trigger": "R-PARTIAL-TRIGGER",
    "partial volume rule": "R-PARTIAL-VOLUME",
    "partial level rule": "R-PARTIAL-LEVEL",
    "normal resume (<=2s)": "R-NORMAL-RESUME",
    "emergency mechanism": "R-EMERGENCY",
    "restart recovery": "R-RESTART-RECOVERY",
    "accumulatortargetusd = 1.68 (per basket)": "R-ACCUM-1-68",
}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    status: str
    candidate: str
    evidence: tuple
    notes: str

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"

    @property
    def unknown(self) -> bool:
        return self.status == "UNKNOWN"

    @property
    def rejected(self) -> bool:
        return self.status == "REJECTED"


@dataclass(frozen=True)
class ModelUncertainty:
    rule_id: str
    what: str
    required_evidence: str
    handling: str = "MODEL_UNCERTAINTY + safe state (never guessed)"


class RuleRegistry:
    """Immutable registry of the 16 frozen rules."""

    def __init__(self, evidence_model: dict):
        self.model_id = evidence_model.get("model_id", "")
        self._rules: dict[str, Rule] = {}
        for raw in evidence_model.get("rules", []):
            name = raw["rule"]
            key = name.strip().lower()
            rule_id = _CANONICAL.get(key, "R-" + key[:24].upper())
            self._rules[rule_id] = Rule(
                rule_id=rule_id,
                name=name,
                status=raw.get("status", "UNKNOWN"),
                candidate=raw.get("candidate", ""),
                evidence=tuple(raw.get("evidence", "").split(";")) if raw.get("evidence") else (),
                notes=raw.get("notes", ""),
            )
        self.uncertainties = self._build_uncertainties()

    @classmethod
    def from_repository(cls, repo) -> "RuleRegistry":
        return cls(repo.evidence_model)

    def _build_uncertainties(self) -> dict[str, ModelUncertainty]:
        req = {
            "R-PARTIAL-TRIGGER": "tick-level data or controlled test",
            "R-PARTIAL-VOLUME": "deal-level requested-vs-filled data",
            "R-EMERGENCY": "observed emergency event or vendor documentation",
            "R-RESTART-RECOVERY": "controlled restart test (TEST_R spec ready)",
        }
        out = {}
        for rid, rule in self._rules.items():
            if rule.status in ("UNKNOWN", "PARTIAL"):
                out[rid] = ModelUncertainty(
                    rule_id=rid, what=rule.name,
                    required_evidence=req.get(rid, "further observed evidence"))
        return out

    # ------------------------------------------------------------------ api
    def get(self, rule_id: str) -> Rule:
        return self._rules[rule_id]

    def all(self) -> list[Rule]:
        return list(self._rules.values())

    def by_status(self, status: str) -> list[Rule]:
        return [r for r in self._rules.values() if r.status == status]

    def require_verified(self, rule_id: str) -> Rule:
        rule = self._rules[rule_id]
        if rule.status != "VERIFIED":
            raise ModelUncertaintyError(rule_id, rule.name,
                                        f"rule status is {rule.status}, not VERIFIED")
        return rule

    def status_summary(self) -> dict:
        out: dict[str, int] = {}
        for r in self._rules.values():
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def assert_frozen_expectations(self) -> None:
        """Pin the frozen-model shape the runtime was built against."""
        s = self.status_summary()
        expected = {"VERIFIED": 8, "PARTIAL": 3, "UNKNOWN": 4, "REJECTED": 1}
        if s != expected:
            raise ModelUncertaintyError(
                "MODEL", "frozen evidence model",
                f"status summary {s} != expected {expected} — model changed, EA must be revalidated")
        if self.get("R-ACCUM-1-68").status != "REJECTED":
            raise ModelUncertaintyError(
                "R-ACCUM-1-68", "AccumulatorTargetUSD",
                "rejected rule no longer rejected — contract violation")


class ModelUncertaintyError(RuntimeError):
    """Raised when runtime reaches behavior that is UNKNOWN in the model."""

    def __init__(self, rule_id: str, what: str, detail: str = ""):
        self.rule_id = rule_id
        self.what = what
        self.detail = detail
        super().__init__(f"MODEL_UNCERTAINTY[{rule_id}] {what}: {detail}")
