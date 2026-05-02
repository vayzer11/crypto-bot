from __future__ import annotations

import asyncio
import json
import logging
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
MEME_KEYWORDS = ["pepe", "doge", "shib", "cat", "moon", "elon", "baby", "inu", "wojak", "bonk"]
DEFI_KEYWORDS = ["defi", "swap", "farm", "yield", "vault", "dex", "lending", "stake", "dao"]
SEARCH_QUERIES = ["new", "ethereum", "bsc", "solana", "base", "arbitrum", "polygon", *MEME_KEYWORDS]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _age_hours(pair_created_at_ms: Any) -> float:
    created = int(_f(pair_created_at_ms, 0))
    if created <= 0:
        return 999.0
    return max((int(time.time() * 1000) - created) / 3_600_000, 0.0)


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
    bt = security.get("buy_tax")
    st = security.get("sell_tax")
    if bt == 0 and st == 0:
        score += 12
    elif st is not None and float(st) < 10 and (bt is None or float(bt) < 10):
        score += 9
    if not security.get("is_honeypot"):
        score += 10
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

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        assert self.session is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                    return data
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.8 + attempt)
        raise RuntimeError(f"request failed: {last_error}")

    def _normalize_pair(self, pair: dict[str, Any]) -> dict[str, Any] | None:
        chain = str(pair.get("chainId", "")).lower()
        if chain not in SUPPORTED_CHAINS:
            return None

        base = pair.get("baseToken") or {}
        contract = str(base.get("address", "")).strip()
        if not contract:
            return None

        name = str(base.get("name", "Unknown"))
        symbol = str(base.get("symbol", "UNK")).upper()
        text = f"{name} {symbol}".lower()
        category = "meme" if any(k in text for k in MEME_KEYWORDS) else "defi" if any(k in text for k in DEFI_KEYWORDS) else "other"

        return {
            "name": name,
            "symbol": symbol,
            "contract": contract,
            "chain": chain,
            "age_hours": _age_hours(pair.get("pairCreatedAt")),
            "price": _f(pair.get("priceUsd")),
            "liquidity": _f((pair.get("liquidity") or {}).get("usd")),
            "market_cap": _f(pair.get("marketCap") or pair.get("fdv")),
            "volume_24h": _f((pair.get("volume") or {}).get("h24")),
            "volume_1h": _f((pair.get("volume") or {}).get("h1")),
            "fdv": _f(pair.get("fdv")),
            "txns_1h_buys": int(_f(((pair.get("txns") or {}).get("h1") or {}).get("buys"))),
            "txns_1h_sells": int(_f(((pair.get("txns") or {}).get("h1") or {}).get("sells"))),
            "price_change_1h": _f((pair.get("priceChange") or {}).get("h1")),
            "price_change_24h": _f((pair.get("priceChange") or {}).get("h24")),
            "buy_tax": None,
            "sell_tax": None,
            "is_honeypot": None,
            "is_mintable": None,
            "is_blacklisted": None,
            "is_renounced": None,
            "lp_locked": None,
            "owner_percent": None,
            "top10_holders_pct": None,
            "risk_score": 0,
            "dex_url": str(pair.get("url", "")),
            "category": category,
        }

    def _filter_core(self, row: dict[str, Any], max_age_hours: float = 24.0) -> bool:
        return row["liquidity"] > 5_000 and row["age_hours"] < max_age_hours

    async def _query_search(self, query: str) -> list[dict[str, Any]]:
        data = await self._get_json(SEARCH_URL, params={"q": query})
        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        rows = []
        for pair in pairs:
            normalized = self._normalize_pair(pair)
            if normalized:
                rows.append(normalized)
        return rows

    async def _query_trending(self) -> list[dict[str, Any]]:
        data = await self._get_json(TRENDING_URL)
        pairs = (data.get("pairs") or []) if isinstance(data, dict) else []
        rows = []
        for pair in pairs:
            normalized = self._normalize_pair(pair)
            if normalized:
                rows.append(normalized)
        return rows

    async def scan_all_tokens(self, max_age_hours: float = 24.0) -> list[dict[str, Any]]:
        await self.start()
        rows: list[dict[str, Any]] = []
        for q in SEARCH_QUERIES:
            try:
                rows.extend(await self._query_search(q))
            except Exception as exc:
                logger.warning("search query failed: %s %s", q, exc)
        try:
            rows.extend(await self._query_trending())
        except Exception as exc:
            logger.warning("trending query failed: %s", exc)

        dedup: dict[str, dict[str, Any]] = {}
        for row in rows:
            uid = token_uid(row["chain"], row["contract"])
            prev = dedup.get(uid)
            if not prev or row["volume_1h"] > prev["volume_1h"]:
                dedup[uid] = row

        filtered = [row for row in dedup.values() if self._filter_core(row, max_age_hours=max_age_hours)]
        # newest first, then volume
        filtered.sort(key=lambda x: (x["age_hours"], -x["volume_1h"]))
        if len(filtered) < 10:
            fallback = [row for row in dedup.values() if row["liquidity"] > 5_000]
            fallback.sort(key=lambda x: (-x["volume_1h"], x["age_hours"]))
            filtered = fallback[:20]
        else:
            filtered = filtered[:20]

        return filtered

    async def scan_new_tokens(self, max_age_hours: float = 24.0) -> list[dict[str, Any]]:
        return await self.scan_all_tokens(max_age_hours=max_age_hours)

    async def enrich_security(self, row: dict[str, Any]) -> dict[str, Any]:
        sec = await self.security.check_contract_security(row["contract"], row["chain"])
        row["buy_tax"] = sec.get("buy_tax")
        row["sell_tax"] = sec.get("sell_tax")
        row["is_honeypot"] = sec.get("is_honeypot")
        row["is_mintable"] = sec.get("is_mintable")
        row["is_blacklisted"] = sec.get("is_blacklisted")
        row["is_renounced"] = sec.get("is_renounced")
        row["lp_locked"] = sec.get("lp_locked")
        row["owner_percent"] = sec.get("owner_percent")
        # GoPlus often has owner% but not top10%. Use it as conservative whale proxy if explicit top10 not present.
        row["top10_holders_pct"] = sec.get("top10_holders_pct") if sec.get("top10_holders_pct") is not None else sec.get("owner_percent")
        return row

    def dump_risk_score(self, row: dict[str, Any], prev_volume_1h: float | None = None) -> int:
        risk = 0
        if 1 <= row["age_hours"] <= 6 and row["market_cap"] >= 1_000_000:
            risk += 20
        if _f(row.get("top10_holders_pct")) >= 80:
            risk += 22
        if row.get("lp_locked") is False:
            risk += 15
        if row.get("is_mintable") is True:
            risk += 15
        if row.get("is_renounced") is False:
            risk += 12
        if prev_volume_1h and prev_volume_1h > 0:
            spike = ((row["volume_1h"] - prev_volume_1h) / prev_volume_1h) * 100
            if spike >= 500:
                risk += 16
        elif row["volume_1h"] > 0 and row["liquidity"] > 0 and (row["volume_1h"] / row["liquidity"]) >= 5:
            risk += 10
        if row.get("is_honeypot") is True:
            risk += 20
        row["risk_score"] = min(100, int(risk))
        return row["risk_score"]

    def is_dump_incoming(self, row: dict[str, Any]) -> bool:
        return row.get("risk_score", 0) >= 70
