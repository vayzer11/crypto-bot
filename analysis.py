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

MEME_KEYWORDS = [
    "pepe", "doge", "shib", "floki", "bonk", "wif", "cat", "dog", "frog", "moon", "ape",
    "baby", "elon", "trump", "maga", "wojak", "chad", "based", "brett", "popcat", "neiro",
    "pnut", "goat", "mew", "turbo", "mog", "act", "banana",
]
DEFI_KEYWORDS = [
    "swap", "finance", "protocol", "lending", "vault", "yield", "dao", "governance", "uniswap",
    "aave", "curve", "maker", "compound", "dydx", "gmx", "pendle",
]

SYMBOL_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
    "AVAX": "avalanche-2", "DOT": "polkadot", "MATIC": "matic-network",
    "ATOM": "cosmos", "NEAR": "near", "ICP": "internet-computer",
    "FIL": "filecoin", "TRX": "tron", "TON": "the-open-network",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "XMR": "monero",
    "XLM": "stellar", "VET": "vechain", "HBAR": "hedera-hashgraph",
    "ALGO": "algorand", "XTZ": "tezos", "EOS": "eos",
    "FLOW": "flow", "EGLD": "elrond-erd-2", "ZEC": "zcash",
    "DASH": "dash", "ZIL": "zilliqa", "WAVES": "waves",
    "NEO": "neo", "IOTA": "iota", "CFX": "conflux-token",
    "ROSE": "oasis-network", "STX": "blockstack", "KAVA": "kava",
    "OP": "optimism", "ARB": "arbitrum", "STRK": "starknet",
    "TIA": "celestia", "MANTA": "manta-network", "ZETA": "zetachain",
    "DYM": "dymension", "IMX": "immutable-x",
    "RENDER": "render-token", "RNDR": "render-token",
    "FET": "fetch-ai", "TAO": "bittensor",
    "WLD": "worldcoin-wld", "AIOZ": "aioz-network",
    "VIRTUAL": "virtual-protocol", "ARKM": "arkham",
    "GRT": "the-graph", "OCEAN": "ocean-protocol",
    "AGIX": "singularitynet",
    "LINK": "chainlink", "UNI": "uniswap", "AAVE": "aave",
    "CRV": "curve-dao-token", "MKR": "maker", "SNX": "havven",
    "COMP": "compound-governance-token", "1INCH": "1inch",
    "SUSHI": "sushi", "YFI": "yearn-finance", "BAL": "balancer",
    "LDO": "lido-dao", "RUNE": "thorchain", "CAKE": "pancakeswap-token",
    "GMX": "gmx", "DYDX": "dydx", "PENDLE": "pendle",
    "ENA": "ethena", "BAND": "band-protocol", "ANKR": "ankr",
    "SKL": "skale", "STORJ": "storj", "ZRX": "0x",
    "BAT": "basic-attention-token", "ENJ": "enjincoin",
    "SAND": "the-sandbox", "MANA": "decentraland",
    "SUI": "sui", "APT": "aptos", "SEI": "sei-network",
    "JUP": "jupiter-exchange-solana", "JTO": "jito-governance-token",
    "PYTH": "pyth-network", "WIF": "dogwifcoin", "BONK": "bonk",
    "ORCA": "orca", "RAY": "raydium",
    "PEPE": "pepe", "SHIB": "shiba-inu", "FLOKI": "floki",
    "POPCAT": "popcat", "PNUT": "peanut-the-squirrel",
    "MOG": "mog-coin", "NEIRO": "neiro-on-eth",
    "PENGU": "pudgy-penguins", "TRUMP": "official-trump",
    "MELANIA": "melania-meme", "BRETT": "based-brett",
    "TURBO": "turbo", "DOGE": "dogecoin",
    "AXS": "axie-infinity", "GALA": "gala",
    "MAGIC": "magic", "BLUR": "blur",
    "ENS": "ethereum-name-service", "ILV": "illuvium",
    "FTM": "fantom", "INJ": "injective-protocol",
    "CRO": "crypto-com-chain", "OKB": "okb",
    "KCS": "kucoin-shares", "LUNA": "terra-luna-2",
    "LUNC": "terra-luna", "HOT": "holotoken",
    "CHZ": "chiliz", "THETA": "theta-token",
    "ONE": "harmony", "CELO": "celo",
    "GLMR": "moonbeam", "KSM": "kusama",
}


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
        if symbol.upper() in SYMBOL_MAP:
            return SYMBOL_MAP[symbol.upper()]
        data = await self._get_json(f"{COINGECKO}/search", {"query": symbol})
        if data and data.get("coins"):
            for coin in data["coins"][:5]:
                if coin.get("symbol", "").upper() == symbol.upper():
                    return coin["id"]
            return data["coins"][0]["id"]
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

    async def get_ai_analysis(self, symbol: str, data: dict[str, Any]) -> str:
        if not ANTHROPIC_API_KEY:
            return "Ключ ANTHROPIC_API_KEY не задан, AI-анализ недоступен."
        prompt = f"""You are a professional crypto trader. Analyze {symbol}:
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
            "messages": [{"role": "user", "content": prompt}],
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
                            "price_change_percentage": "1h,24h,7d",
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

        data, prices, fg, defi = await asyncio.gather(
            market_task, prices_task, fear_greed_task, defi_task
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
        change_24h = safe_float(md.get("price_change_percentage_24h"))
        change_7d = safe_float(md.get("price_change_percentage_7d"))
        change_30d = safe_float(md.get("price_change_percentage_30d"))
        ath = safe_float(md["ath"]["usd"])
        ath_change = safe_float((md.get("ath_change_percentage") or {}).get("usd"))
        vol_ratio = (volume / cap) if cap > 0 else 0

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

        macd_text = f"`{macd_val}` (гист: `{macd_hist}`)" if macd_val is not None else "N/A"
        bb_text = f"`{fmt_price(bb_low)}` / `{fmt_price(bb_mid)}` / `{fmt_price(bb_high)}`" if bb_low else "N/A"

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

        lines = [
            f"📊 *{data['name']} ({symbol.upper()})*",
            "",
            f"💰 Цена: *{fmt_price(price)}*",
            f"📈 24ч: *{change_24h:+.2f}%* | 7д: *{change_7d:+.2f}%* | 30д: *{change_30d:+.2f}%*",
            f"🏦 Капитализация: *{fmt_b(cap)}*",
            f"📦 Объём 24ч: *{fmt_b(volume)}* ({vol_ratio * 100:.1f}% от капы)",
            f"🏆 ATH: *{fmt_price(ath)}* ({ath_change:.1f}%)",
            "",
            "━━━ Индикаторы ━━━",
            f"RSI(14): `{rsi}` — {rsi_signal(rsi)}",
            f"MACD: {macd_text}",
            f"Bollinger: {bb_text}",
        ]

        if ema20_val and ema50_val:
            trend = "📈 Бычий" if ema20_val > ema50_val else "📉 Медвежий"
            lines.append(f"*EMA 20/50:* `{fmt_price(ema20_val)}` / `{fmt_price(ema50_val)}` — {trend}")

        lines += ["", "━━━ Ончейн ━━━", f"{fear_emoji} Fear & Greed: `{fear_value}` — {fg['label']}"]

        if defi.get("tvl"):
            tvl_change = safe_float(defi.get("tvl_change"))
            tvl_text = f"+{tvl_change:.1f}%" if tvl_change >= 0 else f"{tvl_change:.1f}%"
            lines.append(f"🏊 *TVL (DeFiLlama):* {fmt_b(defi['tvl'])} ({tvl_text} за 24ч)")
            if defi.get("flows") is not None:
                flow_emoji = "📥" if defi["flows"] >= 0 else "📤"
                lines.append(f"{flow_emoji} *Потоки ликвидности (7д):* {fmt_b(abs(defi['flows']))}")

        sentiment = data.get("sentiment_votes_up_percentage")
        developer_score = data.get("developer_score")
        if sentiment:
            lines.append(f"💬 *Настроение рынка:* `{sentiment:.0f}%` позитивных")
        if developer_score:
            lines.append(f"👨‍💻 *Dev Score:* `{developer_score:.1f}/100`")

        lines += [
            "",
            "━━━ Сигнал ━━━",
            f"{signal_data['signal']} *(Score: {signal_data['score']}/100)*",
            "Причины:",
        ]
        for reason in signal_data["reasons"]:
            lines.append(f"• {reason}")
        lines += [
            "",
            "Точки:",
            f"🎯 Вход: *{fmt_price(signal_data['entry'])}*",
            f"🛑 SL: *{fmt_price(signal_data['stop_loss'])}*",
            f"✅ TP1: *{fmt_price(signal_data['tp1'])}*",
            f"✅ TP2: *{fmt_price(signal_data['tp2'])}*",
            f"✅ TP3: *{fmt_price(signal_data['tp3'])}*",
            "",
            "🤖 *AI Анализ (Claude):*",
            ai_text,
        ]

        if vol_ratio > 0.3:
            lines.append("\n⚠️ *Аномально высокий объём! Возможна манипуляция.*")

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
        coins = await self._get_market_snapshot(220)
        if not coins:
            return "❌ Ошибка загрузки. Попробуй позже."

        results = []
        for coin in coins:
            if coin["market_cap"] < 10_000_000:
                continue

            score = 0
            if 20 <= (coin["rank"] or 9999) <= 180:
                score += 2
            if coin["market_cap"] >= 100_000_000:
                score += 1
            if coin["vol_ratio"] >= 0.14:
                score += 2
            elif coin["vol_ratio"] >= 0.08:
                score += 1
            if coin["change_24h"] > 4:
                score += 1
            if coin["change_7d"] > 8:
                score += 2
            elif coin["change_7d"] > 2:
                score += 1
            if coin["rsi"] < 66:
                score += 1

            if score >= 4:
                results.append({**coin, "score": score})

        results.sort(
            key=lambda coin: (coin["score"], coin["vol_ratio"], coin["change_7d"], -(coin["rank"] or 9999)),
            reverse=True,
        )
        if not results:
            return "❌ Ошибка загрузки. Попробуй позже."

        lines = [
            "🚀 *Монеты с потенциалом роста*\n",
            "_Критерии: тренд + объём + капитализация_\n",
        ]
        for coin in results[:10]:
            lines.append(
                f"✨ *{coin['symbol']}* — {fmt_price(coin['price'])} | {coin['change_24h']:+.1f}% | "
                f"Кап: {fmt_b(coin['market_cap'])} | Vol: {coin['vol_ratio'] * 100:.0f}% | score `{coin['score']}`"
            )
        lines.append("\n_/analyze SYMBOL для анализа_")
        return "\n".join(lines)

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
