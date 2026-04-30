"""
scanner.py
Поиск свежих memecoin-кандидатов (x1000 strategy) через бесплатные API.
"""

from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEX_TOKEN_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_RECENT_PAIRS = "https://api.dexscreener.com/latest/dex/tokens/recently-added"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"

BASE_DIR = Path(__file__).resolve().parent
SEEN_TOKENS_FILE = BASE_DIR / "seen_tokens.json"

MEME_KEYWORDS = {
    "pepe", "doge", "shib", "cat", "dog", "frog", "moon", "elon", "trump", "maga", "chad",
    "based", "wojak", "ape", "baby", "inu", "floki", "bonk", "wif", "popcat", "pnut",
    "goat", "turbo", "mog", "brett", "neiro", "act", "banana", "pizza", "burger",
}

DEFAULT_FILTERS = {
    "max_age_hours": 12.0,
    "min_liquidity": 5_000.0,
    "max_liquidity": 500_000.0,
    "min_vol_liq_ratio": 0.5,
    "chains": {"ethereum", "solana"},
    "max_buy_tax": 5.0,
    "max_sell_tax": 5.0,
    "min_score": 50,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _chain_normalize(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"eth", "ethereum"}:
        return "ethereum"
    if text in {"sol", "solana"}:
        return "solana"
    return text


def _parse_iso_to_hours(iso_text: str | None) -> float:
    if not iso_text:
        return 999.0
    try:
        stamp = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds() / 3600)
    except Exception:
        return 999.0


def _is_memecoin(name: str, symbol: str, market_cap: float) -> bool:
    hay = f"{name} {symbol}".lower()
    if market_cap > 0 and market_cap < 1_000_000:
        return True
    return any(word in hay for word in MEME_KEYWORDS)


def ensure_seen_tokens_file() -> None:
    if not SEEN_TOKENS_FILE.exists():
        SEEN_TOKENS_FILE.write_text("[]", encoding="utf-8")


def load_seen_tokens() -> set[str]:
    ensure_seen_tokens_file()
    try:
        data = json.loads(SEEN_TOKENS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(item).strip().lower() for item in data if str(item).strip()}
    except Exception as exc:
        logger.error("Ошибка загрузки seen_tokens.json: %s", exc)
    return set()


def save_seen_tokens(seen_tokens: set[str]) -> None:
    try:
        SEEN_TOKENS_FILE.write_text(
            json.dumps(sorted(seen_tokens), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error("Ошибка сохранения seen_tokens.json: %s", exc)


def token_uid(chain: str, contract: str) -> str:
    return f"{_chain_normalize(chain)}:{(contract or '').strip().lower()}"


def calculate_x1000_score(token: dict[str, Any], security: dict[str, Any]) -> int:
    score = 0
    mcap = _safe_float(token.get("market_cap"))
    age_h = _safe_float(token.get("age_hours"), 999.0)
    ratio = _safe_float(token.get("vol_liq_ratio"))
    buys = _safe_int(token.get("buys_1h"))
    sells = _safe_int(token.get("sells_1h"))
    liq_change = _safe_float(token.get("liquidity_change_1h"))
    sell_tax = _safe_float(security.get("sell_tax"))
    buy_tax = _safe_float(security.get("buy_tax"))
    is_honeypot = bool(security.get("is_honeypot"))

    if mcap < 100_000:
        score += 40
    elif mcap < 500_000:
        score += 30
    elif mcap < 1_000_000:
        score += 20

    if age_h < 1:
        score += 25
    elif age_h < 6:
        score += 20
    elif age_h < 12:
        score += 15

    if ratio > 3:
        score += 30
    elif ratio > 1:
        score += 20
    elif ratio > 0.5:
        score += 10

    if buys > sells * 2:
        score += 20
    elif buys > sells:
        score += 10

    if liq_change > 0:
        score += 15

    if sell_tax > 3:
        score -= 30
    if buy_tax > 3:
        score -= 20
    if is_honeypot:
        score -= 40

    return max(0, min(100, score))


class MemecoinScanner:
    def __init__(self, filters: dict[str, Any] | None = None) -> None:
        self.filters = {**DEFAULT_FILTERS, **(filters or {})}

    async def _get_json(self, url: str) -> Any:
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning("HTTP %s (%s)", response.status, url)
                        return None
                    return await response.json()
        except Exception as exc:
            logger.error("Ошибка запроса %s: %s", url, exc)
            return None

    def _normalize_dex_pair(self, pair: dict[str, Any]) -> dict[str, Any] | None:
        chain = _chain_normalize(str(pair.get("chainId", "")))
        if chain not in self.filters["chains"]:
            return None

        base = pair.get("baseToken") or {}
        contract = str(base.get("address") or "").strip()
        if not contract:
            return None

        created_ms = _safe_int(pair.get("pairCreatedAt"))
        age_hours = 999.0
        if created_ms > 0:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            age_hours = max(0.0, (now_ms - created_ms) / 3_600_000)

        liq = _safe_float((pair.get("liquidity") or {}).get("usd"))
        vol_24 = _safe_float((pair.get("volume") or {}).get("h24"))
        vol_1 = _safe_float((pair.get("volume") or {}).get("h1"))
        price = _safe_float(pair.get("priceUsd"))
        price_change_1h = _safe_float((pair.get("priceChange") or {}).get("h1"))
        high = price * (1 + max(price_change_1h, 0) / 100)
        low = price * (1 - max(-price_change_1h, 0) / 100)
        txns_h1 = (pair.get("txns") or {}).get("h1") or {}

        return {
            "source": "dexscreener",
            "name": str(base.get("name") or "Unknown"),
            "symbol": str(base.get("symbol") or "UNK").upper(),
            "chain": chain,
            "contract": contract,
            "pair_address": str(pair.get("pairAddress") or ""),
            "dex_url": f"https://dexscreener.com/{chain}/{contract}",
            "age_hours": age_hours,
            "price": price,
            "market_cap": _safe_float(pair.get("marketCap") or pair.get("fdv")),
            "liquidity": liq,
            "volume_1h": vol_1,
            "volume_24h": vol_24,
            "vol_liq_ratio": (vol_1 / liq) if liq > 0 else 0.0,
            "buys_1h": _safe_int(txns_h1.get("buys")),
            "sells_1h": _safe_int(txns_h1.get("sells")),
            "liquidity_change_1h": _safe_float((pair.get("liquidity") or {}).get("usd_change_h1")),
            "price_high_1h": max(high, price),
            "price_low_1h": min(low, price if price > 0 else low),
        }

    def _normalize_gecko_pool(self, row: dict[str, Any]) -> dict[str, Any] | None:
        attrs = row.get("attributes") or {}
        chain = _chain_normalize(str(attrs.get("network") or attrs.get("network_slug") or ""))
        if chain not in self.filters["chains"]:
            return None

        pool_addr = str(attrs.get("address") or "")
        base_token = attrs.get("base_token") or {}
        contract = str(base_token.get("address") or "")
        if not contract:
            pair_id = str(row.get("id") or "")
            if "_" in pair_id:
                contract = pair_id.split("_", 1)[1]
        if not contract:
            return None

        liq = _safe_float(attrs.get("reserve_in_usd"))
        vol_1 = _safe_float((attrs.get("volume_usd") or {}).get("h1"))
        vol_24 = _safe_float((attrs.get("volume_usd") or {}).get("h24"))
        tx_h1 = (attrs.get("transactions") or {}).get("h1") or {}
        price = _safe_float(attrs.get("base_token_price_usd"))
        price_change_1h = _safe_float((attrs.get("price_change_percentage") or {}).get("h1"))
        high = price * (1 + max(price_change_1h, 0) / 100)
        low = price * (1 - max(-price_change_1h, 0) / 100)

        return {
            "source": "geckoterminal",
            "name": str(attrs.get("base_token_name") or attrs.get("name") or "Unknown"),
            "symbol": str(attrs.get("base_token_symbol") or "UNK").upper(),
            "chain": chain,
            "contract": contract,
            "pair_address": pool_addr,
            "dex_url": f"https://dexscreener.com/{chain}/{contract}",
            "age_hours": _parse_iso_to_hours(attrs.get("pool_created_at")),
            "price": price,
            "market_cap": _safe_float(attrs.get("market_cap_usd") or attrs.get("fdv_usd")),
            "liquidity": liq,
            "volume_1h": vol_1,
            "volume_24h": vol_24,
            "vol_liq_ratio": (vol_1 / liq) if liq > 0 else 0.0,
            "buys_1h": _safe_int(tx_h1.get("buys")),
            "sells_1h": _safe_int(tx_h1.get("sells")),
            "liquidity_change_1h": _safe_float(attrs.get("reserve_in_usd_change_1h")),
            "price_high_1h": max(high, price),
            "price_low_1h": min(low, price if price > 0 else low),
        }

    async def fetch_sources(self) -> list[dict[str, Any]]:
        urls = [
            DEX_TOKEN_PROFILES,
            DEX_RECENT_PAIRS,
            f"{GECKO_BASE}/networks/eth/new_pools",
            f"{GECKO_BASE}/networks/solana/new_pools",
        ]
        payloads = await asyncio.gather(*(self._get_json(url) for url in urls))
        out: list[dict[str, Any]] = []

        # Dex token profiles
        profiles = payloads[0] if isinstance(payloads[0], list) else []
        for item in profiles:
            chain = _chain_normalize(str(item.get("chainId") or ""))
            contract = str(item.get("tokenAddress") or item.get("address") or "").strip()
            if chain not in self.filters["chains"] or not contract:
                continue
            out.append(
                {
                    "source": "dex_token_profiles",
                    "name": str(item.get("tokenName") or item.get("name") or "Unknown"),
                    "symbol": str(item.get("tokenSymbol") or item.get("symbol") or "UNK").upper(),
                    "chain": chain,
                    "contract": contract,
                    "pair_address": "",
                    "dex_url": f"https://dexscreener.com/{chain}/{contract}",
                    "age_hours": 999.0,
                    "price": 0.0,
                    "market_cap": 0.0,
                    "liquidity": 0.0,
                    "volume_1h": 0.0,
                    "volume_24h": 0.0,
                    "vol_liq_ratio": 0.0,
                    "buys_1h": 0,
                    "sells_1h": 0,
                    "liquidity_change_1h": 0.0,
                    "price_high_1h": 0.0,
                    "price_low_1h": 0.0,
                }
            )

        # Dex recent pairs
        recent_pairs = (payloads[1] or {}).get("pairs", []) if isinstance(payloads[1], dict) else []
        for pair in recent_pairs:
            normalized = self._normalize_dex_pair(pair)
            if normalized:
                out.append(normalized)

        # Gecko pools eth/sol
        for idx in (2, 3):
            rows = (payloads[idx] or {}).get("data", []) if isinstance(payloads[idx], dict) else []
            for row in rows:
                normalized = self._normalize_gecko_pool(row)
                if normalized:
                    out.append(normalized)

        return out

    def _merge_best(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for row in rows:
            uid = token_uid(row.get("chain", ""), row.get("contract", ""))
            if not uid:
                continue
            prev = merged.get(uid)
            if not prev:
                merged[uid] = row
                continue
            # Берем запись с более полной ликвидностью/объёмом.
            prev_quality = _safe_float(prev.get("liquidity")) + _safe_float(prev.get("volume_1h"))
            row_quality = _safe_float(row.get("liquidity")) + _safe_float(row.get("volume_1h"))
            if row_quality > prev_quality:
                merged[uid] = row
        return list(merged.values())

    def prefilter_memecoins(self, rows: list[dict[str, Any]], seen_tokens: set[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for token in self._merge_best(rows):
            uid = token_uid(token.get("chain", ""), token.get("contract", ""))
            if uid in seen_tokens:
                continue

            age = _safe_float(token.get("age_hours"), 999.0)
            liq = _safe_float(token.get("liquidity"))
            ratio = _safe_float(token.get("vol_liq_ratio"))
            buys = _safe_int(token.get("buys_1h"))
            sells = _safe_int(token.get("sells_1h"))
            cap = _safe_float(token.get("market_cap"))

            if token.get("chain") not in self.filters["chains"]:
                continue
            if age > self.filters["max_age_hours"]:
                continue
            if liq < self.filters["min_liquidity"] or liq > self.filters["max_liquidity"]:
                continue
            if ratio <= self.filters["min_vol_liq_ratio"]:
                continue
            if buys <= sells:
                continue
            if not _is_memecoin(str(token.get("name", "")), str(token.get("symbol", "")), cap):
                continue
            out.append(token)
        return out
