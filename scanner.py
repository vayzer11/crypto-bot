"""
scanner.py
Реалтайм-сканер новых токенов для ETH/Solana через бесплатные API.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
GECKO_TERMINAL = "https://api.geckoterminal.com/api/v2"

BASE_DIR = Path(__file__).resolve().parent
SEEN_TOKENS_FILE = BASE_DIR / "seen_tokens.json"

DEFAULT_FILTERS = {
    "min_liquidity": 10_000,
    "max_liquidity": 2_000_000,
    "min_volume_24h": 5_000,
    "max_age_hours": 24,
    "min_safety_score": 40,
    "min_holders": 20,
    "max_buy_tax": 10,
    "max_sell_tax": 10,
    "chains": ["ethereum", "solana"],
    "exclude_honeypots": True,
}

CHAIN_ALIASES = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
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


def ensure_seen_tokens_file() -> None:
    if not SEEN_TOKENS_FILE.exists():
        SEEN_TOKENS_FILE.write_text("[]", encoding="utf-8")


def load_seen_tokens() -> set[str]:
    ensure_seen_tokens_file()
    try:
        raw = json.loads(SEEN_TOKENS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {str(x).strip().lower() for x in raw if str(x).strip()}
    except Exception as exc:
        logger.error("Не удалось загрузить seen_tokens.json: %s", exc)
    return set()


def save_seen_tokens(seen_tokens: set[str]) -> None:
    try:
        SEEN_TOKENS_FILE.write_text(
            json.dumps(sorted(seen_tokens), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error("Не удалось сохранить seen_tokens.json: %s", exc)


def normalize_chain(chain: str) -> str:
    return CHAIN_ALIASES.get((chain or "").lower(), (chain or "").lower())


def _token_uid(chain: str, contract: str) -> str:
    return f"{normalize_chain(chain)}:{(contract or '').lower()}"


def passes_filters(token: dict[str, Any], filters: dict[str, Any] | None = None) -> bool:
    active_filters = {**DEFAULT_FILTERS, **(filters or {})}
    chain = normalize_chain(str(token.get("chain", "")))
    liquidity = _safe_float(token.get("liquidity"))
    volume_24h = _safe_float(token.get("volume_24h"))
    age_hours = _safe_float(token.get("age_hours"), default=999.0)

    if chain not in active_filters["chains"]:
        return False
    if liquidity < active_filters["min_liquidity"]:
        return False
    if liquidity > active_filters["max_liquidity"]:
        return False
    if volume_24h < active_filters["min_volume_24h"]:
        return False
    if age_hours > active_filters["max_age_hours"]:
        return False
    if not token.get("contract"):
        return False
    if token.get("price") is None:
        return False
    return True


class TokenScanner:
    def __init__(self, filters: dict[str, Any] | None = None) -> None:
        self.filters = {**DEFAULT_FILTERS, **(filters or {})}

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        logger.warning("HTTP %s для %s", response.status, url)
                        return None
                    return await response.json()
        except Exception as exc:
            logger.error("Ошибка запроса %s: %s", url, exc)
            return None

    def _build_token_from_dex(self, pair: dict[str, Any]) -> dict[str, Any] | None:
        chain = normalize_chain(str(pair.get("chainId", "")))
        if chain not in {"ethereum", "solana"}:
            return None

        base_token = pair.get("baseToken") or {}
        contract = str(base_token.get("address") or "").strip()
        if not contract:
            return None

        created_at_ms = _safe_int(pair.get("pairCreatedAt"))
        now_ms = int(time.time() * 1000)
        if created_at_ms > 0 and created_at_ms <= now_ms:
            age_hours = (now_ms - created_at_ms) / 3_600_000
        else:
            age_hours = 999.0

        price = _safe_float(pair.get("priceUsd"))
        change_1h = _safe_float((pair.get("priceChange") or {}).get("h1"))
        change_24h = _safe_float((pair.get("priceChange") or {}).get("h24"))
        price_high_24h = price * (1 + max(change_24h, 0) / 100)
        price_low_24h = price * (1 - max(-change_24h, 0) / 100)
        if price_high_24h <= 0:
            price_high_24h = price
        if price_low_24h <= 0:
            price_low_24h = price * 0.95

        liquidity = _safe_float((pair.get("liquidity") or {}).get("usd"))
        volume_24h = _safe_float((pair.get("volume") or {}).get("h24"))
        market_cap = _safe_float(pair.get("marketCap") or pair.get("fdv"))
        txns = pair.get("txns") or {}
        txns_24h = _safe_int((txns.get("h24") or {}).get("buys")) + _safe_int((txns.get("h24") or {}).get("sells"))

        return {
            "name": str(base_token.get("name") or "Unknown"),
            "symbol": str(base_token.get("symbol") or "UNK"),
            "chain": chain,
            "contract": contract,
            "pair_address": str(pair.get("pairAddress") or ""),
            "age_hours": age_hours,
            "price": price,
            "market_cap": market_cap,
            "liquidity": liquidity,
            "volume_24h": volume_24h,
            "vol_liq_ratio": (volume_24h / liquidity) if liquidity > 0 else 0.0,
            "price_change_1h": change_1h,
            "price_change_24h": change_24h,
            "txns_24h": txns_24h,
            "buys_24h": _safe_int((txns.get("h24") or {}).get("buys")),
            "sells_24h": _safe_int((txns.get("h24") or {}).get("sells")),
            "price_high_24h": max(price_high_24h, price),
            "price_low_24h": min(price_low_24h, price),
            "source": "dexscreener",
        }

    async def get_new_pairs(self) -> list[dict[str, Any]]:
        urls = [
            f"{DEXSCREENER_API}/tokens/recently-added",
            f"{DEXSCREENER_API}/search",
        ]
        params = [None, {"q": "new"}]
        out: list[dict[str, Any]] = []

        for url, query in zip(urls, params, strict=False):
            data = await self._get_json(url, params=query)
            pairs = (data or {}).get("pairs", []) if isinstance(data, dict) else []
            for pair in pairs:
                token = self._build_token_from_dex(pair)
                if token:
                    out.append(token)

        return out

    async def _gecko_fetch(self, endpoint: str) -> list[dict[str, Any]]:
        url = f"{GECKO_TERMINAL}{endpoint}"
        data = await self._get_json(url)
        rows = (data or {}).get("data", []) if isinstance(data, dict) else []
        tokens: list[dict[str, Any]] = []

        for row in rows:
            attrs = row.get("attributes") or {}
            chain = normalize_chain(str(attrs.get("network") or attrs.get("network_slug") or ""))
            if chain not in {"ethereum", "solana"}:
                continue

            pair_id = str(row.get("id") or "")
            # Пример id: "eth_0xabc..." или "solana_xxx"
            contract = pair_id.split("_", 1)[1] if "_" in pair_id else pair_id
            if not contract:
                continue

            created_at = attrs.get("pool_created_at")
            age_hours = 999.0
            if created_at:
                try:
                    created_ts = int(
                        time.mktime(time.strptime(str(created_at).split(".")[0], "%Y-%m-%dT%H:%M:%S"))
                    )
                    age_hours = max(0.0, (time.time() - created_ts) / 3600)
                except Exception:
                    pass

            price = _safe_float(attrs.get("base_token_price_usd"))
            change_24h = _safe_float((attrs.get("price_change_percentage") or {}).get("h24"))
            high = price * (1 + max(change_24h, 0) / 100)
            low = price * (1 - max(-change_24h, 0) / 100)
            liq = _safe_float(attrs.get("reserve_in_usd"))
            vol = _safe_float((attrs.get("volume_usd") or {}).get("h24"))
            txns = attrs.get("transactions") or {}
            buys = _safe_int((txns.get("h24") or {}).get("buys"))
            sells = _safe_int((txns.get("h24") or {}).get("sells"))

            tokens.append(
                {
                    "name": str(attrs.get("name") or attrs.get("base_token_name") or "Unknown"),
                    "symbol": str(attrs.get("base_token_symbol") or "UNK"),
                    "chain": chain,
                    "contract": contract,
                    "pair_address": pair_id,
                    "age_hours": age_hours,
                    "price": price,
                    "market_cap": _safe_float(attrs.get("fdv_usd") or attrs.get("market_cap_usd")),
                    "liquidity": liq,
                    "volume_24h": vol,
                    "vol_liq_ratio": (vol / liq) if liq > 0 else 0.0,
                    "price_change_1h": _safe_float((attrs.get("price_change_percentage") or {}).get("h1")),
                    "price_change_24h": change_24h,
                    "txns_24h": buys + sells,
                    "buys_24h": buys,
                    "sells_24h": sells,
                    "price_high_24h": max(high, price),
                    "price_low_24h": min(low, price if price > 0 else low),
                    "source": "geckoterminal",
                }
            )

        return tokens

    async def get_trending_pools(self) -> list[dict[str, Any]]:
        eth = await self._gecko_fetch("/networks/eth/trending_pools")
        sol = await self._gecko_fetch("/networks/solana/trending_pools")
        return eth + sol

    async def get_new_pools_eth(self) -> list[dict[str, Any]]:
        return await self._gecko_fetch("/networks/eth/new_pools")

    async def get_new_pools_solana(self) -> list[dict[str, Any]]:
        return await self._gecko_fetch("/networks/solana/new_pools")

    async def get_candidates(self, seen_tokens: set[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        dex_pairs = await self.get_new_pairs()
        gecko_trending = await self.get_trending_pools()
        gecko_new_eth = await self.get_new_pools_eth()
        gecko_new_sol = await self.get_new_pools_solana()
        all_tokens = dex_pairs + gecko_trending + gecko_new_eth + gecko_new_sol

        dedup: dict[str, dict[str, Any]] = {}
        for token in all_tokens:
            uid = _token_uid(token.get("chain", ""), token.get("contract", ""))
            if not uid or uid in seen_tokens:
                continue
            if not passes_filters(token, self.filters):
                continue
            existing = dedup.get(uid)
            if existing is None or token.get("volume_24h", 0) > existing.get("volume_24h", 0):
                dedup[uid] = token

        candidates.extend(dedup.values())
        candidates.sort(key=lambda item: item.get("volume_24h", 0), reverse=True)
        return candidates
