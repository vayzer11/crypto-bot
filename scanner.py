from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
SEEN_TOKENS_FILE = Path(__file__).resolve().parent / "seen_tokens.json"
SUPPORTED_CHAINS = {"ethereum", "bsc", "solana", "base", "arbitrum", "polygon"}
MEME_KEYWORDS = ["pepe", "doge", "shib", "cat", "moon", "elon", "baby", "inu", "wojak", "bonk"]
DEFI_KEYWORDS = ["swap", "defi", "yield", "farm", "vault", "lending", "dex", "stake", "dao"]
SCAN_QUERIES = ["ethereum", "bsc", "solana", "base", "arbitrum", "polygon", *MEME_KEYWORDS, "new"]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def token_uid(chain: str, contract: str) -> str:
    return f"{str(chain).lower()}:{str(contract).lower()}"


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
    score = 35
    age = _f(token.get("age_hours"), 999)
    liq = _f(token.get("liquidity"))
    vol1 = _f(token.get("volume_1h"))
    if age <= 0.2:
        score += 25
    elif age <= 1:
        score += 18
    elif age <= 6:
        score += 10
    if liq >= 100_000:
        score += 15
    elif liq >= 20_000:
        score += 8
    if liq > 0 and vol1 / liq > 0.3:
        score += 12
    if security.get("buy_tax") == 0 and security.get("sell_tax") == 0:
        score += 8
    if not security.get("is_honeypot"):
        score += 8
    return max(0, min(100, score))


class MultiChainScanner:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        assert self.session is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.6 + attempt)
        raise RuntimeError(f"request failed: {last_error}")

    def _normalize_pair(self, pair: dict[str, Any]) -> dict[str, Any] | None:
        chain = str(pair.get("chainId", "")).lower()
        if chain not in SUPPORTED_CHAINS:
            return None
        base = pair.get("baseToken") or {}
        contract = str(base.get("address", "")).strip()
        if not contract:
            return None
        created_ms = int(_f(pair.get("pairCreatedAt"), 0))
        age_hours = max((int(time.time() * 1000) - created_ms) / 3_600_000, 0.0) if created_ms else 999.0
        name = str(base.get("name", "Unknown"))
        symbol = str(base.get("symbol", "UNK")).upper()
        blob = f"{name} {symbol}".lower()
        category = "meme" if any(k in blob for k in MEME_KEYWORDS) else "defi" if any(k in blob for k in DEFI_KEYWORDS) else "other"
        return {
            "name": name,
            "symbol": symbol,
            "contract": contract,
            "chain": chain,
            "age_hours": age_hours,
            "liquidity": _f((pair.get("liquidity") or {}).get("usd")),
            "market_cap": _f(pair.get("marketCap") or pair.get("fdv")),
            "volume_24h": _f((pair.get("volume") or {}).get("h24")),
            "volume_1h": _f((pair.get("volume") or {}).get("h1")),
            "price": _f(pair.get("priceUsd")),
            "buy_tax": None,
            "sell_tax": None,
            "is_honeypot": False,
            "is_mintable": False,
            "is_blacklisted": False,
            "is_renounced": None,
            "lp_locked": None,
            "risk_score": 0,
            "dex_url": str(pair.get("url", "")),
            "category": category,
        }

    def _passes(self, row: dict[str, Any], max_age_hours: float) -> bool:
        return row["liquidity"] > 5_000 and row["age_hours"] < max_age_hours

    async def _search_query(self, query: str) -> list[dict[str, Any]]:
        data = await self._get_json(SEARCH_URL, params={"q": query})
        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        logger.info("scanner query=%s pairs=%s", query, len(pairs))
        print(f"[scanner] query={query} pairs={len(pairs)}")
        out = []
        for p in pairs:
            row = self._normalize_pair(p)
            if row:
                out.append(row)
        return out

    async def scan_new_tokens(self, max_age_hours: float = 24.0) -> list[dict[str, Any]]:
        await self.start()
        rows: list[dict[str, Any]] = []
        for q in SCAN_QUERIES:
            try:
                rows.extend(await self._search_query(q))
            except Exception as exc:
                logger.warning("query failed %s: %s", q, exc)

        uniq: dict[str, dict[str, Any]] = {}
        for row in rows:
            uid = token_uid(row["chain"], row["contract"])
            prev = uniq.get(uid)
            if not prev or row["liquidity"] > prev["liquidity"]:
                uniq[uid] = row

        filtered = [r for r in uniq.values() if self._passes(r, max_age_hours)]
        filtered.sort(key=lambda x: (x["volume_1h"], x["liquidity"]), reverse=True)

        if len(filtered) < 10:
            # Emergency fallback: loosen only age threshold to avoid empty responses.
            fallback = [r for r in uniq.values() if r["liquidity"] > 5_000]
            fallback.sort(key=lambda x: (x["volume_1h"], -x["age_hours"]), reverse=True)
            filtered = fallback[:20]
        else:
            filtered = filtered[:20]

        logger.info("scanner final tokens=%s", len(filtered))
        print(f"[scanner] final_tokens={len(filtered)}")
        return filtered
