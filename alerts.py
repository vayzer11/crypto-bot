"""
alerts.py — Система алертов
Хранит алерты пользователей в памяти и проверяет их по расписанию.
Для продакшена замени на Redis или SQLite.
"""

import aiohttp
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analysis import CryptoAnalyzer

COINGECKO = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")


class AlertManager:
    def __init__(self):
        # {user_id: [{"symbol": str, "price": float, "direction": str}]}
        self._alerts: dict[int, list] = {}

    def add_alert(self, user_id: int, symbol: str, price: float, direction: str = "above"):
        if user_id not in self._alerts:
            self._alerts[user_id] = []
        self._alerts[user_id].append({
            "symbol": symbol.upper(),
            "price": price,
            "direction": direction
        })

    def remove_alert(self, user_id: int, idx: int) -> bool:
        alerts = self._alerts.get(user_id, [])
        if 0 <= idx < len(alerts):
            alerts.pop(idx)
            return True
        return False

    def get_user_alerts(self, user_id: int) -> list:
        return self._alerts.get(user_id, [])

    async def check_alerts(self, analyzer: "CryptoAnalyzer") -> list[tuple]:
        """
        Проверяет все алерты. Возвращает список (user_id, message) для сработавших.
        Срабатавшие алерты удаляются.
        """
        if not self._alerts:
            return []

        # Собираем уникальные символы
        symbols = set()
        for alerts in self._alerts.values():
            for a in alerts:
                symbols.add(a["symbol"])

        # Получаем текущие цены
        prices = await self._fetch_prices(list(symbols), analyzer)
        if not prices:
            return []

        triggered = []
        for user_id, alerts in list(self._alerts.items()):
            remaining = []
            for a in alerts:
                current = prices.get(a["symbol"])
                if current is None:
                    remaining.append(a)
                    continue
                fired = (
                    (a["direction"] == "above" and current >= a["price"]) or
                    (a["direction"] == "below" and current <= a["price"])
                )
                if fired:
                    dir_sym = "≥" if a["direction"] == "above" else "≤"
                    msg = (
                        f"🔔 *Алерт сработал!*\n\n"
                        f"*{a['symbol']}* достиг ${current:,.4f}\n"
                        f"Условие: {dir_sym} ${a['price']:,.4f}\n\n"
                        f"_/analyze {a['symbol']} — посмотреть сигнал_"
                    )
                    triggered.append((user_id, msg))
                else:
                    remaining.append(a)
            self._alerts[user_id] = remaining

        return triggered

    async def _fetch_prices(self, symbols: list[str], analyzer: "CryptoAnalyzer") -> dict:
        """Получить текущие цены по символам через CoinGecko"""
        from analysis import SYMBOL_MAP

        prices = {}
        snapshot = await analyzer._get_market_snapshot(250)
        for coin in snapshot:
            if coin.get("symbol") in symbols:
                prices[coin["symbol"]] = coin["price"]

        missing = [symbol for symbol in symbols if symbol not in prices]
        if not missing:
            return prices

        ids = [SYMBOL_MAP.get(s, s.lower()) for s in missing]
        ids_str = ",".join(ids)
        headers = {"User-Agent": "CryptoSignalBot/1.1"}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    f"{COINGECKO}/simple/price",
                    params={"ids": ids_str, "vs_currencies": "usd"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    if r.status != 200:
                        return prices
                    data = await r.json()
                    for sym, coin_id in zip(missing, ids):
                        if coin_id in data:
                            prices[sym] = data[coin_id]["usd"]
                    return prices
        except Exception:
            return prices
