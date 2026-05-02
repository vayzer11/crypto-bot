from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

try:
    from _symbol_map_generated import SYMBOL_MAP_COINGECKO as SYMBOL_MAP
except Exception:
    SYMBOL_MAP = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calc_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(x, 0) for x in deltas]
    losses = [abs(min(x, 0)) for x in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for idx in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calc_ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    multiplier = 2 / (period + 1)
    values = [sum(prices[:period]) / period]
    for p in prices[period:]:
        values.append((p - values[-1]) * multiplier + values[-1])
    return values


def calc_macd(prices: list[float]) -> tuple[float | None, float | None, float | None]:
    e12 = calc_ema(prices, 12)
    e26 = calc_ema(prices, 26)
    if not e12 or not e26:
        return None, None, None
    size = min(len(e12), len(e26))
    line = [a - b for a, b in zip(e12[-size:], e26[-size:])]
    signal = calc_ema(line, 9)
    if not signal:
        return line[-1], None, None
    hist = line[-1] - signal[-1]
    return round(line[-1], 6), round(signal[-1], 6), round(hist, 6)


def calc_bollinger(prices: list[float], period: int = 20, std_mult: float = 2.0) -> tuple[float | None, float | None, float | None]:
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = variance**0.5
    return round(mean - std_mult * std, 6), round(mean, 6), round(mean + std_mult * std, 6)


class CryptoAnalyzer:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None, ttl: int = 180) -> Any:
        key = f"{url}:{params}"
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1]

        timeout = aiohttp.ClientTimeout(total=20)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, params=params) as resp:
                        if resp.status >= 400:
                            raise RuntimeError(f"HTTP {resp.status}")
                        data = await resp.json(content_type=None)
                        self._cache[key] = (time.time(), data)
                        return data
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.8 + attempt)
        raise RuntimeError(f"API error: {last_error}")

    async def indicator_snapshot(self, symbol: str) -> dict[str, Any]:
        coin_id = SYMBOL_MAP.get(symbol.upper(), symbol.lower())
        data = await self._get_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": 30},
            ttl=180,
        )
        prices = [safe_float(x[1]) for x in data.get("prices", [])]
        rsi = calc_rsi(prices)
        macd, macd_signal, macd_hist = calc_macd(prices)
        bb_low, bb_mid, bb_high = calc_bollinger(prices)
        return {
            "symbol": symbol.upper(),
            "coin_id": coin_id,
            "rsi": rsi,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "bb_low": bb_low,
            "bb_mid": bb_mid,
            "bb_high": bb_high,
            "prices_count": len(prices),
        }

    def scam_risk(self, security: dict[str, Any]) -> dict[str, Any]:
        risk = 0
        flags: list[str] = []
        if security.get("is_honeypot"):
            risk += 50
            flags.append("honeypot")
        if security.get("is_mintable"):
            risk += 15
            flags.append("mint")
        if security.get("is_blacklisted"):
            risk += 15
            flags.append("blacklist")
        if security.get("buy_tax") not in (None, 0):
            risk += 10
            flags.append("buy-tax")
        if security.get("sell_tax") not in (None, 0):
            risk += 10
            flags.append("sell-tax")
        if not security.get("lp_locked"):
            risk += 10
            flags.append("lp-unlocked")
        if not security.get("is_open_source"):
            risk += 10
            flags.append("unverified")
        return {"risk_score": min(100, risk), "flags": flags}
