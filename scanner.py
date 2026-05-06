from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import aiohttp

from contract_checker import ContractSecurityChecker

logger = logging.getLogger(__name__)
SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
TRENDING_URL = "https://api.dexscreener.com/latest/dex/tokens/trending"
SEEN_TOKENS_FILE = Path(__file__).resolve().parent / "seen_tokens.json"
SUPPORTED_CHAINS = {"ethereum", "bsc", "solana", "base", "arbitrum", "polygon"}
SEARCH_QUERIES = ["new", "solana", "ethereum", "bsc", "meme", "gem", "trending", "frog", "doge", "ai"]


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _age_h(ms: Any) -> float:
    ts = int(_f(ms, 0))
    if ts <= 0:
        return 999.0
    return max((int(time.time() * 1000) - ts) / 3_600_000, 0)


def token_uid(chain: str, contract: str) -> str:
    return f"{chain.lower()}:{contract.lower()}"


def ensure_seen_tokens_file() -> None:
    if not SEEN_TOKENS_FILE.exists():
        SEEN_TOKENS_FILE.write_text("[]", encoding="utf-8")


def load_seen_tokens() -> set[str]:
    ensure_seen_tokens_file()
    try:
        return {str(x) for x in json.loads(SEEN_TOKENS_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return set()


def save_seen_tokens(seen_tokens: set[str]) -> None:
    SEEN_TOKENS_FILE.write_text(json.dumps(sorted(seen_tokens), ensure_ascii=False, indent=2), encoding="utf-8")


def calculate_x1000_score(token: dict[str, Any], security: dict[str, Any]) -> int:
    score = 20
    age = _f(token.get("age_hours"), 999)
    liq = _f(token.get("liquidity"))
    v1 = _f(token.get("volume_1h"))
    if age <= 0.25:
        score += 25
    elif age <= 1:
        score += 18
    elif age <= 6:
        score += 10
    if liq >= 100_000:
        score += 18
    elif liq >= 20_000:
        score += 10
    if liq > 0 and (v1 / liq) > 0.35:
        score += 16
    st = security.get("sell_tax")
    if st is not None and float(st) < 10:
        score += 10
    if not security.get("is_honeypot"):
        score += 12
    return max(0, min(100, score))


class MultiChainScanner:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.security = ContractSecurityChecker()

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        assert self.session is not None
        for i in range(3):
            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(2 + i)
                        continue
                    if resp.status >= 400:
                        return {}
                    return await resp.json(content_type=None)
            except Exception:
                if i == 2:
                    return {}
                await asyncio.sleep(1 + i)
        return {}

    def _norm(self, pair: dict[str, Any]) -> dict[str, Any] | None:
        chain = str(pair.get("chainId", "")).lower()
        if chain not in SUPPORTED_CHAINS:
            return None
        base = pair.get("baseToken") or {}
        contract = str(base.get("address", "")).strip()
        if not contract:
            return None
        return {
            "name": str(base.get("name", "Unknown")),
            "symbol": str(base.get("symbol", "UNK")).upper(),
            "contract": contract,
            "chain": chain,
            "age_hours": _age_h(pair.get("pairCreatedAt")),
            "price": _f(pair.get("priceUsd")),
            "liquidity": _f((pair.get("liquidity") or {}).get("usd")),
            "market_cap": _f(pair.get("marketCap") or pair.get("fdv")),
            "volume_24h": _f((pair.get("volume") or {}).get("h24")),
            "volume_1h": _f((pair.get("volume") or {}).get("h1")),
            "txns_1h_buys": int(_f(((pair.get("txns") or {}).get("h1") or {}).get("buys"))),
            "txns_1h_sells": int(_f(((pair.get("txns") or {}).get("h1") or {}).get("sells"))),
            "dex_url": str(pair.get("url", "")),
        }

    async def scan_all_tokens(self, max_age_hours: float = 24.0) -> list[dict[str, Any]]:
        await self.start()
        rows: list[dict[str, Any]] = []
        qs = SEARCH_QUERIES[:]
        random.shuffle(qs)
        for q in qs:
            data = await self._get_json(SEARCH_URL, {"q": q})
            for p in data.get("pairs") or []:
                n = self._norm(p)
                if n:
                    rows.append(n)
            await asyncio.sleep(2)
        tr = await self._get_json(TRENDING_URL)
        for p in tr.get("pairs") or []:
            n = self._norm(p)
            if n:
                rows.append(n)
        dedup: dict[str, dict[str, Any]] = {}
        for r in rows:
            uid = token_uid(r["chain"], r["contract"])
            prev = dedup.get(uid)
            if not prev or r["volume_1h"] > prev["volume_1h"]:
                dedup[uid] = r
        out = [r for r in dedup.values() if r["age_hours"] <= max_age_hours and r["liquidity"] >= 5_000]
        out.sort(key=lambda x: (-x["volume_1h"], x["age_hours"]))
        return out[:60]

    async def scan_new_tokens(self, max_age_hours: float = 24.0) -> list[dict[str, Any]]:
        return await self.scan_all_tokens(max_age_hours=max_age_hours)
