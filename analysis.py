"""
analysis.py - technical analysis helpers for the Telegram bot and mini app.

Main fixes in this version:
- cache market snapshots to avoid CoinGecko 429 floods
- remove invalid market queries like order=gecko_desc
- use a saner market-level RSI proxy for scans
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

COINGECKO = "https://api.coingecko.com/api/v3"
DEFILLAMA = "https://api.llama.fi"
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"

MEME_KEYWORDS = [
    "pepe", "doge", "shib", "floki", "bonk", "wif", "cat", "dog", "frog", "moon", "ape",
    "baby", "elon", "trump", "maga", "wojak", "chad", "based", "brett", "popcat", "neiro",
    "pnut", "goat", "mew", "turbo", "mog", "act", "banana",
]
DEFI_KEYWORDS = [
    "swap", "finance", "protocol", "lending", "vault", "yield", "dao", "governance", "uniswap",
    "aave", "curve", "maker", "compound", "dydx", "gmx", "pendle",
]

try:
    from _symbol_map_generated import SYMBOL_MAP_COINGECKO as _SYMBOL_MAP_BASE
except ImportError:
    _SYMBOL_MAP_BASE = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}

# Приоритет: явные id для мемов / листингов / конфликтных тикеров (620+ из CoinGecko markets + оверрайды)
SYMBOL_OVERRIDES: dict[str, str] = {
    "MATIC": "matic-network",
    "RNDR": "render-token",
    "RENDER": "render-token",
    "MEW": "cat-in-a-dogs-world",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "PEPE": "pepe",
    "SHIB": "shiba-inu",
    "DOGE": "dogecoin",
    "FLOKI": "floki",
    "POPCAT": "popcat",
    "PNUT": "peanut-the-squirrel",
    "NEIRO": "neiro-on-eth",
    "TURBO": "turbo",
    "MOG": "mog-coin",
    "BRETT": "based-brett",
    "GOAT": "goatseus-maximus",
    "ACT": "act-i-the-ai-prophet",
    "BANANA": "banana-gun",
    "LADYS": "milady-meme-coin",
    "WOJAK": "wojak",
    "CHAD": "chad-coin",
    "BILLY": "billy",
    "SLERF": "slerf",
    "BOME": "book-of-meme",
    "ZERO": "zero-2",
    "GIGA": "gigachad-2",
    "PONKE": "ponke",
    "MYRO": "myro-2",
    "SILLY": "silly-dragon",
    "RETARDIO": "retardio",
    "MICHI": "michi",
    "NMR": "numeraire",
    "CTXC": "cortex",
    "ZKS": "zksync",
    "SCROLL": "scroll",
    "SAMO": "samoyedcoin",
    "PVU": "plant-vs-undead-token",
    "NAKA": "nakamoto-games",
    "HERO": "metahero",
    "HMSTR": "hamster-kombat",
    "CATI": "catizen",
    "MAJOR": "major",
    "BLUM": "blum-2",
    "DOGS": "dogs-2",
    "LISTA": "lista-dao",
    "ZRO": "layerzero",
    "ETHFI": "ether-fi",
    "EIGEN": "eigenlayer",
    "REZ": "renzo",
    "SAGA": "saga-2",
    "PORTAL": "portal-2",
    "PIXEL": "pixels",
    "ALT": "altlayer",
    "AGIX": "singularitynet",
    "OCEAN": "ocean-protocol",
    "RDNT": "radiant-capital",
    "VELA": "vela-token",
    "GNS": "gains-network",
    "METIS": "metis-token",
    "BOBA": "boba-network",
    "STRK": "starknet",
}

SYMBOL_MAP: dict[str, str] = {**_SYMBOL_MAP_BASE, **SYMBOL_OVERRIDES}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    if price >= 1:
        return f"${price:.4f}"
    if price >= 0.01:
        return f"${price:.6f}"
    return f"${price:.8f}"


def fmt_b(value: float) -> str:
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def calc_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [delta if delta > 0 else 0 for delta in deltas[-period:]]
    losses = [abs(delta) if delta < 0 else 0 for delta in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    factor = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * factor + ema[-1] * (1 - factor))
    return ema


def calc_macd(prices: list[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    if not ema12 or not ema26:
        return None, None, None
    size = min(len(ema12), len(ema26))
    macd_line = [a - b for a, b in zip(ema12[-size:], ema26[-size:])]
    signal = calc_ema(macd_line, 9)
    if not signal:
        return round(macd_line[-1], 6), None, None
    histogram = macd_line[-1] - signal[-1]
    return round(macd_line[-1], 6), round(signal[-1], 6), round(histogram, 6)


def calc_bollinger(prices: list[float], period: int = 20, std_mult: float = 2.0) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mean = sum(window) / period
    std = statistics.stdev(window)
    return round(mean - std_mult * std, 6), round(mean, 6), round(mean + std_mult * std, 6)


def rsi_signal(rsi: float) -> str:
    if rsi >= 80:
        return "🔴 СИЛЬНО ПЕРЕКУПЛЕН"
    if rsi >= 70:
        return "🟠 ПЕРЕКУПЛЕН"
    if rsi >= 60:
        return "🟡 Нейтрально-бычий"
    if rsi >= 40:
        return "🟡 Нейтральная зона"
    if rsi >= 30:
        return "🟢 Близко к перепроданности"
    return "🟢 ПЕРЕПРОДАН — возможен отскок"


def market_rsi_proxy(change_1h: float, change_24h: float, change_7d: float, vol_ratio: float) -> float:
    score = 50 + change_1h * 2.0 + change_24h * 1.1 + change_7d * 0.55
    if vol_ratio > 0.35:
        score += 4
    return round(clamp(score, 6, 94), 1)


def overall_signal(
    rsi: float,
    macd_hist: Optional[float],
    price: float,
    bb_low: Optional[float],
    bb_mid: Optional[float],
    bb_high: Optional[float],
    vol_ratio: float,
    onchain_score: int = 0,
) -> tuple[str, str]:
    score = 0
    if rsi < 30:
        score += 2
    elif rsi < 40:
        score += 1
    elif rsi > 70:
        score -= 2
    elif rsi > 60:
        score -= 1

    if macd_hist is not None:
        score += 1 if macd_hist > 0 else -1

    if bb_low is not None and bb_high is not None:
        if price < bb_low:
            score += 2
        elif price > bb_high:
            score -= 2

    if vol_ratio > 0.3:
        score += 1

    score += onchain_score

    if score >= 3:
        return "🟢 ПОКУПАТЬ", "Сильный сигнал на вход"
    if score >= 1:
        return "🟡 ЖДАТЬ / Осторожная покупка", "Слабый бычий сигнал"
    if score <= -3:
        return "🔴 ПРОДАВАТЬ / НЕ ВХОДИТЬ", "Сильный медвежий сигнал"
    if score <= -1:
        return "🟠 ОСТОРОЖНО", "Нейтрально-медвежий сигнал"
    return "🟡 ЖДАТЬ", "Нейтральная зона"


class CryptoAnalyzer:
    def __init__(self) -> None:
        self._cache_dir = Path(__file__).resolve().parent
        self._market_cache_path = self._cache_dir / "market_cache.json"
        self._fg_cache_path = self._cache_dir / "fear_greed_cache.json"

        market_payload = self._load_cache_payload(self._market_cache_path, [])
        self._market_cache: list[dict[str, Any]] = market_payload["data"]
        self._market_cache_time = market_payload["ts"]
        self._market_cache_ttl = 240
        self._market_lock = asyncio.Lock()

        fg_payload = self._load_cache_payload(self._fg_cache_path, {"value": 50, "label": "Neutral"})
        self._fg_cache = fg_payload["data"]
        self._fg_cache_time = fg_payload["ts"]
        self._fg_cache_ttl = 300

    def _load_cache_payload(self, path: Path, default: Any) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "data" in payload:
                return {"ts": float(payload.get("ts", 0)), "data": payload.get("data", default)}
        except Exception:
            pass
        return {"ts": 0.0, "data": default}

    def _save_cache_payload(self, path: Path, data: Any) -> None:
        try:
            payload = {"ts": time.time(), "data": data}
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "CryptoSignalBot/1.1"}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
        return headers

    async def _get_json(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Optional[Any]:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)

        try:
            if "api.coingecko.com" in url:
                await asyncio.sleep(1)
            async with aiohttp.ClientSession(headers=request_headers) as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    if response.status == 429:
                        await asyncio.sleep(3)
                        async with session.get(
                            url,
                            params=params,
                            timeout=aiohttp.ClientTimeout(total=20),
                        ) as retry:
                            if retry.status == 200:
                                return await retry.json()
                        return None
                    if response.status == 200:
                        return await response.json()
        except Exception:
            return None
        return None

    async def _resolve_id(self, symbol: str) -> str:
        sym_u = symbol.upper().strip()
        if sym_u in SYMBOL_MAP:
            return SYMBOL_MAP[sym_u]
        data = await self._get_json(f"{COINGECKO}/search", {"query": symbol})
        if data and data.get("coins"):
            for coin in data["coins"]:
                if (coin.get("symbol") or "").upper() == sym_u:
                    return str(coin["id"])
        return symbol.lower()

    async def _get_prices(self, coin_id: str, days: int = 60) -> list[float]:
        data = await self._get_json(
            f"{COINGECKO}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": days, "interval": "daily"},
        )
        if not data:
            return []
        return [safe_float(point[1]) for point in data.get("prices", [])]

    async def _get_fear_greed(self) -> dict[str, Any]:
        now = time.time()
        if now - self._fg_cache_time < self._fg_cache_ttl:
            return self._fg_cache

        data = await self._get_json("https://api.alternative.me/fng/")
        if data and data.get("data"):
            item = data["data"][0]
            self._fg_cache = {
                "value": int(item.get("value", 50)),
                "label": item.get("value_classification", "Neutral"),
            }
            self._fg_cache_time = now
            self._save_cache_payload(self._fg_cache_path, self._fg_cache)
        return self._fg_cache

    async def _get_defi_llama(self, symbol: str) -> dict[str, Any]:
        result = {"tvl": None, "tvl_change": None, "flows": None}
        try:
            protocols = await self._get_json(f"{DEFILLAMA}/protocols")
            if not protocols:
                return result

            symbol_lower = symbol.lower()
            protocol = next(
                (
                    item for item in protocols
                    if item.get("symbol", "").lower() == symbol_lower
                    or symbol_lower in item.get("name", "").lower()
                ),
                None,
            )
            if not protocol:
                return result

            slug = protocol.get("slug") or protocol.get("name", "").lower().replace(" ", "-")
            detail = await self._get_json(f"{DEFILLAMA}/protocol/{slug}")
            if detail:
                tvl_data = detail.get("tvl", [])
                if len(tvl_data) >= 2:
                    current_tvl = safe_float(tvl_data[-1].get("totalLiquidityUSD"))
                    prev_tvl = safe_float(tvl_data[-2].get("totalLiquidityUSD"))
                    result["tvl"] = current_tvl
                    result["tvl_change"] = ((current_tvl - prev_tvl) / prev_tvl * 100) if prev_tvl else 0
                    if len(tvl_data) >= 7:
                        week_ago = safe_float(tvl_data[-7].get("totalLiquidityUSD"))
                        result["flows"] = current_tvl - week_ago
        except Exception:
            return result
        return result

    def _normalize_market_coin(self, coin: dict[str, Any]) -> dict[str, Any]:
        price = safe_float(coin.get("current_price"))
        market_cap = safe_float(coin.get("market_cap"))
        volume = safe_float(coin.get("total_volume"))
        change_1h = safe_float(coin.get("price_change_percentage_1h_in_currency"))
        change_24h = safe_float(coin.get("price_change_percentage_24h"))
        change_7d = safe_float(coin.get("price_change_percentage_7d_in_currency"))
        change_30d = safe_float(coin.get("price_change_percentage_30d_in_currency"))
        vol_ratio = (volume / market_cap) if market_cap else 0.0
        rsi = market_rsi_proxy(change_1h, change_24h, change_7d, vol_ratio)

        return {
            "id": coin.get("id"),
            "symbol": coin.get("symbol", "").upper(),
            "name": coin.get("name"),
            "price": price,
            "market_cap": market_cap,
            "volume": volume,
            "rank": coin.get("market_cap_rank"),
            "change_1h": change_1h,
            "change_24h": change_24h,
            "change_7d": change_7d,
            "change_30d": change_30d,
            "ath": safe_float(coin.get("ath")),
            "vol_ratio": vol_ratio,
            "rsi": rsi,
        }

    def _coin_signal(self, coin: dict[str, Any], fg_value: int = 50) -> tuple[str, int]:
        score = 50
        rsi = coin["rsi"]
        change_24h = coin["change_24h"]
        change_7d = coin["change_7d"]
        vol_ratio = coin["vol_ratio"]

        if rsi <= 32:
            score += 18
        elif rsi <= 40:
            score += 10
        elif rsi >= 72:
            score -= 18
        elif rsi >= 64:
            score -= 10

        if change_24h > 0:
            score += 5
        elif change_24h < -8:
            score -= 8

        if change_7d > 4:
            score += 6
        elif change_7d < -10:
            score -= 8

        if vol_ratio > 0.35:
            score += 5
        elif vol_ratio > 0.18:
            score += 2

        if fg_value < 25:
            score += 4
        elif fg_value > 75:
            score -= 4

        score = int(clamp(score, 5, 95))
        if score >= 65:
            return "buy", score
        if score <= 35:
            return "sell", score
        return "wait", score

    def calculate_signal_score(
        self,
        rsi: float,
        macd: float,
        macd_signal: float,
        price: float,
        bb_lower: float,
        bb_upper: float,
        ema20: float,
        ema50: float,
        change_24h: float,
        change_7d: float,
        vol_ratio: float,
    ) -> dict[str, Any]:
        score = 0
        reasons: list[str] = []

        if rsi < 25:
            score += 35
            reasons.append("RSI экстремально перепродан")
        elif rsi < 35:
            score += 20
            reasons.append("RSI перепродан")
        elif rsi < 45:
            score += 10
            reasons.append("RSI нейтрально-бычий")
        elif rsi > 75:
            score -= 35
            reasons.append("RSI экстремально перекуплен")
        elif rsi > 65:
            score -= 20
            reasons.append("RSI перекуплен")

        if macd > macd_signal and macd > 0:
            score += 15
            reasons.append("MACD бычий crossover")
        elif macd > macd_signal:
            score += 8
            reasons.append("MACD разворот вверх")
        elif macd < macd_signal and macd < 0:
            score -= 15
            reasons.append("MACD медвежий")

        if price <= bb_lower:
            score += 20
            reasons.append("Цена у нижней BB")
        elif price >= bb_upper:
            score -= 20
            reasons.append("Цена у верхней BB")

        if ema20 > ema50:
            score += 10
            reasons.append("EMA20 > EMA50 (бычий тренд)")
        else:
            score -= 10
            reasons.append("EMA20 < EMA50 (медвежий тренд)")

        if vol_ratio > 10 and change_24h > 0:
            score += 10
            reasons.append("Высокий объём при росте")
        elif vol_ratio > 10 and change_24h < 0:
            score -= 10
            reasons.append("Высокий объём при падении")

        if change_7d > 15:
            score += 5
            reasons.append("Устойчивый рост за 7д")
        elif change_7d < -15:
            score -= 5
            reasons.append("Сильная слабость за 7д")

        if score >= 35:
            signal = "🟢 ПОКУПАТЬ"
            color = "buy"
        elif score >= 15:
            signal = "🟡 ОСТОРОЖНАЯ ПОКУПКА"
            color = "watch"
        elif score <= -35:
            signal = "🔴 ПРОДАВАТЬ"
            color = "sell"
        elif score <= -15:
            signal = "🟠 ОСТОРОЖНАЯ ПРОДАЖА"
            color = "caution"
        else:
            signal = "⚪ ЖДАТЬ"
            color = "wait"

        entry = price
        stop_loss = price * (0.93 if color in ["buy", "watch"] else 1.07)
        tp1 = price * (1.05 if color in ["buy", "watch"] else 0.95)
        tp2 = price * (1.12 if color in ["buy", "watch"] else 0.88)
        tp3 = price * (1.22 if color in ["buy", "watch"] else 0.78)

        return {
            "signal": signal,
            "score": int(clamp(score, -100, 100)),
            "color": color,
            "reasons": reasons[:4],
            "entry": entry,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
        }

    async def _groq_ru(self, prompt: str, max_tokens: int = 220) -> str:
        if not GROQ_API_KEY:
            return ""
        body = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.35,
        }
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    GROQ_API,
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as response:
                    if response.status != 200:
                        return ""
                    result = await response.json()
                    return str(result["choices"][0]["message"]["content"]).strip()
        except Exception:
            return ""

    async def get_ai_analysis(self, symbol: str, data: dict[str, Any]) -> str:
        prompt = (
            f"Ты криптоаналитик. Кратко 2–3 полных предложения на русском по {symbol}: вход/ожидание, риски, что подтвердить.\n"
            f"Цена ${data['price']}, RSI {data['rsi']}, MACD {data['macd']}, 24ч {data['change_24h']}%, 7д {data['change_7d']}%, "
            f"Vol/MCap {data['vol_ratio']}%, F&G {data['fear_greed']}, TVL {data['tvl']}."
        )
        g = await self._groq_ru(prompt, max_tokens=260)
        if g:
            return g
        if not ANTHROPIC_API_KEY:
            return "AI недоступен: задай GROQ_API_KEY или ANTHROPIC_API_KEY в окружении."
        claude_prompt = f"""You are a professional crypto trader. Analyze {symbol}:
Price: ${data['price']}
RSI: {data['rsi']}
MACD: {data['macd']}
Change 24h: {data['change_24h']}%
Change 7d: {data['change_7d']}%
Volume/MCap ratio: {data['vol_ratio']}%
Fear & Greed: {data['fear_greed']}
TVL: {data['tvl']}

Give a SHORT trading recommendation in Russian (4-6 sentences):
1. Should I enter now or wait?
2. Key risks
3. What to watch for entry confirmation
Be honest and specific. No generic advice."""
        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": "claude-opus-4-5",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": claude_prompt}],
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=40),
                ) as response:
                    if response.status != 200:
                        return "Claude временно недоступен, используй техническую часть анализа."
                    result = await response.json()
                    content = result.get("content", [])
                    if content and isinstance(content, list):
                        return str(content[0].get("text", "")).strip() or "Нет ответа от Claude."
                    return "Нет ответа от Claude."
        except Exception:
            return "Ошибка запроса к Claude API."

    async def _get_market_snapshot(self, limit: int = 200) -> list[dict[str, Any]]:
        now = time.time()
        if self._market_cache and (now - self._market_cache_time) < self._market_cache_ttl:
            return self._market_cache[:limit]

        async with self._market_lock:
            now = time.time()
            if self._market_cache and (now - self._market_cache_time) < self._market_cache_ttl:
                return self._market_cache[:limit]

            pages = []
            remaining = max(limit, 100)
            page = 1
            while remaining > 0:
                per_page = min(250, remaining)
                pages.append(
                    self._get_json(
                        f"{COINGECKO}/coins/markets",
                        {
                            "vs_currency": "usd",
                            "order": "market_cap_desc",
                            "per_page": per_page,
                            "page": page,
                            "sparkline": "false",
                            "price_change_percentage": "1h,24h,7d,30d",
                        },
                    )
                )
                remaining -= per_page
                page += 1

            responses = await asyncio.gather(*pages)
            raw_market = [item for response in responses if response for item in response]
            if raw_market:
                self._market_cache = [self._normalize_market_coin(item) for item in raw_market]
                self._market_cache_time = time.time()
                self._save_cache_payload(self._market_cache_path, self._market_cache)
                return self._market_cache[:limit]

            if self._market_cache:
                return self._market_cache[:limit]
            return []

    async def _find_snapshot_coin(self, symbol: str, coin_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        coins = await self._get_market_snapshot(250)
        symbol_upper = symbol.upper()
        for coin in coins:
            if coin.get("symbol") == symbol_upper:
                return coin
            if coin_id and coin.get("id") == coin_id:
                return coin
        return None

    def _coin_category(self, coin: dict[str, Any]) -> str:
        rank = int(coin.get("market_cap_rank") or 999999)
        name_id = f"{coin.get('name', '')} {coin.get('id', '')}".lower()
        vol_ratio = (safe_float(coin.get("total_volume")) / safe_float(coin.get("market_cap"))) if safe_float(coin.get("market_cap")) > 0 else 0
        change_24h = safe_float(coin.get("price_change_percentage_24h"))
        if any(k in name_id for k in MEME_KEYWORDS):
            return "🐸 MEMECOIN"
        if any(k in name_id for k in DEFI_KEYWORDS):
            return "💎 DEFI"
        if vol_ratio > 0.2 and rank > 200:
            return "🆕 NEW LISTING"
        if change_24h > 30:
            return "🔥 HOT"
        return "📌 WATCHLIST"

    async def find_new_coins(self) -> str:
        try:
            markets = await self._get_json(
                f"{COINGECKO}/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "volume_desc",
                    "per_page": 100,
                    "page": 1,
                    "sparkline": "false",
                    "price_change_percentage": "24h,7d",
                },
            )
            trending = await self._get_json(f"{COINGECKO}/search/trending")
            trending_ids = {item.get("item", {}).get("id", "") for item in (trending or {}).get("coins", [])}
            now = datetime.now(timezone.utc)
            filtered: list[dict[str, Any]] = []

            for coin in markets or []:
                mcap = safe_float(coin.get("market_cap"))
                volume = safe_float(coin.get("total_volume"))
                if mcap <= 0:
                    continue
                vol_ratio = (volume / mcap) * 100
                change_24h = safe_float(coin.get("price_change_percentage_24h"))
                change_7d = safe_float(coin.get("price_change_percentage_7d_in_currency"))
                atl_date = str(coin.get("atl_date") or "")
                is_recent = False
                if atl_date:
                    try:
                        is_recent = (now - datetime.fromisoformat(atl_date.replace("Z", "+00:00"))).days <= 30
                    except Exception:
                        is_recent = False

                matched = (
                    is_recent
                    or vol_ratio > 15
                    or change_24h > 20
                    or (mcap < 500_000_000 and volume > 50_000_000)
                    or coin.get("id") in trending_ids
                )
                if not matched:
                    continue

                if vol_ratio > 50 and change_24h > 50:
                    risk = "🔴 HIGH RISK"
                    risk_level = 2
                    quick = "AVOID 🚫"
                elif vol_ratio > 25 or change_24h > 30:
                    risk = "🟡 MEDIUM RISK"
                    risk_level = 1
                    quick = "RISKY ⚠️"
                else:
                    risk = "🟢 LOW RISK"
                    risk_level = 0
                    quick = "WATCH 👀"

                filtered.append(
                    {
                        "symbol": coin.get("symbol", "").upper(),
                        "name": coin.get("name", ""),
                        "price": safe_float(coin.get("current_price")),
                        "change_24h": change_24h,
                        "change_7d": change_7d,
                        "vol_ratio": vol_ratio,
                        "volume": volume,
                        "category": self._coin_category(coin),
                        "risk": risk,
                        "risk_level": risk_level,
                        "quick": quick,
                    }
                )

            filtered.sort(key=lambda x: (x["risk_level"], -x["volume"]))
            if not filtered:
                return "🆕 Новые монеты по заданным фильтрам не найдены."
            lines = ["🆕 *Новые монеты: Meme/DeFi/Листинги*\n"]
            for coin in filtered[:10]:
                lines.append(
                    f"{coin['category']} *{coin['symbol']}* ({coin['name']})\n"
                    f"Цена: {fmt_price(coin['price'])} | 24ч: {coin['change_24h']:+.1f}% | 7д: {coin['change_7d']:+.1f}%\n"
                    f"Vol/MCap: {coin['vol_ratio']:.1f}% | {coin['risk']} | {coin['quick']}"
                )
            lines.append("\n⚠️ Новые монеты = высокий риск. DYOR.")
            return "\n\n".join(lines)
        except Exception:
            return "❌ Не удалось выполнить скан новых монет. Попробуй позже."

    def calculate_gem_score(self, coin: dict) -> dict:
        score = 0
        signals: list[str] = []
        potential = ""

        mcap = safe_float(coin.get("market_cap"))
        vol = safe_float(coin.get("total_volume"))
        vol_ratio = vol / mcap if mcap > 0 else 0
        change_24h = safe_float(coin.get("price_change_percentage_24h"))
        change_7d = safe_float(coin.get("price_change_percentage_7d_in_currency"))
        ath = safe_float(coin.get("ath"))
        price = safe_float(coin.get("current_price"))
        ath_drop = ((price - ath) / ath * 100) if ath > 0 else 0

        if mcap < 10_000_000:
            score += 40
            signals.append("Микрокапа < $10M 🚀")
        elif mcap < 50_000_000:
            score += 30
            signals.append("Малая капа < $50M")
        elif mcap < 100_000_000:
            score += 20
            signals.append("Капа < $100M")
        elif mcap < 500_000_000:
            score += 10
            signals.append("Капа < $500M")

        if vol_ratio > 0.5:
            score += 35
            signals.append("Аномальный объём! Кто-то скупает 🐋")
        elif vol_ratio > 0.3:
            score += 25
            signals.append("Очень высокий объём")
        elif vol_ratio > 0.15:
            score += 15
            signals.append("Высокий объём")

        if ath_drop < -90:
            score += 30
            signals.append("На -90% от ATH — огромный потенциал")
        elif ath_drop < -80:
            score += 20
            signals.append("На -80% от ATH")
        elif ath_drop < -70:
            score += 10
            signals.append("На -70% от ATH")

        if change_24h > 30:
            score += 25
            signals.append("Уже растёт +30% за 24ч 🔥")
        elif change_24h > 15:
            score += 15
            signals.append("Движение +15% за 24ч")
        elif change_24h > 5:
            score += 8
            signals.append("Начинает двигаться")
        elif change_24h < -20:
            score -= 20
            signals.append("Сильное падение ⚠️")

        if change_7d > 50:
            score += 20
            signals.append("Тренд +50% за 7д")
        elif change_7d > 20:
            score += 10
            signals.append("Тренд +20% за 7д")
        elif change_7d < -30:
            score -= 15

        if mcap < 10_000_000 and score >= 60:
            potential = "🚀 x50-x100 потенциал"
        elif mcap < 50_000_000 and score >= 50:
            potential = "🚀 x20-x50 потенциал"
        elif mcap < 100_000_000 and score >= 40:
            potential = "📈 x10-x20 потенциал"
        elif mcap < 500_000_000 and score >= 35:
            potential = "📈 x5-x10 потенциал"
        else:
            potential = "📊 x2-x5 потенциал"

        if vol_ratio > 0.5 and change_24h > 50:
            risk = "🔴 ОЧЕНЬ ВЫСОКИЙ (возможен дамп)"
        elif mcap < 10_000_000:
            risk = "🟠 ВЫСОКИЙ (малая капа)"
        elif score >= 50:
            risk = "🟡 СРЕДНИЙ"
        else:
            risk = "🟢 УМЕРЕННЫЙ"
        return {"score": min(score, 100), "signals": signals[:4], "potential": potential, "risk": risk, "ath_drop": ath_drop}

    async def find_gems(self) -> str:
        try:
            markets_top = await self._get_json(
                f"{COINGECKO}/coins/markets",
                {"vs_currency": "usd", "order": "volume_desc", "per_page": 250, "page": 1, "sparkline": "false", "price_change_percentage": "24h,7d"},
            )
            markets_top2 = await self._get_json(
                f"{COINGECKO}/coins/markets",
                {"vs_currency": "usd", "order": "volume_desc", "per_page": 250, "page": 2, "sparkline": "false", "price_change_percentage": "24h,7d"},
            )
            trending = await self._get_json(f"{COINGECKO}/search/trending")
            trending_ids = [item.get("item", {}).get("id") for item in (trending or {}).get("coins", []) if item.get("item", {}).get("id")]

            all_markets = (markets_top or []) + (markets_top2 or [])
            by_id = {item.get("id"): item for item in all_markets if item.get("id")}

            for coin_id in trending_ids:
                if coin_id in by_id:
                    continue
                details = await self._get_json(
                    f"{COINGECKO}/coins/markets",
                    {"vs_currency": "usd", "ids": coin_id, "sparkline": "false", "price_change_percentage": "24h,7d"},
                )
                if details:
                    by_id[coin_id] = details[0]

            candidates = [coin for coin in by_id.values() if safe_float(coin.get("market_cap")) < 100_000_000 or coin.get("id") in trending_ids]
            scored = []
            for coin in candidates:
                gem = self.calculate_gem_score(coin)
                if gem["score"] >= 35:
                    scored.append((coin, gem))
            scored.sort(key=lambda item: item[1]["score"], reverse=True)
            if not scored:
                return "💎 Gems с оценкой 35+ сейчас не найдено."

            lines = ["💎 *Gem Finder — монеты с потенциалом x10-x100*\n"]
            for coin, gem in scored[:5]:
                lines.append(
                    f"*{coin.get('symbol', '').upper()}* ({coin.get('name', '')}) — Score `{gem['score']}/100`\n"
                    f"Цена: {fmt_price(safe_float(coin.get('current_price')))} | Капа: {fmt_b(safe_float(coin.get('market_cap')))}\n"
                    f"24ч: {safe_float(coin.get('price_change_percentage_24h')):+.1f}% | 7д: {safe_float(coin.get('price_change_percentage_7d_in_currency')):+.1f}% | ATH: {gem['ath_drop']:.1f}%\n"
                    f"{gem['potential']} | Риск: {gem['risk']}\n"
                    f"Сигналы: {'; '.join(gem['signals'])}"
                )
            lines.append("\n⚠️ Это высокорисковые идеи. Обязательно проверяй ликвидность и токеномику.")
            return "\n\n".join(lines)
        except Exception:
            return "❌ Не удалось найти gems. Попробуй позже."

    async def detect_entry_signal(self, symbol: str) -> dict[str, Any]:
        try:
            coin_id = await self._resolve_id(symbol)
            data = await self._get_json(
                f"{COINGECKO}/coins/{coin_id}/market_chart",
                {"vs_currency": "usd", "days": 30, "interval": "daily"},
            )
            if not data:
                return {"symbol": symbol.upper(), "patterns": [], "error": "Нет данных графика."}

            prices = [safe_float(p[1]) for p in data.get("prices", [])]
            volumes = [safe_float(v[1]) for v in data.get("total_volumes", [])]
            if len(prices) < 25:
                return {"symbol": symbol.upper(), "patterns": [], "error": "Недостаточно данных."}

            rsi_prev = calc_rsi(prices[:-1])
            rsi_current = calc_rsi(prices)
            macd_prev, signal_prev, _ = calc_macd(prices[:-1])
            macd_current, signal_current, _ = calc_macd(prices)
            bb_lower_prev, _, _ = calc_bollinger(prices[:-1])
            bb_lower_current, _, _ = calc_bollinger(prices)
            price_prev = prices[-2]
            price_current = prices[-1]
            current_volume = volumes[-1] if volumes else 0
            avg_volume = sum(volumes[-8:-1]) / max(len(volumes[-8:-1]), 1) if len(volumes) >= 8 else current_volume
            change_24h = ((price_current - price_prev) / price_prev * 100) if price_prev else 0

            patterns = []
            if rsi_prev > 70 and rsi_current < 65:
                patterns.append({"name": "RSI разворот вниз", "type": "SELL", "strength": "strong"})
            if rsi_prev < 30 and rsi_current > 35:
                patterns.append({"name": "RSI разворот вверх", "type": "BUY", "strength": "strong"})
            if (macd_prev or 0) < (signal_prev or 0) and (macd_current or 0) > (signal_current or 0):
                patterns.append({"name": "MACD Golden Cross", "type": "BUY", "strength": "very_strong"})
            if bb_lower_prev is not None and bb_lower_current is not None and price_prev < bb_lower_prev and price_current > bb_lower_current:
                patterns.append({"name": "Отскок от нижней BB", "type": "BUY", "strength": "strong"})
            if current_volume > avg_volume * 3 and change_24h > 0:
                patterns.append({"name": "Аномальный объём при росте", "type": "BUY", "strength": "medium"})

            return {"symbol": symbol.upper(), "patterns": patterns, "price": price_current, "change_24h": change_24h}
        except Exception:
            return {"symbol": symbol.upper(), "patterns": [], "error": "Ошибка анализа точки входа."}

    async def get_top_strong_buys(self, limit: int = 200, threshold: int = 40) -> list[dict[str, Any]]:
        coins = await self._get_market_snapshot(limit)
        strong: list[dict[str, Any]] = []
        for coin in coins:
            score_data = self.calculate_signal_score(
                rsi=coin["rsi"],
                macd=coin["change_24h"] / 10,
                macd_signal=coin["change_7d"] / 10,
                price=coin["price"],
                bb_lower=coin["price"] * 0.97,
                bb_upper=coin["price"] * 1.03,
                ema20=coin["price"] * (1 + coin["change_24h"] / 1000),
                ema50=coin["price"] * (1 + coin["change_7d"] / 1000),
                change_24h=coin["change_24h"],
                change_7d=coin["change_7d"],
                vol_ratio=coin["vol_ratio"] * 100,
            )
            if score_data["score"] >= threshold:
                strong.append({"symbol": coin["symbol"], "name": coin["name"], "score": score_data["score"], "price": coin["price"], "change_24h": coin["change_24h"]})
        strong.sort(key=lambda item: item["score"], reverse=True)
        return strong[:10]

    async def _get_btc_dominance(self) -> Optional[float]:
        try:
            g = await self._get_json(f"{COINGECKO}/global")
            if not g:
                return None
            pct = ((g.get("data") or {}).get("market_cap_percentage") or {}).get("btc")
            return safe_float(pct) if pct is not None else None
        except Exception:
            return None

    async def _pick_gems_for_broadcast(self, n: int = 2) -> list[dict[str, Any]]:
        try:
            trending = await self._get_json(f"{COINGECKO}/search/trending")
            trending_ids = {
                item.get("item", {}).get("id", "")
                for item in (trending or {}).get("coins", [])
                if item.get("item", {}).get("id")
            }
            lowcap = await self._get_json(
                f"{COINGECKO}/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "id_asc",
                    "per_page": 120,
                    "page": 6,
                    "sparkline": "false",
                    "price_change_percentage": "24h,7d",
                },
            )
            scored: list[tuple[dict[str, Any], dict[str, Any], int]] = []
            for coin in lowcap or []:
                mcap = safe_float(coin.get("market_cap"))
                if mcap <= 0 or mcap >= 150_000_000:
                    continue
                gem = self.calculate_gem_score(coin)
                if gem["score"] < 42:
                    continue
                bonus = 6 if coin.get("id") in trending_ids else 0
                scored.append((coin, gem, gem["score"] + bonus))
            scored.sort(key=lambda x: x[2], reverse=True)
            plat_map = {
                "solana": "Solana",
                "ethereum": "Ethereum",
                "binance-smart-chain": "BSC",
                "base": "Base",
                "arbitrum-one": "Arbitrum",
            }
            out: list[dict[str, Any]] = []
            for coin, gem, _ in scored:
                sym = coin.get("symbol", "").upper()
                if sym in {"BTC", "ETH", "SOL", "USDT", "USDC", "BNB", "STETH"}:
                    continue
                mcap = safe_float(coin.get("market_cap"))
                plat_raw = str(coin.get("asset_platform_id") or "")
                chain = plat_map.get(plat_raw.lower(), plat_raw or "—")
                pot = gem["potential"].replace("🚀", "").replace("📈", "").replace("📊", "").strip()
                out.append({"symbol": sym, "chain": chain, "score": gem["score"], "pot": pot, "mcap": mcap})
                if len(out) >= n:
                    break
            return out
        except Exception:
            return []

    async def auto_broadcast_message(self) -> str:
        try:
            now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            coins = await self._get_market_snapshot(100)
            if not coins:
                return ""
            fg = await self._get_fear_greed()
            ranked: list[tuple[dict[str, Any], int]] = []
            for c in coins:
                st, sc = self._coin_signal(c, fg["value"])
                if st == "buy":
                    ranked.append((c, sc))
            ranked.sort(key=lambda x: x[1], reverse=True)
            top3 = ranked[:3]
            lines = [f"🔔 АВТО-СКАНЕР — [{now}]\n", "🟢 ТОП СИГНАЛЫ НА ПОКУПКУ:"]
            if not top3:
                lines.append("— Сейчас мало явных buy-сигналов в топ-100 по правилам бота.")
            for i, (c, sc) in enumerate(top3, 1):
                p = c["price"]
                rsi = c["rsi"]
                entry = p
                stp = p * 0.93
                tgt = p * 1.2
                lines.append(
                    f"{i}. {c['symbol']} — RSI {rsi:.0f}, Score {sc} | Вход {fmt_price(entry)} | Стоп {fmt_price(stp)} | Цель {fmt_price(tgt)}"
                )
            gems = await self._pick_gems_for_broadcast(2)
            lines.append("")
            lines.append("💎 GEM FINDER:")
            if gems:
                for g in gems:
                    lines.append(
                        f"- {g['symbol']} ({g['chain']}) — Score {g['score']}, {g['pot']}, Капа {fmt_b(g['mcap'])}"
                    )
            else:
                lines.append("- Гемы по фильтрам не найдены — попробуй позже.")
            lines.append("")
            lines.append("📱 Подробный анализ: просто напиши символ монеты")
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_snapshot_analysis(
        self,
        coin: dict[str, Any],
        fg: dict[str, Any],
        symbol: str,
        detail_unavailable: bool = False,
    ) -> str:
        signal_type, score = self._coin_signal(coin, fg["value"])
        signal_label = {
            "buy": "🟢 ПОКУПАТЬ",
            "sell": "🔴 ПРОДАВАТЬ",
            "wait": "🟡 ЖДАТЬ",
        }[signal_type]
        signal_text = {
            "buy": "Импульс и объем выглядят конструктивно.",
            "sell": "Монета перегрета или под давлением.",
            "wait": "Явного преимущества у входа сейчас нет.",
        }[signal_type]
        prefix = "⚠️ Расширенный анализ временно недоступен, показываю рыночный срез.\n\n" if detail_unavailable else ""

        return (
            f"{prefix}"
            f"📊 *{coin['name']} ({symbol.upper()})* — быстрый анализ\n\n"
            f"💰 *Цена:* {fmt_price(coin['price'])}\n"
            f"📈 *Изменение:* {coin['change_24h']:+.2f}% (24ч) | {coin['change_7d']:+.2f}% (7д)\n"
            f"🏦 *Капитализация:* {fmt_b(coin['market_cap'])}\n"
            f"📦 *Объем 24ч:* {fmt_b(coin['volume'])} ({coin['vol_ratio'] * 100:.1f}% от капы)\n"
            f"🔥 *RSI proxy:* `{coin['rsi']}`\n"
            f"😐 *Fear & Greed:* `{fg['value']}` — {fg['label']}\n\n"
            f"*{signal_label}* — score `{score}`\n"
            f"_{signal_text}_\n\n"
            f"🎯 *Точка входа:* {fmt_price(coin['price'])}\n"
            f"🛑 *Стоп:* {fmt_price(coin['price'] * 0.94)}\n"
            f"✅ *TP1:* {fmt_price(coin['price'] * 1.05)}\n"
            f"✅ *TP2:* {fmt_price(coin['price'] * 1.10)}\n"
            f"\n_/scan и /signals для общего рынка_"
        )

    async def full_analysis(self, symbol: str) -> str:
        coin_id = await self._resolve_id(symbol)

        market_task = self._get_json(
            f"{COINGECKO}/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "true",
                "developer_data": "true",
            },
        )
        prices_task = self._get_prices(coin_id, 60)
        fear_greed_task = self._get_fear_greed()
        defi_task = self._get_defi_llama(symbol)
        btc_dom_task = self._get_btc_dominance()

        data, prices, fg, defi, btc_dom = await asyncio.gather(
            market_task, prices_task, fear_greed_task, defi_task, btc_dom_task
        )

        snapshot_coin = await self._find_snapshot_coin(symbol, coin_id)

        if not data:
            if snapshot_coin:
                return self._build_snapshot_analysis(snapshot_coin, fg, symbol, detail_unavailable=True)
            return (
                f"❌ Монета *{symbol}* не найдена.\n"
                f"Попробуй: BTC, ETH, SOL, RENDER, TAO, WIF..."
            )

        md = data["market_data"]
        price = safe_float(md["current_price"]["usd"])
        cap = safe_float(md["market_cap"]["usd"])
        volume = safe_float(md["total_volume"]["usd"])
        change_1h = safe_float(md.get("price_change_percentage_1h_in_currency"))
        change_24h = safe_float(md.get("price_change_percentage_24h"))
        change_7d = safe_float(md.get("price_change_percentage_7d"))
        change_30d = safe_float(md.get("price_change_percentage_30d"))
        ath = safe_float(md["ath"]["usd"])
        ath_change = safe_float((md.get("ath_change_percentage") or {}).get("usd"))
        vol_ratio = (volume / cap) if cap > 0 else 0
        rank = data.get("market_cap_rank")
        rank_txt = f"#{rank}" if rank else "—"

        rsi = calc_rsi(prices) if len(prices) >= 15 else (snapshot_coin["rsi"] if snapshot_coin else 50.0)
        macd_val, macd_signal, macd_hist = calc_macd(prices) if len(prices) >= 30 else (None, None, None)
        bb_low, bb_mid, bb_high = calc_bollinger(prices) if len(prices) >= 20 else (None, None, None)
        ema20 = calc_ema(prices, 20)
        ema50 = calc_ema(prices, 50)
        ema20_val = ema20[-1] if ema20 else None
        ema50_val = ema50[-1] if ema50 else None

        signal_data = self.calculate_signal_score(
            rsi=rsi,
            macd=macd_val or 0.0,
            macd_signal=macd_signal or 0.0,
            price=price,
            bb_lower=bb_low or price,
            bb_upper=bb_high or price,
            ema20=ema20_val or price,
            ema50=ema50_val or price,
            change_24h=change_24h,
            change_7d=change_7d,
            vol_ratio=vol_ratio * 100,
        )

        fear_value = fg["value"]
        fear_emoji = "😱" if fear_value < 25 else "😨" if fear_value < 45 else "😐" if fear_value < 55 else "😊" if fear_value < 75 else "🤑"

        macd_bias = "Медвежий ↘️" if (macd_hist or 0) < 0 else "Бычий ↗️"
        macd_hist_txt = f"{macd_hist:+.6f}" if macd_hist is not None else "n/a"
        macd_line = f"MACD: {macd_bias} (гист: `{macd_hist_txt}`)" if macd_val is not None else "MACD: недостаточно данных"

        if bb_low and bb_mid and bb_high and price > 0:
            span = bb_high - bb_low
            pos = (price - bb_low) / span if span else 0.5
            if pos < 0.25:
                bb_pos = "Нижняя зона канала"
            elif pos > 0.75:
                bb_pos = "Верхняя зона канала"
            else:
                bb_pos = "Середина канала"
        else:
            bb_pos = "Нет данных"

        vmc_pct = vol_ratio * 100
        if vmc_pct < 2:
            vol_comment = "низкий"
        elif vmc_pct < 10:
            vol_comment = "нормальный"
        else:
            vol_comment = "повышенный"

        ath_drop_pct = ((price - ath) / ath * 100) if ath > 0 else 0.0
        score_ui = int(clamp((signal_data["score"] + 100) / 2, 0, 100))

        ai_text = await self.get_ai_analysis(
            symbol.upper(),
            {
                "price": round(price, 8),
                "rsi": rsi,
                "macd": macd_val if macd_val is not None else 0,
                "change_24h": round(change_24h, 2),
                "change_7d": round(change_7d, 2),
                "vol_ratio": round(vol_ratio * 100, 2),
                "fear_greed": fg["value"],
                "tvl": fmt_b(defi["tvl"]) if defi.get("tvl") else "N/A",
            },
        )

        ema_trend = ""
        if ema20_val and ema50_val:
            ema_trend = "📈 Бычий" if ema20_val > ema50_val else "📉 Медвежий"

        entry_low = min(price, signal_data["entry"]) * 0.998
        entry_high = max(price, signal_data["entry"]) * 1.002
        pct_sl = ((signal_data["stop_loss"] - price) / price * 100) if price else 0
        pct_tp1 = ((signal_data["tp1"] - price) / price * 100) if price else 0
        pct_tp2 = ((signal_data["tp2"] - price) / price * 100) if price else 0
        pct_tp3 = ((signal_data["tp3"] - price) / price * 100) if price else 0

        lines = [
            f"📊 *{data['name']} ({symbol.upper()})* {rank_txt}",
            "",
            f"💰 Цена: {fmt_price(price)}",
            f"📈 1ч: {change_1h:+.1f}% | 24ч: {change_24h:+.1f}% | 7д: {change_7d:+.1f}% | 30д: {change_30d:+.1f}%",
            f"🏦 Капа: {fmt_b(cap)} | Объём: {fmt_b(volume)}",
            f"📉 От ATH: {ath_drop_pct:+.1f}% | ATH: {fmt_price(ath)}",
            "",
            "━━━ ТЕХНИЧЕСКИЙ АНАЛИЗ ━━━",
            f"RSI (14): {rsi} — {rsi_signal(rsi)}",
            macd_line,
            f"Bollinger: {bb_pos}",
        ]
        if ema20_val and ema50_val:
            lines.append(f"EMA 20: {fmt_price(ema20_val)} | EMA 50: {fmt_price(ema50_val)}")
            lines.append(f"Тренд EMA: {ema_trend}")

        lines += [
            "",
            "━━━ ОБЪЁМ И ЛИКВИДНОСТЬ ━━━",
            f"Vol/MCap: {vmc_pct:.1f}% ({vol_comment})",
        ]
        if btc_dom is not None:
            lines.append(f"Доминация BTC: {btc_dom:.1f}%")

        lines += [
            "",
            "━━━ ОНЧЕЙН ━━━",
            f"{fear_emoji} Fear & Greed: {fear_value} — {fg['label']}",
        ]
        if defi.get("tvl"):
            tvl_change = safe_float(defi.get("tvl_change"))
            tvl_text = f"+{tvl_change:.1f}%" if tvl_change >= 0 else f"{tvl_change:.1f}%"
            lines.append(f"🏊 TVL: {fmt_b(defi['tvl'])} ({tvl_text} к дню)")

        lines += [
            "",
            f"━━━ СИГНАЛ (Score: {score_ui}/100) ━━━",
            signal_data["signal"],
            "",
            "Причины:",
        ]
        for reason in signal_data["reasons"][:6]:
            lines.append(f"- {reason}")

        lines += [
            "",
            "━━━ ТОЧКИ ━━━",
            f"🎯 Вход: {fmt_price(entry_low)} – {fmt_price(entry_high)}",
            f"🛑 Стоп: {fmt_price(signal_data['stop_loss'])} ({pct_sl:+.0f}%)",
            f"✅ TP1: {fmt_price(signal_data['tp1'])} ({pct_tp1:+.0f}%)",
            f"✅ TP2: {fmt_price(signal_data['tp2'])} ({pct_tp2:+.0f}%)",
            f"✅ TP3: {fmt_price(signal_data['tp3'])} ({pct_tp3:+.0f}%)",
            "",
            "━━━ AI АНАЛИЗ (Groq) ━━━",
            f"🤖 {ai_text}",
        ]

        if vol_ratio > 0.3:
            lines.append("")
            lines.append("⚠️ Аномально высокий объём относительно капы — осторожность с размером позиции.")

        return "\n".join(lines)

    async def market_scan(self) -> str:
        coins, fg = await asyncio.gather(self._get_market_snapshot(200), self._get_fear_greed())
        if not coins:
            return "❌ Ошибка загрузки данных рынка. Попробуй позже."

        ranked = []
        for coin in coins:
            signal_type, signal_score = self._coin_signal(coin, fg["value"])
            ranked.append((coin, signal_type, signal_score))

        buys = sorted(
            [item for item in ranked if item[0]["change_24h"] > -15],
            key=lambda item: (item[2], item[0]["change_7d"], item[0]["vol_ratio"]),
            reverse=True,
        )[:6]
        sells = sorted(
            ranked,
            key=lambda item: (item[2], item[0]["rsi"], -item[0]["change_24h"]),
        )[:6]

        lines = [
            f"🔍 *Скан рынка — {len(coins)} монет*",
            f"😐 *Fear & Greed:* `{fg['value']}` — {fg['label']}\n",
        ]
        lines.append("🟢 *Лучшие кандидаты на рост:*")
        for coin, signal_type, score in buys:
            lines.append(
                f"  ✅ *{coin['symbol']}* — score `{score}` | RSI `{coin['rsi']}` | "
                f"{coin['change_24h']:+.1f}% | {fmt_price(coin['price'])}"
            )

        lines.append("")
        lines.append("🔴 *Монеты под давлением / перегревом:*")
        for coin, signal_type, score in sells:
            marker = "❌" if signal_type == "sell" else "⚠️"
            lines.append(
                f"  {marker} *{coin['symbol']}* — score `{score}` | RSI `{coin['rsi']}` | "
                f"{coin['change_24h']:+.1f}% | {fmt_price(coin['price'])}"
            )

        strong_buys = [item for item in ranked if item[1] == "buy"]
        strong_sells = [item for item in ranked if item[1] == "sell"]
        lines += [
            "",
            f"📌 Сильных buy-сигналов: `{len(strong_buys)}`",
            f"📌 Сильных sell-сигналов: `{len(strong_sells)}`",
            "\n_/analyze SYMBOL для деталей_",
        ]
        return "\n".join(lines)

    async def find_overbought(self) -> str:
        coins = await self._get_market_snapshot(200)
        if not coins:
            return "❌ Ошибка загрузки. Попробуй позже."

        results = [coin for coin in coins if coin["rsi"] >= 68]
        results.sort(key=lambda coin: (coin["rsi"], coin["vol_ratio"], coin["change_24h"]), reverse=True)
        if not results:
            results = sorted(coins, key=lambda coin: (coin["rsi"], coin["change_24h"]), reverse=True)[:8]
            lines = [
                "🟠 *Сильного перегрева не нашлось, но вот лидеры по RSI*\n",
                "_Это монеты, где риск коррекции сейчас выше среднего._\n",
            ]
        else:
            lines = [
                f"🔥 *Перекупленные монеты ({len(results)} из {len(coins)})*\n",
                "_RSI > 68 — высокий риск коррекции_\n",
            ]

        for coin in results[:10]:
            risk = " ⚠️ Памп?" if coin["vol_ratio"] > 0.4 else ""
            lines.append(
                f"🔴 *{coin['symbol']}* — RSI `{coin['rsi']}` | {coin['change_24h']:+.1f}% | "
                f"{fmt_price(coin['price'])} | {fmt_b(coin['market_cap'])}{risk}"
            )
        lines.append("\n_/analyze SYMBOL для точки выхода_")
        return "\n".join(lines)

    async def find_new_potential(self) -> str:
        """Тренды + гейнеры + «дальняя» страница — разнообразные монеты при каждом запросе."""
        try:
            fg, trending, gainers, obscure = await asyncio.gather(
                self._get_fear_greed(),
                self._get_json(f"{COINGECKO}/search/trending"),
                self._get_json(
                    f"{COINGECKO}/coins/markets",
                    {
                        "vs_currency": "usd",
                        "order": "percent_change_24h_desc",
                        "per_page": 50,
                        "page": 1,
                        "sparkline": "false",
                        "price_change_percentage": "24h,7d",
                    },
                ),
                self._get_json(
                    f"{COINGECKO}/coins/markets",
                    {
                        "vs_currency": "usd",
                        "order": "id_asc",
                        "per_page": 100,
                        "page": 5,
                        "sparkline": "false",
                        "price_change_percentage": "24h,7d",
                    },
                ),
            )
            picks: list[dict[str, Any]] = []
            seen_syms: set[str] = set()

            for item in (trending or {}).get("coins", [])[:12]:
                it = item.get("item") or {}
                cid = it.get("id")
                if not cid:
                    continue
                detail = await self._get_json(
                    f"{COINGECKO}/coins/markets",
                    {"vs_currency": "usd", "ids": cid, "sparkline": "false", "price_change_percentage": "24h,7d"},
                )
                if not detail:
                    continue
                c = detail[0]
                sym = str(c.get("symbol", "")).upper()
                if sym in seen_syms:
                    continue
                seen_syms.add(sym)
                vol = safe_float(c.get("total_volume"))
                mcap = safe_float(c.get("market_cap"))
                vr = (vol / mcap * 100) if mcap else 0.0
                why = "🔥 В тренде CoinGecko — повышенное внимание рынка"
                if vr > 40:
                    why += f"; всплеск объёма Vol/MCap {vr:.0f}%"
                picks.append({"raw": c, "why": why, "sym": sym, "volr": vr})

            for c in gainers or []:
                sym = str(c.get("symbol", "")).upper()
                if sym in seen_syms:
                    continue
                seen_syms.add(sym)
                ch = safe_float(c.get("price_change_percentage_24h"))
                vol = safe_float(c.get("total_volume"))
                mcap = safe_float(c.get("market_cap"))
                vr = (vol / mcap * 100) if mcap else 0.0
                why = f"📈 Сильный рост 24ч ({ch:+.1f}%) среди лидеров по %"
                if vr > 35:
                    why += f"; всплеск объёма Vol/MCap {vr:.0f}%"
                picks.append({"raw": c, "why": why, "sym": sym, "volr": vr})

            for c in obscure or []:
                sym = str(c.get("symbol", "")).upper()
                if sym in seen_syms:
                    continue
                seen_syms.add(sym)
                rank = c.get("market_cap_rank")
                why = "🆕 Менее известные активы (страница листинга CoinGecko)"
                if rank:
                    why += f"; ранг капы #{rank}"
                picks.append({"raw": c, "why": why, "sym": sym, "volr": 0.0})

            if not picks:
                return "❌ Не удалось собрать данные. Попробуй через минуту."

            random.shuffle(picks)
            picks.sort(key=lambda x: x["volr"], reverse=True)
            selected = picks[:10]

            lines = ["🆕 *Новые и интересные монеты*\n", "_Источники: тренды + топ гейнеров + «дальняя» страница рынка_\n"]
            for row in selected:
                c = row["raw"]
                sym = row["sym"]
                norm = self._normalize_market_coin(c)
                price = norm["price"]
                ch24 = norm["change_24h"]
                sig, sc = self._coin_signal(norm, fg["value"])
                quick = "🟢 Покупка" if sig == "buy" else "🟡 Осторожно" if sig == "wait" else "🔴 Риск"
                stop = price * 0.92 if price > 0 else 0.0
                lines.append(
                    f"*{sym}* ({c.get('name', '')})\n"
                    f"• Почему: {row['why']}\n"
                    f"• Быстрый сигнал: {quick} (score рынка `{sc}`)\n"
                    f"• Цена: {fmt_price(price)} | 24ч: {ch24:+.1f}%\n"
                    f"• Вход ориентир: {fmt_price(price)} | Стоп: {fmt_price(stop)} (-8%)\n"
                )
            lines.append("⚠️ Высокий риск. DYOR. `/analyze SYMBOL` — детальный разбор.")
            return "\n".join(lines)
        except Exception:
            return "❌ Ошибка при поиске новых монет. Попробуй позже."

    async def find_dump_risk(self) -> str:
        coins, fg = await asyncio.gather(self._get_market_snapshot(220), self._get_fear_greed())
        if not coins:
            return "❌ Ошибка загрузки. Попробуй позже."

        risks = []
        for coin in coins:
            risk_score = 0
            reasons = []
            if coin["rsi"] > 74:
                risk_score += 2
                reasons.append(f"RSI {coin['rsi']}")
            if coin["vol_ratio"] > 0.45:
                risk_score += 2
                reasons.append(f"Vol/Cap {coin['vol_ratio'] * 100:.0f}%")
            elif coin["vol_ratio"] > 0.30:
                risk_score += 1
            if coin["change_24h"] > 18:
                risk_score += 2
                reasons.append(f"Рост {coin['change_24h']:.0f}% за 24ч")
            elif coin["change_24h"] < -10:
                risk_score += 1
                reasons.append(f"Слив {coin['change_24h']:.0f}% за 24ч")
            if coin["change_7d"] > 35:
                risk_score += 1
                reasons.append("Сильный недельный перегрев")
            if fg["value"] > 75:
                risk_score += 1
                reasons.append("Экстремальная жадность")

            if risk_score >= 3:
                risks.append({**coin, "risk_score": risk_score, "reasons": reasons})

        risks.sort(key=lambda coin: (coin["risk_score"], coin["vol_ratio"], coin["change_24h"]), reverse=True)
        if not risks:
            risks = []
            for coin in coins:
                risk_score = 0
                reasons = []
                if coin["rsi"] > 65:
                    risk_score += 1
                    reasons.append(f"RSI {coin['rsi']}")
                if coin["vol_ratio"] > 0.25:
                    risk_score += 1
                    reasons.append(f"Vol/Cap {coin['vol_ratio'] * 100:.0f}%")
                if coin["change_24h"] > 10:
                    risk_score += 1
                    reasons.append(f"Рост {coin['change_24h']:.0f}% за 24ч")
                if risk_score:
                    risks.append({**coin, "risk_score": risk_score, "reasons": reasons})
            risks.sort(key=lambda coin: (coin["risk_score"], coin["change_24h"], coin["vol_ratio"]), reverse=True)
            header = "🟠 *Зона риска — мягкие сигналы возможной раздачи*"
        else:
            header = f"⚠️ *Риск дампа — {len(risks)} монет*"

        lines = [
            header,
            f"Fear & Greed: `{fg['value']}` — {fg['label']}\n",
            "_Высокий RSI + аномальный объём + резкий рост_\n",
        ]
        for coin in risks[:8]:
            lines.append(
                f"💣 *{coin['symbol']}* — {fmt_price(coin['price'])} | {fmt_b(coin['market_cap'])}\n"
                f"   _{' | '.join(coin['reasons'])}_"
            )
        lines.append("\n_/analyze SYMBOL для уровней выхода_")
        return "\n".join(lines)

    async def top_signals(self) -> str:
        coins, fg = await asyncio.gather(self._get_market_snapshot(200), self._get_fear_greed())
        if not coins:
            return "❌ Ошибка загрузки. Попробуй позже."

        ranked = []
        for coin in coins:
            signal_type, score = self._coin_signal(coin, fg["value"])
            ranked.append((coin, signal_type, score))

        buys = sorted(
            ranked,
            key=lambda item: (item[2], item[0]["change_7d"], item[0]["vol_ratio"]),
            reverse=True,
        )[:6]
        sells = sorted(
            ranked,
            key=lambda item: (item[2], item[0]["rsi"], -item[0]["change_24h"]),
        )[:6]

        lines = [
            "📈 *Топ сигналы рынка*",
            f"😐 Fear & Greed: `{fg['value']}` — {fg['label']}\n",
            "🟢 *Кандидаты на рост:*",
        ]
        for coin, signal_type, score in buys:
            marker = "✅" if signal_type == "buy" else "🟡"
            lines.append(
                f"  {marker} *{coin['symbol']}* — score `{score}` | RSI `{coin['rsi']}` | "
                f"{coin['change_24h']:+.1f}% | {fmt_price(coin['price'])}"
            )

        lines.append("\n🔴 *Кандидаты на падение / фиксацию:*")
        for coin, signal_type, score in sells:
            marker = "❌" if signal_type == "sell" else "⚠️"
            lines.append(
                f"  {marker} *{coin['symbol']}* — score `{score}` | RSI `{coin['rsi']}` | "
                f"{coin['change_24h']:+.1f}% | {fmt_price(coin['price'])}"
            )

        lines.append("\n_/analyze SYMBOL для деталей_")
        return "\n".join(lines)
