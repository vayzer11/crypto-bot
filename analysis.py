"""
analysis.py - market intelligence, screening, news, and coin analysis helpers.

Built around free/public-friendly endpoints first:
- CoinGecko market data
- DeFiLlama TVL
- Alternative.me Fear & Greed
- RSS feeds for crypto headlines
"""

from __future__ import annotations

import asyncio
import html
import os
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import aiohttp

COINGECKO_BASE = (
    "https://pro-api.coingecko.com/api/v3"
    if os.getenv("COINGECKO_USE_PRO", "").lower() in {"1", "true", "yes"}
    else "https://api.coingecko.com/api/v3"
)
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
DEFILLAMA = "https://api.llama.fi"
NEWS_FEEDS = [
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
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
        return f"${price:,.4f}"
    if price >= 0.01:
        return f"${price:,.6f}"
    return f"${price:,.8f}"


def fmt_b(value: float) -> str:
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def fmt_pct(value: float) -> str:
    return f"{value:+.1f}%"


def clean_text(value: str) -> str:
    return " ".join(html.unescape(value or "").split())


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value: Optional[str]) -> Optional[int]:
    parsed = parse_iso_date(value)
    if not parsed:
        return None
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds() // 86400))


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
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
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
        return "🟡 СИЛЬНЫЙ ИМПУЛЬС"
    if rsi >= 40:
        return "🟡 НЕЙТРАЛЬНАЯ ЗОНА"
    if rsi >= 30:
        return "🟢 БЛИЗКО К ПЕРЕПРОДАННОСТИ"
    return "🟢 ПЕРЕПРОДАН"


def estimated_rsi(change_1h: float, change_24h: float, change_7d: float, change_30d: float) -> int:
    raw = 50 + (change_1h * 2.2) + (change_24h * 0.9) + (change_7d * 0.45) + (change_30d * 0.08)
    return int(round(clamp(raw, 5, 95)))


def trend_label(change_24h: float, change_7d: float) -> str:
    if change_24h > 0 and change_7d > 0:
        return "bullish"
    if change_24h < 0 and change_7d < 0:
        return "bearish"
    return "neutral"


class CryptoAnalyzer:
    def __init__(self) -> None:
        self.coingecko_key = COINGECKO_API_KEY
        self.coingecko_base = COINGECKO_BASE

    def _request_headers(self, extra_headers: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = {
            "User-Agent": "CryptoSignalBot/2.0",
            "Accept": "application/json, text/plain, */*",
        }
        if self.coingecko_key:
            key_header = "x-cg-pro-api-key" if "pro-api" in self.coingecko_base else "x-cg-demo-api-key"
            headers[key_header] = self.coingecko_key
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def _get_json(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Optional[Any]:
        timeout = aiohttp.ClientTimeout(total=25)
        try:
            async with aiohttp.ClientSession(headers=self._request_headers(headers), timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 429:
                        await asyncio.sleep(4)
                        async with session.get(url, params=params) as retry:
                            if retry.status == 200:
                                return await retry.json()
                        return None
                    if response.status == 200:
                        return await response.json()
        except Exception:
            return None
        return None

    async def _get_text(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(headers=self._request_headers(headers), timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.text()
        except Exception:
            return None
        return None

    async def _resolve_id(self, symbol: str) -> str:
        symbol = symbol.strip()
        if symbol.upper() in SYMBOL_MAP:
            return SYMBOL_MAP[symbol.upper()]
        if "-" in symbol or symbol.islower():
            return symbol.lower()
        data = await self._get_json(f"{self.coingecko_base}/search", {"query": symbol})
        if data and data.get("coins"):
            for coin in data["coins"][:8]:
                if coin.get("symbol", "").upper() == symbol.upper():
                    return coin["id"]
            return data["coins"][0]["id"]
        return symbol.lower()

    async def _get_prices(self, coin_id: str, days: int = 90) -> list[float]:
        data = await self._get_json(
            f"{self.coingecko_base}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": days, "interval": "daily"},
        )
        if not data:
            return []
        return [safe_float(point[1]) for point in data.get("prices", [])]

    async def _get_fear_greed(self) -> dict[str, Any]:
        data = await self._get_json("https://api.alternative.me/fng/")
        if data and data.get("data"):
            item = data["data"][0]
            return {
                "value": int(item.get("value", 50)),
                "label": item.get("value_classification", "Neutral"),
            }
        return {"value": 50, "label": "Neutral"}

    async def _get_global_market(self) -> dict[str, Any]:
        data = await self._get_json(f"{self.coingecko_base}/global")
        payload = data.get("data", {}) if data else {}
        total_cap = safe_float((payload.get("total_market_cap") or {}).get("usd"))
        total_vol = safe_float((payload.get("total_volume") or {}).get("usd"))
        btc_dom = safe_float((payload.get("market_cap_percentage") or {}).get("btc"))
        active = int(payload.get("active_cryptocurrencies") or 0)
        return {
            "total_market_cap": total_cap,
            "total_volume": total_vol,
            "btc_dominance": btc_dom,
            "active_cryptocurrencies": active,
        }

    async def _get_trending(self) -> list[dict[str, Any]]:
        data = await self._get_json(f"{self.coingecko_base}/search/trending")
        coins = []
        for item in (data or {}).get("coins", [])[:7]:
            payload = item.get("item", {})
            coins.append(
                {
                    "id": payload.get("id"),
                    "name": payload.get("name"),
                    "symbol": (payload.get("symbol") or "").upper(),
                    "rank": payload.get("market_cap_rank"),
                    "price_btc": safe_float(payload.get("price_btc")),
                    "thumb": payload.get("thumb"),
                }
            )
        return coins

    async def _get_defi_llama(self, symbol: str) -> dict[str, Optional[float]]:
        result: dict[str, Optional[float]] = {"tvl": None, "tvl_change": None, "flows": None}
        protocols = await self._get_json(f"{DEFILLAMA}/protocols")
        if not protocols:
            return result
        symbol_lower = symbol.lower()
        protocol = next(
            (
                item
                for item in protocols
                if item.get("symbol", "").lower() == symbol_lower
                or symbol_lower in item.get("name", "").lower()
            ),
            None,
        )
        if not protocol:
            return result
        slug = protocol.get("slug") or protocol.get("name", "").lower().replace(" ", "-")
        detail = await self._get_json(f"{DEFILLAMA}/protocol/{slug}")
        if not detail:
            return result
        tvl_history = detail.get("tvl", [])
        if len(tvl_history) >= 2:
            current = safe_float(tvl_history[-1].get("totalLiquidityUSD"))
            prev = safe_float(tvl_history[-2].get("totalLiquidityUSD"))
            result["tvl"] = current
            result["tvl_change"] = ((current - prev) / prev * 100) if prev else 0
            if len(tvl_history) >= 7:
                week_ago = safe_float(tvl_history[-7].get("totalLiquidityUSD"))
                result["flows"] = current - week_ago
        return result

    def _score_fresh(self, coin: dict[str, Any]) -> int:
        score = 0
        freshness_days = coin.get("freshness_days")
        market_cap = safe_float(coin.get("market_cap"))
        vol_ratio = safe_float(coin.get("vol_ratio"))
        fdv_ratio = safe_float(coin.get("fdv_ratio"))
        ath_gap = safe_float(coin.get("ath_gap_pct"))
        change_7d = safe_float(coin.get("change_7d"))

        if freshness_days is not None and freshness_days <= 120:
            score += 2
        elif freshness_days is not None and freshness_days <= 240:
            score += 1
        if 50_000_000 <= market_cap <= 5_000_000_000:
            score += 1
        if vol_ratio >= 0.12:
            score += 2
        elif vol_ratio >= 0.07:
            score += 1
        if change_7d >= 5:
            score += 1
        if 1 < fdv_ratio <= 2.2:
            score += 1
        if -65 <= ath_gap <= -10:
            score += 1
        return score

    def _score_risk(self, coin: dict[str, Any]) -> tuple[int, list[str]]:
        score = 0
        flags: list[str] = []
        market_cap = safe_float(coin.get("market_cap"))
        vol_ratio = safe_float(coin.get("vol_ratio"))
        fdv_ratio = safe_float(coin.get("fdv_ratio"))
        change_24h = safe_float(coin.get("change_24h"))
        change_7d = safe_float(coin.get("change_7d"))
        ath_gap = safe_float(coin.get("ath_gap_pct"))
        rsi = safe_float(coin.get("rsi"))
        supply_ratio = coin.get("supply_ratio")
        freshness_days = coin.get("freshness_days")

        if market_cap >= 100_000_000:
            score += 1
        if freshness_days is not None and freshness_days <= 120:
            score += 1
            flags.append("свежий листинг/хайп")
        if change_24h <= -8:
            score += 2
            flags.append(f"24ч {fmt_pct(change_24h)}")
        elif change_24h <= -4:
            score += 1
        if change_7d >= 25 and change_24h < 0:
            score += 1
            flags.append("резкий откат после пампа")
        if vol_ratio >= 0.35:
            score += 2
            flags.append(f"объем/кап {vol_ratio * 100:.0f}%")
        elif vol_ratio >= 0.20:
            score += 1
        if fdv_ratio >= 3:
            score += 2
            flags.append(f"FDV/MCap {fdv_ratio:.1f}x")
        elif fdv_ratio >= 2:
            score += 1
        if supply_ratio is not None and supply_ratio <= 0.40:
            score += 2
            flags.append("низкая циркуляция")
        elif supply_ratio is not None and supply_ratio <= 0.60:
            score += 1
        if ath_gap >= -15:
            score += 1
            flags.append("цена близко к ATH")
        if rsi >= 72:
            score += 1
        return score, flags

    def _score_signal(self, coin: dict[str, Any], fear_value: int = 50) -> tuple[str, str, int]:
        score = 50
        rsi = safe_float(coin.get("rsi"))
        change_24h = safe_float(coin.get("change_24h"))
        change_7d = safe_float(coin.get("change_7d"))
        vol_ratio = safe_float(coin.get("vol_ratio"))
        ath_gap = safe_float(coin.get("ath_gap_pct"))
        risk_score = int(coin.get("risk_score") or 0)
        fresh_score = int(coin.get("fresh_score") or 0)

        if rsi <= 30:
            score += 18
        elif rsi <= 40:
            score += 10
        elif rsi >= 75:
            score -= 18
        elif rsi >= 65:
            score -= 10

        if change_24h > 0:
            score += 5
        elif change_24h < -7 and rsi <= 35:
            score += 6
        elif change_24h < -7:
            score -= 6

        if change_7d > 0:
            score += 8
        elif change_7d < -10:
            score -= 8

        if vol_ratio >= 0.18:
            score += 8
        elif vol_ratio >= 0.10:
            score += 4

        if ath_gap <= -35:
            score += 6
        elif ath_gap >= -10:
            score -= 3

        if fear_value <= 28:
            score += 4
        elif fear_value >= 78:
            score -= 4

        score += min(8, fresh_score * 2)
        score -= min(18, risk_score * 2)
        score = int(clamp(score, 1, 99))

        if score >= 68:
            return "buy", "🟢 ПОКУПАТЬ", score
        if score <= 38:
            return "sell", "🔴 ПРОДАВАТЬ", score
        return "wait", "🟡 ЖДАТЬ", score

    def _enrich_coin(self, coin: dict[str, Any]) -> dict[str, Any]:
        market_cap = safe_float(coin.get("market_cap"))
        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))
        fdv = safe_float(coin.get("fully_diluted_valuation"), market_cap)
        circulating = safe_float(coin.get("circulating_supply"))
        total_supply = safe_float(coin.get("total_supply")) or safe_float(coin.get("max_supply"))
        supply_ratio = (circulating / total_supply) if total_supply else None
        ath = safe_float(coin.get("ath"))
        ath_gap_pct = ((price - ath) / ath * 100) if ath else 0.0
        freshness_days = days_since(coin.get("ath_date"))
        change_1h = safe_float(coin.get("price_change_percentage_1h_in_currency"))
        change_24h = safe_float(coin.get("price_change_percentage_24h"))
        change_7d = safe_float(coin.get("price_change_percentage_7d_in_currency"))
        change_30d = safe_float(coin.get("price_change_percentage_30d_in_currency"))
        vol_ratio = (volume / market_cap) if market_cap else 0.0
        fdv_ratio = (fdv / market_cap) if market_cap else 0.0
        rsi = estimated_rsi(change_1h, change_24h, change_7d, change_30d)

        enriched = {
            "id": coin.get("id"),
            "symbol": (coin.get("symbol") or "").upper(),
            "name": coin.get("name"),
            "image": coin.get("image"),
            "rank": coin.get("market_cap_rank"),
            "price": price,
            "market_cap": market_cap,
            "fdv": fdv,
            "volume": volume,
            "change_1h": change_1h,
            "change_24h": change_24h,
            "change_7d": change_7d,
            "change_30d": change_30d,
            "ath": ath,
            "ath_gap_pct": ath_gap_pct,
            "ath_date": coin.get("ath_date"),
            "freshness_days": freshness_days,
            "vol_ratio": vol_ratio,
            "fdv_ratio": fdv_ratio,
            "supply_ratio": supply_ratio,
            "rsi": rsi,
            "trend": trend_label(change_24h, change_7d),
            "last_updated": coin.get("last_updated"),
        }
        enriched["fresh_score"] = self._score_fresh(enriched)
        risk_score, risk_flags = self._score_risk(enriched)
        enriched["risk_score"] = risk_score
        enriched["risk_flags"] = risk_flags
        return enriched

    async def _get_market_page(self, page: int, per_page: int = 250, order: str = "market_cap_desc") -> list[dict[str, Any]]:
        payload = await self._get_json(
            f"{self.coingecko_base}/coins/markets",
            {
                "vs_currency": "usd",
                "order": order,
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
                "price_change_percentage": "1h,24h,7d,30d",
            },
        )
        if not payload:
            return []
        return [self._enrich_coin(item) for item in payload]

    async def get_market_snapshot(self, limit: int = 600, order: str = "market_cap_desc") -> list[dict[str, Any]]:
        page_count = max(1, (limit + 249) // 250)
        pages = await asyncio.gather(
            *[self._get_market_page(page=index + 1, order=order) for index in range(page_count)]
        )
        coins = [coin for page in pages for coin in page]
        deduped: dict[str, dict[str, Any]] = {}
        for coin in coins:
            if coin.get("id"):
                deduped[coin["id"]] = coin
        return list(deduped.values())[:limit]

    async def get_market_overview(self, limit: int = 600) -> dict[str, Any]:
        snapshot_task = self.get_market_snapshot(limit=limit)
        fear_task = self._get_fear_greed()
        global_task = self._get_global_market()
        trending_task = self._get_trending()
        snapshot, fear, global_market, trending = await asyncio.gather(
            snapshot_task,
            fear_task,
            global_task,
            trending_task,
        )
        for coin in snapshot:
            signal_type, signal_label, signal_score = self._score_signal(coin, fear["value"])
            coin["signal_type"] = signal_type
            coin["signal_label"] = signal_label
            coin["signal_score"] = signal_score
            coin["opportunity_score"] = max(
                0,
                int(signal_score + coin["fresh_score"] * 4 - coin["risk_score"] * 3),
            )
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "fear_greed": fear,
            "global": global_market,
            "trending": trending,
            "items": snapshot,
        }

    async def get_coin_dashboard(self, symbol_or_id: str, chart_days: int = 30) -> dict[str, Any]:
        coin_id = await self._resolve_id(symbol_or_id)
        market_task = self._get_json(
            f"{self.coingecko_base}/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "true",
                "developer_data": "true",
            },
        )
        chart_task = self._get_json(
            f"{self.coingecko_base}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": chart_days},
        )
        prices_task = self._get_prices(coin_id, 90)
        fear_task = self._get_fear_greed()
        market, chart, prices, fear, defi = await asyncio.gather(
            market_task,
            chart_task,
            prices_task,
            fear_task,
            self._get_defi_llama(symbol_or_id),
        )
        if not market:
            raise ValueError("coin_not_found")

        market_data = market.get("market_data") or {}
        price = safe_float((market_data.get("current_price") or {}).get("usd"))
        cap = safe_float((market_data.get("market_cap") or {}).get("usd"))
        volume = safe_float((market_data.get("total_volume") or {}).get("usd"))
        fdv = safe_float((market_data.get("fully_diluted_valuation") or {}).get("usd"), cap)
        ath = safe_float((market_data.get("ath") or {}).get("usd"))
        ath_change = safe_float((market_data.get("ath_change_percentage") or {}).get("usd"))
        change_24h = safe_float(market_data.get("price_change_percentage_24h"))
        change_7d = safe_float(market_data.get("price_change_percentage_7d"))
        change_30d = safe_float(market_data.get("price_change_percentage_30d"))
        circulating = safe_float(market_data.get("circulating_supply"))
        total_supply = safe_float(market_data.get("total_supply")) or safe_float(market_data.get("max_supply"))
        supply_ratio = (circulating / total_supply) if total_supply else None
        vol_ratio = (volume / cap) if cap else 0.0
        fdv_ratio = (fdv / cap) if cap else 0.0
        rsi = calc_rsi(prices) if len(prices) >= 15 else 50.0
        macd_val, macd_signal, macd_hist = calc_macd(prices) if len(prices) >= 30 else (None, None, None)
        bb_low, bb_mid, bb_high = calc_bollinger(prices) if len(prices) >= 20 else (None, None, None)
        ema20 = calc_ema(prices, 20)
        ema50 = calc_ema(prices, 50)
        ema20_val = ema20[-1] if ema20 else None
        ema50_val = ema50[-1] if ema50 else None
        ath_gap_pct = ((price - ath) / ath * 100) if ath else 0.0

        profile = {
            "market_cap": cap,
            "vol_ratio": vol_ratio,
            "fdv_ratio": fdv_ratio,
            "change_24h": change_24h,
            "change_7d": change_7d,
            "rsi": rsi,
            "supply_ratio": supply_ratio,
            "ath_gap_pct": ath_gap_pct,
            "freshness_days": days_since(market_data.get("ath_date", {}).get("usd")),
        }
        profile["fresh_score"] = self._score_fresh(profile)
        risk_score, risk_flags = self._score_risk(profile)
        profile["risk_score"] = risk_score
        profile["risk_flags"] = risk_flags
        signal_type, signal_label, signal_score = self._score_signal(profile, fear["value"])

        stop_loss = round(price * 0.94, 8)
        take_profit_1 = round(bb_mid if bb_mid else price * 1.05, 8)
        take_profit_2 = round(bb_high if bb_high else price * 1.11, 8)
        take_profit_3 = round(price * 1.18, 8)
        entry_zone = (
            f"{fmt_price(price * 0.98)} - {fmt_price(price)}"
            if bb_low and bb_high
            else fmt_price(price)
        )

        return {
            "id": market.get("id"),
            "symbol": (market.get("symbol") or "").upper(),
            "name": market.get("name"),
            "description": clean_text((market.get("description") or {}).get("en", ""))[:320],
            "image": (market.get("image") or {}).get("large"),
            "price": price,
            "market_cap": cap,
            "volume": volume,
            "fdv": fdv,
            "vol_ratio": vol_ratio,
            "fdv_ratio": fdv_ratio,
            "supply_ratio": supply_ratio,
            "ath": ath,
            "ath_change_pct": ath_change,
            "ath_gap_pct": ath_gap_pct,
            "change_24h": change_24h,
            "change_7d": change_7d,
            "change_30d": change_30d,
            "rsi": rsi,
            "macd": macd_val,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "bb_low": bb_low,
            "bb_mid": bb_mid,
            "bb_high": bb_high,
            "ema20": ema20_val,
            "ema50": ema50_val,
            "fear_greed": fear,
            "defi": defi,
            "signal_type": signal_type,
            "signal_label": signal_label,
            "signal_score": signal_score,
            "fresh_score": profile["fresh_score"],
            "risk_score": risk_score,
            "risk_flags": risk_flags,
            "entry_zone": entry_zone,
            "stop_loss": stop_loss,
            "tp1": take_profit_1,
            "tp2": take_profit_2,
            "tp3": take_profit_3,
            "chart": (chart or {}).get("prices", []),
            "sentiment_up": safe_float(market.get("sentiment_votes_up_percentage")),
            "developer_score": safe_float(market.get("developer_score")),
            "homepage": next(iter((market.get("links") or {}).get("homepage") or []), ""),
        }

    async def _fetch_feed(self, source: str, url: str) -> list[dict[str, Any]]:
        raw = await self._get_text(url, headers={"Accept": "application/rss+xml, application/xml, text/xml"})
        if not raw:
            return []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []

        items: list[dict[str, Any]] = []
        for node in root.findall(".//item")[:10]:
            title = clean_text(node.findtext("title", ""))
            link = clean_text(node.findtext("link", ""))
            pub_date = clean_text(node.findtext("pubDate", ""))
            description = clean_text(node.findtext("description", ""))[:220]
            published_at = None
            if pub_date:
                try:
                    published_at = parsedate_to_datetime(pub_date)
                except (TypeError, ValueError, IndexError):
                    published_at = None
            if not title or not link:
                continue
            items.append(
                {
                    "source": source,
                    "title": title,
                    "link": link,
                    "description": description,
                    "published_at": published_at.isoformat() if published_at else None,
                }
            )
        return items

    async def get_news_items(self, limit: int = 8) -> list[dict[str, Any]]:
        results = await asyncio.gather(*[self._fetch_feed(source, url) for source, url in NEWS_FEEDS])
        flattened = [item for group in results for item in group]
        deduped: dict[str, dict[str, Any]] = {}
        for item in flattened:
            dedupe_key = item["link"] or item["title"]
            deduped[dedupe_key] = item
        items = list(deduped.values())
        items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
        return items[:limit]

    def _coin_line(self, coin: dict[str, Any]) -> str:
        flags = []
        if coin.get("fresh_score", 0) >= 4:
            flags.append("fresh")
        if coin.get("risk_score", 0) >= 6:
            flags.append("risk")
        flag_text = f" | {'/'.join(flags)}" if flags else ""
        return (
            f"*{coin['symbol']}* - {fmt_price(coin['price'])} | "
            f"24ч {fmt_pct(coin['change_24h'])} | 7д {fmt_pct(coin['change_7d'])} | "
            f"RSI `{coin['rsi']}` | Cap {fmt_b(coin['market_cap'])} | Vol/Cap {coin['vol_ratio'] * 100:.0f}%{flag_text}"
        )

    async def full_analysis(self, symbol: str) -> str:
        try:
            data = await self.get_coin_dashboard(symbol, chart_days=30)
        except ValueError:
            return f"❌ Монета *{symbol.upper()}* не найдена."

        macd_text = (
            f"`{data['macd']}` / `{data['macd_signal']}` (hist `{data['macd_hist']}`)"
            if data["macd"] is not None and data["macd_signal"] is not None
            else "N/A"
        )
        bollinger_text = (
            f"`{fmt_price(data['bb_low'])}` / `{fmt_price(data['bb_mid'])}` / `{fmt_price(data['bb_high'])}`"
            if data["bb_low"] is not None and data["bb_mid"] is not None and data["bb_high"] is not None
            else "N/A"
        )
        ema_text = (
            f"`{fmt_price(data['ema20'])}` / `{fmt_price(data['ema50'])}`"
            if data["ema20"] is not None and data["ema50"] is not None
            else "N/A"
        )
        trend_text = "📈 Бычий" if data["ema20"] and data["ema50"] and data["ema20"] > data["ema50"] else "📉 Медвежий"
        fear = data["fear_greed"]
        risk_note = " | ".join(data["risk_flags"][:3]) if data["risk_flags"] else "давление умеренное"

        lines = [
            f"📊 *{data['name']} ({data['symbol']})*",
            "",
            f"💰 *Цена:* {fmt_price(data['price'])}",
            f"📈 *Изменение:* {fmt_pct(data['change_24h'])} (24ч) | {fmt_pct(data['change_7d'])} (7д) | {fmt_pct(data['change_30d'])} (30д)",
            f"🏦 *Капитализация:* {fmt_b(data['market_cap'])}",
            f"📦 *Объем 24ч:* {fmt_b(data['volume'])} ({data['vol_ratio'] * 100:.1f}% от капы)",
            f"🏁 *FDV/MCap:* `{data['fdv_ratio']:.2f}x`",
            f"🏆 *ATH:* {fmt_price(data['ath'])} ({data['ath_change_pct']:.1f}% от ATH)",
            "",
            "━━━ ТЕХНИКА ━━━",
            f"*RSI (14):* `{data['rsi']}` - {rsi_signal(data['rsi'])}",
            f"*MACD:* {macd_text}",
            f"*Bollinger:* {bollinger_text}",
            f"*EMA 20/50:* {ema_text} - {trend_text if ema_text != 'N/A' else 'N/A'}",
            "",
            "━━━ СИГНАЛ ━━━",
            f"*{data['signal_label']}* - score `{data['signal_score']}/100`",
            f"_Fresh `{data['fresh_score']}` | Risk `{data['risk_score']}`_",
            "",
            "━━━ ПЛАН ━━━",
            f"🎯 *Зона входа:* {data['entry_zone']}",
            f"🛑 *Стоп:* {fmt_price(data['stop_loss'])}",
            f"✅ *TP1:* {fmt_price(data['tp1'])}",
            f"✅ *TP2:* {fmt_price(data['tp2'])}",
            f"✅ *TP3:* {fmt_price(data['tp3'])}",
            "",
            "━━━ ОНЧЕЙН И НАСТРОЕНИЕ ━━━",
            f"😐 *Fear & Greed:* `{fear['value']}` - {fear['label']}",
            f"⚠️ *Риск распределения:* {risk_note}",
        ]

        if data["defi"].get("tvl"):
            tvl_change = safe_float(data["defi"].get("tvl_change"))
            lines.append(f"🏊 *TVL:* {fmt_b(data['defi']['tvl'])} ({fmt_pct(tvl_change)})")
        if data["defi"].get("flows") is not None:
            lines.append(f"📥 *Потоки 7д:* {fmt_b(abs(safe_float(data['defi']['flows'])))}")
        if data["sentiment_up"]:
            lines.append(f"💬 *Позитивные голоса:* `{data['sentiment_up']:.0f}%`")
        if data["developer_score"]:
            lines.append(f"👨‍💻 *Dev Score:* `{data['developer_score']:.1f}/100`")
        if data["description"]:
            lines.append("")
            lines.append(f"_{data['description'][:180]}..._")

        return "\n".join(lines)

    async def market_scan(self) -> str:
        overview = await self.get_market_overview(limit=600)
        coins = overview["items"]
        buys = [coin for coin in coins if coin["signal_type"] == "buy" and coin["risk_score"] < 7]
        sells = [coin for coin in coins if coin["signal_type"] == "sell" or coin["risk_score"] >= 8]
        buys.sort(key=lambda item: (item["signal_score"], item["fresh_score"]), reverse=True)
        sells.sort(key=lambda item: (item["risk_score"], item["rsi"]), reverse=True)

        lines = [
            f"🔎 *Скан рынка* - покрытие `{len(coins)}` монет",
            f"😐 *Fear & Greed:* `{overview['fear_greed']['value']}` - {overview['fear_greed']['label']}",
            "",
            "🟢 *Лучшие long-идеи:*",
        ]
        if buys:
            lines.extend([f"• {self._coin_line(coin)}" for coin in buys[:6]])
        else:
            lines.append("• Сильных long-сетапов прямо сейчас немного.")
        lines += ["", "🔴 *Слабые / под давлением:*"]
        if sells:
            lines.extend([f"• {self._coin_line(coin)}" for coin in sells[:6]])
        else:
            lines.append("• Явных перекосов вниз сейчас мало.")
        lines.append("\n_/analyze BTC или /screener fresh для деталей_")
        return "\n".join(lines)

    async def find_overbought(self) -> str:
        coins = (await self.get_market_overview(limit=700))["items"]
        results = [coin for coin in coins if coin["rsi"] >= 68]
        results.sort(key=lambda item: (item["rsi"], item["risk_score"], item["vol_ratio"]), reverse=True)
        if not results:
            return "✅ Перекупленных монет сейчас почти нет."

        lines = [
            f"🔥 *Перекупленные монеты* ({len(results)} найдено)",
            "_Высокий RSI и слабое соотношение reward/risk_",
            "",
        ]
        for coin in results[:10]:
            risk = " | exit-risk" if coin["risk_score"] >= 6 else ""
            lines.append(
                f"• *{coin['symbol']}* - RSI `{coin['rsi']}` | 24ч {fmt_pct(coin['change_24h'])} | "
                f"Cap {fmt_b(coin['market_cap'])} | Vol/Cap {coin['vol_ratio'] * 100:.0f}%{risk}"
            )
        lines.append("\n_/analyze SYMBOL для уровней выхода_")
        return "\n".join(lines)

    async def find_new_potential(self) -> str:
        coins = (await self.get_market_overview(limit=700))["items"]
        results = [
            coin
            for coin in coins
            if coin["fresh_score"] >= 4
            and coin["market_cap"] >= 40_000_000
            and coin["risk_score"] < 8
        ]
        results.sort(key=lambda item: (item["opportunity_score"], item["vol_ratio"], item["change_7d"]), reverse=True)
        if not results:
            return "⚠️ По свежим монетам сейчас нет сильных сетапов без повышенного риска."

        lines = [
            "🚀 *Свежие монеты с потенциалом*",
            "_Смотрю на хайп, объем, капитализацию и то, насколько рынок их уже начал распределять._",
            "",
        ]
        for coin in results[:10]:
            days = coin["freshness_days"]
            fresh_text = f"{days}д от локального ATH" if days is not None else "fresh proxy n/a"
            lines.append(
                f"• *{coin['symbol']}* - {fmt_price(coin['price'])} | Cap {fmt_b(coin['market_cap'])} | "
                f"24ч {fmt_pct(coin['change_24h'])} | Vol/Cap {coin['vol_ratio'] * 100:.0f}% | "
                f"score `{coin['opportunity_score']}` | {fresh_text}"
            )
        lines.append("\n_/screener fresh cap_min=50000000 limit=12_")
        return "\n".join(lines)

    async def find_listing_risk(self) -> str:
        coins = (await self.get_market_overview(limit=700))["items"]
        candidates = [
            coin
            for coin in coins
            if coin["market_cap"] >= 50_000_000
            and coin["fresh_score"] >= 3
            and coin["risk_score"] >= 5
        ]
        candidates.sort(key=lambda item: (item["risk_score"], item["fdv_ratio"], -item["change_24h"]), reverse=True)
        if not candidates:
            return "✅ Свежих large-cap монет с явными признаками распределения сейчас не нашел."

        lines = [
            "🧨 *Свежие large-cap монеты с риском распределения*",
            "_Ищу новые истории, где капа уже большая, а рынок начинает продавать в ликвидность._",
            "",
        ]
        for coin in candidates[:10]:
            reasons = ", ".join(coin["risk_flags"][:3]) if coin["risk_flags"] else "повышенная волатильность"
            lines.append(
                f"• *{coin['symbol']}* - Cap {fmt_b(coin['market_cap'])} | 24ч {fmt_pct(coin['change_24h'])} | "
                f"FDV/MCap `{coin['fdv_ratio']:.2f}x` | Vol/Cap {coin['vol_ratio'] * 100:.0f}%\n"
                f"  _Почему риск: {reasons}_"
            )
        lines.append("\n_/analyze SYMBOL чтобы проверить уровни и спрос_")
        return "\n".join(lines)

    async def find_dump_risk(self) -> str:
        coins = (await self.get_market_overview(limit=700))["items"]
        risks = [coin for coin in coins if coin["risk_score"] >= 6]
        risks.sort(key=lambda item: (item["risk_score"], item["vol_ratio"], -item["change_24h"]), reverse=True)
        if not risks:
            return "✅ Монет с ярко выраженным дамп-риском сейчас немного."

        lines = [
            f"⚠️ *Риск дампа* - `{len(risks)}` монет",
            "_Высокий объем, перегретость, FDV и признаки распределения._",
            "",
        ]
        for coin in risks[:10]:
            reasons = " | ".join(coin["risk_flags"][:3]) if coin["risk_flags"] else "сигнал слабее"
            lines.append(
                f"• *{coin['symbol']}* - {fmt_price(coin['price'])} | Cap {fmt_b(coin['market_cap'])} | "
                f"risk `{coin['risk_score']}` | 24ч {fmt_pct(coin['change_24h'])}\n"
                f"  _{reasons}_"
            )
        lines.append("\n_/screener risk limit=12_")
        return "\n".join(lines)

    async def top_signals(self) -> str:
        coins = (await self.get_market_overview(limit=700))["items"]
        buys = [coin for coin in coins if coin["signal_type"] == "buy" and coin["risk_score"] < 8]
        sells = [coin for coin in coins if coin["signal_type"] == "sell" or coin["risk_score"] >= 8]
        buys.sort(key=lambda item: (item["signal_score"], item["opportunity_score"]), reverse=True)
        sells.sort(key=lambda item: (item["risk_score"], item["signal_score"]), reverse=True)

        lines = ["📈 *Топ сигналы рынка*", ""]
        lines.append("🟢 *Buy-side:*")
        if buys:
            lines.extend([f"• {self._coin_line(coin)}" for coin in buys[:5]])
        else:
            lines.append("• Сильных покупок не видно.")
        lines += ["", "🔴 *Sell / risk-side:*"]
        if sells:
            lines.extend([f"• {self._coin_line(coin)}" for coin in sells[:5]])
        else:
            lines.append("• Сильных short/risk сигналов не видно.")
        return "\n".join(lines)

    async def market_screener(self, filters: Optional[dict[str, str]] = None) -> str:
        filters = {key.lower(): value for key, value in (filters or {}).items()}
        preset = filters.get("preset", "smart").lower()
        universe = int(safe_float(filters.get("universe", 700), 700))
        limit = max(1, min(20, int(safe_float(filters.get("limit", 8), 8))))
        coins = (await self.get_market_overview(limit=universe))["items"]

        if preset == "fresh":
            filters.setdefault("cap_min", "50000000")
            filters.setdefault("fresh_min", "3")
            filters.setdefault("risk_max", "8")
            filters.setdefault("sort", "opportunity")
        elif preset == "risk":
            filters.setdefault("risk_min", "6")
            filters.setdefault("sort", "risk")
        elif preset == "momentum":
            filters.setdefault("change_24h_min", "3")
            filters.setdefault("change_7d_min", "8")
            filters.setdefault("vol_ratio_min", "0.08")
            filters.setdefault("sort", "signal")
        elif preset == "oversold":
            filters.setdefault("rsi_max", "35")
            filters.setdefault("sort", "signal")

        def in_range(value: float, min_key: str, max_key: str) -> bool:
            min_value = filters.get(min_key)
            max_value = filters.get(max_key)
            if min_value is not None and value < safe_float(min_value):
                return False
            if max_value is not None and value > safe_float(max_value):
                return False
            return True

        results: list[dict[str, Any]] = []
        for coin in coins:
            if not in_range(coin["market_cap"], "cap_min", "cap_max"):
                continue
            if not in_range(coin["vol_ratio"], "vol_ratio_min", "vol_ratio_max"):
                continue
            if not in_range(coin["rsi"], "rsi_min", "rsi_max"):
                continue
            if not in_range(coin["change_24h"], "change_24h_min", "change_24h_max"):
                continue
            if not in_range(coin["change_7d"], "change_7d_min", "change_7d_max"):
                continue
            if not in_range(coin["fdv_ratio"], "fdv_ratio_min", "fdv_ratio_max"):
                continue
            if not in_range(coin["risk_score"], "risk_min", "risk_max"):
                continue
            if not in_range(coin["fresh_score"], "fresh_min", "fresh_max"):
                continue
            rank_max = filters.get("rank_max")
            if rank_max is not None and coin["rank"] and coin["rank"] > int(safe_float(rank_max)):
                continue
            signal = filters.get("signal")
            if signal and coin["signal_type"] != signal:
                continue
            results.append(coin)

        sort_key = filters.get("sort", "signal").lower()
        if sort_key == "risk":
            results.sort(key=lambda item: (item["risk_score"], item["vol_ratio"]), reverse=True)
        elif sort_key == "volume":
            results.sort(key=lambda item: (item["vol_ratio"], item["market_cap"]), reverse=True)
        elif sort_key == "fresh":
            results.sort(key=lambda item: (item["fresh_score"], item["opportunity_score"]), reverse=True)
        elif sort_key == "opportunity":
            results.sort(key=lambda item: (item["opportunity_score"], item["signal_score"]), reverse=True)
        elif sort_key == "cap":
            results.sort(key=lambda item: item["market_cap"], reverse=True)
        else:
            results.sort(key=lambda item: (item["signal_score"], item["fresh_score"]), reverse=True)

        if not results:
            return "🔎 По этим фильтрам ничего не нашлось. Ослабь условия и попробуй еще раз."

        lines = [
            f"🧰 *Скринер* - preset `{preset}` | найдено `{len(results)}`",
            f"_Universe {universe} монет | sort `{sort_key}`_",
            "",
        ]
        for coin in results[:limit]:
            lines.append(f"• {self._coin_line(coin)}")
        lines.append("")
        lines.append("Пример: `/screener fresh cap_min=50000000 risk_max=7 limit=10`")
        return "\n".join(lines)

    async def latest_news(self, limit: int = 6) -> str:
        items = await self.get_news_items(limit=limit)
        if not items:
            return "📰 Не получилось подтянуть новости. Проверь сеть на Railway и попробуй позже."

        lines = ["📰 *Крипто-лента*", ""]
        for item in items:
            source = item["source"]
            title = item["title"]
            link = item["link"]
            lines.append(f"• *{source}* - [{title}]({link})")
        lines.append("\n_Лента собрана из открытых RSS-источников._")
        return "\n".join(lines)
