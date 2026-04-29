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
from pathlib import Path
from typing import Any, Optional

import aiohttp

COINGECKO = "https://api.coingecko.com/api/v3"
DEFILLAMA = "https://api.llama.fi"
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

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
        macd_val, _, macd_hist = calc_macd(prices) if len(prices) >= 30 else (None, None, None)
        bb_low, bb_mid, bb_high = calc_bollinger(prices) if len(prices) >= 20 else (None, None, None)
        ema20 = calc_ema(prices, 20)
        ema50 = calc_ema(prices, 50)
        ema20_val = ema20[-1] if ema20 else None
        ema50_val = ema50[-1] if ema50 else None

        onchain_score = 0
        if defi.get("tvl_change") and defi["tvl_change"] > 5:
            onchain_score += 1
        if defi.get("flows") and defi["flows"] > 0:
            onchain_score += 1
        if fg["value"] < 25:
            onchain_score += 1
        elif fg["value"] > 75:
            onchain_score -= 1

        signal_label, signal_desc = overall_signal(
            rsi, macd_hist, price, bb_low, bb_mid, bb_high, vol_ratio, onchain_score
        )

        if bb_low and bb_high:
            stop_loss = round(price * 0.94, 8)
            tp1 = round(bb_mid, 8)
            tp2 = round(bb_high, 8)
            tp3 = round(price * 1.15, 8)
            entry_zone = f"{fmt_price(price * 0.98)} – {fmt_price(price)}"
        else:
            stop_loss = round(price * 0.94, 8)
            tp1 = round(price * 1.05, 8)
            tp2 = round(price * 1.10, 8)
            tp3 = round(price * 1.15, 8)
            entry_zone = fmt_price(price)

        fear_value = fg["value"]
        fear_emoji = "😱" if fear_value < 25 else "😨" if fear_value < 45 else "😐" if fear_value < 55 else "😊" if fear_value < 75 else "🤑"

        macd_text = f"`{macd_val}` (гист: `{macd_hist}`)" if macd_val is not None else "N/A"
        bb_text = f"`{fmt_price(bb_low)}` / `{fmt_price(bb_mid)}` / `{fmt_price(bb_high)}`" if bb_low else "N/A"

        lines = [
            f"📊 *{data['name']} ({symbol.upper()})* — Полный анализ",
            "",
            f"💰 *Цена:* {fmt_price(price)}",
            f"📈 *Изменение:* {change_24h:+.2f}% (24ч) | {change_7d:+.2f}% (7д) | {change_30d:+.2f}% (30д)",
            f"🏦 *Капитализация:* {fmt_b(cap)}",
            f"📦 *Объём 24ч:* {fmt_b(volume)} ({vol_ratio * 100:.1f}% от кап)",
            f"🏆 *ATH:* {fmt_price(ath)} (сейчас {ath_change:.1f}%)",
            "",
            "━━━ ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ ━━━",
            f"*RSI (14):* `{rsi}` — {rsi_signal(rsi)}",
            f"*MACD:* {macd_text}",
            f"*Bollinger Bands:* {bb_text}",
        ]

        if ema20_val and ema50_val:
            trend = "📈 Бычий" if ema20_val > ema50_val else "📉 Медвежий"
            lines.append(f"*EMA 20/50:* `{fmt_price(ema20_val)}` / `{fmt_price(ema50_val)}` — {trend}")

        lines += [
            "",
            "━━━ ОНЧЕЙН ДАННЫЕ ━━━",
            f"{fear_emoji} *Fear & Greed:* `{fear_value}` — {fg['label']}",
        ]

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
            "━━━ СИГНАЛ ━━━",
            f"*{signal_label}*",
            f"_{signal_desc}_",
            "",
            "━━━ ТОЧКИ ━━━",
            f"🎯 *Зона входа:* {entry_zone}",
            f"🛑 *Стоп-лосс:* {fmt_price(stop_loss)} (-6%)",
            f"✅ *TP1:* {fmt_price(tp1)}",
            f"✅ *TP2:* {fmt_price(tp2)}",
            f"✅ *TP3:* {fmt_price(tp3)} (+15%)",
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
