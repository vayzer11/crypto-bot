"""
alerts.py - lightweight in-memory alert manager.

For production persistence, move alerts to Redis/Postgres.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from analysis import CryptoAnalyzer

COINGECKO_BASE = (
    "https://pro-api.coingecko.com/api/v3"
    if os.getenv("COINGECKO_USE_PRO", "").lower() in {"1", "true", "yes"}
    else "https://api.coingecko.com/api/v3"
)
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")


class AlertManager:
    def __init__(self) -> None:
        self._alerts: dict[int, list[dict[str, Any]]] = {}

    def add_alert(self, user_id: int, symbol: str, price: float, direction: str = "above") -> None:
        self._alerts.setdefault(user_id, []).append(
            {
                "symbol": symbol.upper(),
                "price": price,
                "direction": direction,
            }
        )

    def remove_alert(self, user_id: int, index: int) -> bool:
        alerts = self._alerts.get(user_id, [])
        if 0 <= index < len(alerts):
            alerts.pop(index)
            return True
        return False

    def get_user_alerts(self, user_id: int) -> list[dict[str, Any]]:
        return self._alerts.get(user_id, [])

    def _request_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "CryptoSignalBot/2.0"}
        if COINGECKO_API_KEY:
            key_header = "x-cg-pro-api-key" if "pro-api" in COINGECKO_BASE else "x-cg-demo-api-key"
            headers[key_header] = COINGECKO_API_KEY
        return headers

    async def check_alerts(self, analyzer: "CryptoAnalyzer") -> list[tuple[int, str]]:
        if not self._alerts:
            return []

        from analysis import SYMBOL_MAP

        symbols = sorted({alert["symbol"] for alerts in self._alerts.values() for alert in alerts})
        ids = [SYMBOL_MAP.get(symbol, symbol.lower()) for symbol in symbols]
        prices = await self._fetch_prices(symbols, ids)
        if not prices:
            return []

        triggered: list[tuple[int, str]] = []
        for user_id, alerts in list(self._alerts.items()):
            remaining = []
            for alert in alerts:
                current = prices.get(alert["symbol"])
                if current is None:
                    remaining.append(alert)
                    continue
                fired = (
                    (alert["direction"] == "above" and current >= alert["price"])
                    or (alert["direction"] == "below" and current <= alert["price"])
                )
                if not fired:
                    remaining.append(alert)
                    continue

                direction_symbol = "≥" if alert["direction"] == "above" else "≤"
                message = (
                    "🔔 *Алерт сработал!*\n\n"
                    f"*{alert['symbol']}* достиг {current:,.4f}$\n"
                    f"Условие: {direction_symbol} ${alert['price']:,.4f}\n\n"
                    f"_/analyze {alert['symbol']}_"
                )
                triggered.append((user_id, message))
            self._alerts[user_id] = remaining

        return triggered

    async def _fetch_prices(self, symbols: list[str], ids: list[str]) -> dict[str, float]:
        try:
            async with aiohttp.ClientSession(
                headers=self._request_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as session:
                async with session.get(
                    f"{COINGECKO_BASE}/simple/price",
                    params={"ids": ",".join(ids), "vs_currencies": "usd"},
                ) as response:
                    if response.status != 200:
                        return {}
                    data = await response.json()
        except Exception:
            return {}

        result: dict[str, float] = {}
        for symbol, coin_id in zip(symbols, ids):
            if coin_id in data and "usd" in data[coin_id]:
                result[symbol] = float(data[coin_id]["usd"])
        return result
