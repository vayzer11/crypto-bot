from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiohttp

from contract_checker import ContractSecurityChecker

logger = logging.getLogger(__name__)
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "").strip()
SEEN_TOKENS_FILE = Path(__file__).resolve().parent / "seen_tokens.json"
SUPPORTED = {"ethereum": "eth", "bsc": "bsc", "solana": "solana", "base": "base", "arbitrum": "arbitrum", "polygon": "polygon"}


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _i(v: Any, d: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def token_uid(chain: str, contract: str) -> str:
    return f"{str(chain).lower()}:{str(contract).lower()}"


def ensure_seen_tokens_file() -> None:
    if not SEEN_TOKENS_FILE.exists():
        SEEN_TOKENS_FILE.write_text("[]", encoding="utf-8")


def load_seen_tokens() -> set[str]:
    ensure_seen_tokens_file()
    try:
        raw = json.loads(SEEN_TOKENS_FILE.read_text(encoding="utf-8"))
        return {str(x) for x in raw}
    except Exception:
        return set()


def save_seen_tokens(seen_tokens: set[str]) -> None:
    SEEN_TOKENS_FILE.write_text(json.dumps(sorted(seen_tokens), ensure_ascii=False, indent=2), encoding="utf-8")


class MultiChainScanner:
    def __init__(self) -> None:
        self.security = ContractSecurityChecker()
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        assert self.session is not None
        for attempt in range(3):
            try:
                async with self.session.get(url, params=params, headers=headers) as resp:
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.8 + attempt)
        return None

    async def _fetch_candidates(self) -> list[dict[str, Any]]:
        profiles = await self._get_json("https://api.dexscreener.com/token-profiles/latest/v1")
        boosts = await self._get_json("https://api.dexscreener.com/token-boosts/latest/v1")
        merged: dict[str, dict[str, Any]] = {}
        for row in (profiles or []) + (boosts or []):
            chain = str(row.get("chainId", "")).lower()
            addr = str(row.get("tokenAddress", "")).lower()
            if chain in SUPPORTED and addr:
                merged[token_uid(chain, addr)] = row
        return list(merged.values())[:140]

    async def _fetch_pairs(self, contract: str) -> list[dict[str, Any]]:
        data = await self._get_json(f"https://api.dexscreener.com/latest/dex/tokens/{contract}")
        return data.get("pairs", []) if isinstance(data, dict) else []

    async def _holders_from_moralis(self, chain: str, contract: str) -> int:
        if not MORALIS_API_KEY:
            return 0
        chain_alias = SUPPORTED[chain]
        headers = {"X-API-Key": MORALIS_API_KEY}
        url = f"https://deep-index.moralis.io/api/v2.2/erc20/{contract}/holders"
        try:
            data = await self._get_json(url, params={"chain": chain_alias}, headers=headers)
            return _i((data or {}).get("total"))
        except Exception:
            return 0

    async def _normalize_pair(self, pair: dict[str, Any]) -> dict[str, Any] | None:
        chain = str(pair.get("chainId", "")).lower()
        if chain not in SUPPORTED:
            return None
        base = pair.get("baseToken") or {}
        contract = str(base.get("address", "")).strip()
        if not contract:
            return None
        sec = await self.security.check_contract_security(contract, chain)
        holders = sec.get("holder_count") or await self._holders_from_moralis(chain, contract)
        created = _i(pair.get("pairCreatedAt"))
        age_hours = max((int(time.time() * 1000) - created) / 3_600_000, 0.0) if created else 999.0
        return {
            "name": str(base.get("name", "Unknown")),
            "symbol": str(base.get("symbol", "UNK")).upper(),
            "contract": contract,
            "chain": chain,
            "age_hours": age_hours,
            "holders": _i(holders),
            "liquidity": _f((pair.get("liquidity") or {}).get("usd")),
            "market_cap": _f(pair.get("marketCap") or pair.get("fdv")),
            "volume_24h": _f((pair.get("volume") or {}).get("h24")),
            "volume_1h": _f((pair.get("volume") or {}).get("h1")),
            "buy_tax": sec.get("buy_tax"),
            "sell_tax": sec.get("sell_tax"),
            "is_honeypot": bool(sec.get("is_honeypot")),
            "is_mintable": bool(sec.get("is_mintable")),
            "is_blacklisted": bool(sec.get("is_blacklisted")),
            "is_renounced": bool(sec.get("is_renounced")),
            "lp_locked": bool(sec.get("lp_locked")),
            "risk_score": _i(sec.get("risk_score")),
            "dex_url": str(pair.get("url", "")),
        }

    def _passes_filters(self, row: dict[str, Any], *, max_age_hours: float) -> bool:
        return (
            row["age_hours"] <= max_age_hours
            and row["holders"] >= 2000
            and row["liquidity"] >= 50_000
            and row["buy_tax"] == 0
            and row["sell_tax"] == 0
        )

    async def scan_new_tokens(self, max_age_hours: float = 24.0) -> list[dict[str, Any]]:
        await self.start()
        out: list[dict[str, Any]] = []
        candidates = await self._fetch_candidates()
        seen: set[str] = set()
        for c in candidates:
            addr = str(c.get("tokenAddress", "")).strip()
            if not addr or addr.lower() in seen:
                continue
            seen.add(addr.lower())
            try:
                pairs = await self._fetch_pairs(addr)
            except Exception as exc:
                logger.warning("pairs error %s: %s", addr, exc)
                continue
            valid = [p for p in pairs if str((p or {}).get("chainId", "")).lower() in SUPPORTED]
            if not valid:
                continue
            best = max(valid, key=lambda p: _f((p.get("liquidity") or {}).get("usd")))
            row = await self._normalize_pair(best)
            if row and self._passes_filters(row, max_age_hours=max_age_hours):
                out.append(row)
        out.sort(key=lambda x: (x["volume_1h"], x["volume_24h"]), reverse=True)
        return out


def calculate_x1000_score(token: dict[str, Any], security: dict[str, Any]) -> int:
    score = 0
    age = _f(token.get("age_hours"), 999)
    liq = _f(token.get("liquidity"))
    vol1h = _f(token.get("volume_1h"))
    if age <= 0.2:
        score += 35
    elif age <= 1:
        score += 25
    elif age <= 6:
        score += 15
    if liq >= 100_000:
        score += 20
    elif liq >= 50_000:
        score += 12
    if liq > 0 and (vol1h / liq) >= 0.25:
        score += 20
    if security.get("buy_tax") == 0 and security.get("sell_tax") == 0:
        score += 10
    if not security.get("is_honeypot"):
        score += 10
    return max(0, min(100, score))
