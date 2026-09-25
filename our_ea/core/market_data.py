"""Market data integrity — RAW -> INGEST -> VALIDATE -> NORMALIZE -> CANONICAL.

Checks (Phase 16): impossible prices, inverted quote, spread anomaly,
tick volatility spike, stale/regressing timestamps. Flagged ticks are
rejected with TICK_REJECTED — never silently repaired. Data-quality gate:
bad data => NO TRADE.
"""
from __future__ import annotations

from dataclasses import dataclass

from our_ea.core.identifiers import parse_ts, utc_now_iso


@dataclass(frozen=True)
class Tick:
    ts: str
    buy_price: float   # ask (price BUY orders pay)
    sell_price: float  # bid (price SELL orders pay / BUY valuation)
    source: str = "feed"
    received_at: str = ""

    @property
    def spread(self) -> float:
        return round(self.buy_price - self.sell_price, 4)


class TickRejection(RuntimeError):
    def __init__(self, reason: str, detail: dict | None = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(f"TICK_REJECTED[{reason}] {detail or ''}")


class MarketDataPipeline:
    def __init__(self, max_spread_usd: float = 0.6,
                 max_tick_volatility_usd: float = 25.0,
                 staleness_sec: float = 30.0):
        self.max_spread_usd = max_spread_usd
        self.max_tick_volatility_usd = max_tick_volatility_usd
        self.staleness_sec = staleness_sec
        self._prev_price: float = 0.0
        self._prev_ts: str = ""

    def reset(self) -> None:
        self._prev_price = 0.0
        self._prev_ts = ""

    # ------------------------------------------------------------------ api
    def validate(self, tick: Tick, now: str = "") -> Tick:
        """Validate a tick. `now` (wall clock) is optional: when omitted the
        tick is checked against its own timeline — the live feed layer is
        responsible for passing real `now` (freshness gate); replay/historical
        runs stay deterministic."""
        if not now and tick.received_at:
            now = tick.received_at
        now_dt = parse_ts(now or tick.ts)
        ts_dt = parse_ts(tick.ts)

        if tick.buy_price <= 0 or tick.sell_price <= 0:
            raise TickRejection("INVALID_PRICE",
                                {"buy": tick.buy_price, "sell": tick.sell_price})
        if tick.sell_price > tick.buy_price:
            raise TickRejection("INVERTED_QUOTE",
                                {"buy": tick.buy_price, "sell": tick.sell_price})
        spread = tick.spread
        if spread < 0 or spread > self.max_spread_usd:
            raise TickRejection("SPREAD_ANOMALY", {"spread": spread})

        if ts_dt is not None:
            if self._prev_ts:
                prev_dt = parse_ts(self._prev_ts)
                if prev_dt and ts_dt < prev_dt:
                    raise TickRejection("REGRESSING_TIMESTAMP",
                                        {"ts": tick.ts, "prev": self._prev_ts})
            if now_dt is not None:
                age = (now_dt - ts_dt).total_seconds()
                if age > self.staleness_sec:
                    raise TickRejection("STALE_TICK", {"age_sec": age})
                if age < -self.staleness_sec:
                    raise TickRejection("FUTURE_TIMESTAMP", {"age_sec": age})

        mid = round((tick.buy_price + tick.sell_price) / 2.0, 4)
        if self._prev_price > 0:
            jump = abs(mid - self._prev_price)
            if jump > self.max_tick_volatility_usd:
                raise TickRejection("VOLATILITY_SPIKE",
                                    {"jump_usd": jump, "mid": mid})
        self._prev_price = mid
        self._prev_ts = tick.ts
        return tick

    def classify_observed(self, tick: Tick) -> str:
        """Data-quality classification without raising (monitoring path)."""
        try:
            self.validate(tick)
            return "VALID"
        except TickRejection as exc:
            return exc.reason
