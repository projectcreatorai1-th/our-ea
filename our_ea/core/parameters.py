"""Parameter set — SSOT loaded from STRATEGY_SPEC/parameter_spec.json.

Rules:
    * Non-configurable parameters are immutable; overrides raise
      ParameterViolation (no hard-coded strategy values, INV-003 family).
    * Configurable parameters accept overrides only inside allowed_range
      (currently GridStepUSD, GridTriggerHypothesis, BasketCloseHypothesis).
"""
from __future__ import annotations

from dataclasses import dataclass


class ParameterViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class Parameter:
    parameter_id: str
    name: str
    type: str
    unit: str
    value: object
    status: str
    source: str
    evidence_refs: tuple
    configurable: bool
    allowed_range: object

    @property
    def is_verified(self) -> bool:
        return self.status == "VERIFIED"


class ParameterSet:
    """Typed read-only view over parameter_spec.json with validated overrides."""

    def __init__(self, spec: dict, overrides: dict | None = None):
        self._by_id: dict[str, Parameter] = {}
        self._by_name: dict[str, Parameter] = {}
        for raw in spec.get("parameters", []):
            p = Parameter(
                parameter_id=raw["parameter_id"],
                name=raw["name"],
                type=raw["type"],
                unit=raw.get("unit", ""),
                value=raw["value"],
                status=raw.get("status", "UNKNOWN"),
                source=raw.get("source", ""),
                evidence_refs=tuple(raw.get("evidence_refs", [])),
                configurable=bool(raw.get("configurable", False)),
                allowed_range=raw.get("allowed_range"),
            )
            self._by_id[p.parameter_id] = p
            self._by_name[p.name] = p
        self._values = {p.parameter_id: p.value for p in self._by_id.values()}
        self.apply_overrides(overrides or {})

    # ------------------------------------------------------------------ core
    def apply_overrides(self, overrides: dict) -> None:
        for name, value in overrides.items():
            p = self._by_name.get(name)
            if p is None:
                raise ParameterViolation(f"unknown parameter: {name}")
            if not p.configurable:
                raise ParameterViolation(
                    f"parameter {name} is not configurable (status={p.status})")
            self._validate_range(p, value)
            self._values[p.parameter_id] = value

    @staticmethod
    def _validate_range(p: Parameter, value) -> None:
        rng = p.allowed_range
        if rng is None:
            return
        if isinstance(rng, list):  # enum
            if value not in rng:
                raise ParameterViolation(
                    f"{p.name}: {value!r} not in allowed values {rng}")
            return
        lo = rng.get("min")
        hi = rng.get("max")
        if lo is not None and value < lo:
            raise ParameterViolation(f"{p.name}: {value} < min {lo}")
        if hi is not None and value > hi:
            raise ParameterViolation(f"{p.name}: {value} > max {hi}")

    # --------------------------------------------------------------- access
    def get(self, name: str, default=None):
        p = self._by_name.get(name)
        if p is None:
            return default
        return self._values[p.parameter_id]

    def require(self, name: str):
        p = self._by_name.get(name)
        if p is None:
            raise ParameterViolation(f"missing required parameter: {name}")
        return self._values[p.parameter_id]

    def info(self, name: str) -> Parameter:
        return self._by_name[name]

    def all_parameters(self) -> list[Parameter]:
        return list(self._by_id.values())

    def snapshot(self) -> dict:
        """Effective values (parameter_id -> value) for persistence/audit."""
        return dict(self._values)

    @property
    def status_summary(self) -> dict:
        out: dict[str, int] = {}
        for p in self._by_id.values():
            out[p.status] = out.get(p.status, 0) + 1
        return out
