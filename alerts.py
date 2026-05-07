from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    type: str
    symbol: str
    condition: str
    value: float
    created_at: str


class AlertManager:
    def __init__(self) -> None:
        self._alerts: dict[int, list[Alert]] = {}
        self._history: list[dict[str, Any]] = []
        self._dedupe: set[str] = set()

    def add_price_alert(self, user_id: int, symbol: str, price: float, direction: str = "above") -> None:
        self._alerts.setdefault(user_id, []).append(
            Alert(type="price", symbol=symbol.upper(), condition=direction, value=float(price), created_at=datetime.now(timezone.utc).isoformat())
        )

    def add_volume_alert(self, user_id: int, symbol: str, min_volume_1h: float) -> None:
        self._alerts.setdefault(user_id, []).append(
            Alert(type="volume", symbol=symbol.upper(), condition="gte", value=float(min_volume_1h), created_at=datetime.now(timezone.utc).isoformat())
        )

    def add_listing_alert(self, user_id: int, chain: str, max_age_minutes: int = 30) -> None:
        self._alerts.setdefault(user_id, []).append(
            Alert(type="listing", symbol=chain.lower(), condition="age_lte", value=float(max_age_minutes), created_at=datetime.now(timezone.utc).isoformat())
        )

    def get_user_alerts(self, user_id: int) -> list[dict[str, Any]]:
        return [a.__dict__.copy() for a in self._alerts.get(user_id, [])]

    def remove_alert(self, user_id: int, idx: int) -> bool:
        arr = self._alerts.get(user_id, [])
        if 0 <= idx < len(arr):
            arr.pop(idx)
            return True
        return False

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._history[-limit:]

    def _push_history(self, user_id: int, key: str, message: str) -> None:
        self._history.append({"ts": datetime.now(timezone.utc).isoformat(), "user_id": user_id, "key": key, "message": message})
        self._dedupe.add(key)

    def evaluate(self, market_rows: list[dict[str, Any]]) -> list[tuple[int, str]]:
        triggered: list[tuple[int, str]] = []
        by_symbol: dict[str, dict[str, Any]] = {str(x.get("symbol", "")).upper(): x for x in market_rows}
        for user_id, alerts in list(self._alerts.items()):
            next_alerts: list[Alert] = []
            for a in alerts:
                msg: str | None = None
                dedupe_key = f"{user_id}:{a.type}:{a.symbol}:{a.condition}:{a.value}"
                if dedupe_key in self._dedupe:
                    continue

                if a.type == "price":
                    row = by_symbol.get(a.symbol)
                    if row:
                        price = float(row.get("price", 0))
                        if (a.condition == "above" and price >= a.value) or (a.condition == "below" and price <= a.value):
                            sign = "≥" if a.condition == "above" else "≤"
                            msg = f"🔔 Price Alert\n{a.symbol}: ${price:,.6f}\nУсловие: {sign} ${a.value:,.6f}"
                elif a.type == "volume":
                    row = by_symbol.get(a.symbol)
                    if row and float(row.get("volume_1h", 0)) >= a.value:
                        msg = f"🔔 Volume Alert\n{a.symbol}: 1h volume ${float(row.get('volume_1h', 0)):,.0f}"
                elif a.type == "listing":
                    chain = a.symbol.lower()
                    fresh = [x for x in market_rows if str(x.get("chain", "")).lower() == chain and float(x.get("age_hours", 999)) * 60 <= a.value]
                    if fresh:
                        msg = f"🆕 Listing Alert\nСеть {chain.upper()}: найдено {len(fresh)} новых токенов <= {int(a.value)} мин."

                if msg:
                    self._push_history(user_id, dedupe_key, msg)
                    triggered.append((user_id, msg))
                else:
                    next_alerts.append(a)
            self._alerts[user_id] = next_alerts
        return triggered


async def check_alerts_loop(bot: Any, analyzer: Any, alert_manager: AlertManager, interval_sec: int = 75) -> None:
    while True:
        try:
            coins = await analyzer.fetch_all_coins()
            market_rows = [
                {
                    "symbol": str(c.get("symbol", "")).upper(),
                    "price": float(c.get("current_price") or 0),
                    "volume_1h": float(c.get("total_volume") or 0) / 24,
                    "chain": str(c.get("platform", "") or ""),
                    "age_hours": float(c.get("atl_date") and 999 or 999),
                }
                for c in coins
                if c.get("symbol")
            ]
            triggered = alert_manager.evaluate(market_rows)
            for user_id, text in triggered:
                try:
                    await bot.send_message(user_id, text)
                except Exception as send_err:
                    logger.warning("alert send failed %s: %s", user_id, send_err)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("check_alerts_loop error: %s", exc)
        await asyncio.sleep(interval_sec)
