from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)
GOPLUS_API = "https://api.gopluslabs.io/api/v1"
CHAIN_MAP = {"eth": "1", "bsc": "56", "base": "8453", "arbitrum": "42161", "polygon": "137", "solana": "solana"}


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


def _b(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes"}


class ContractSecurityChecker:
    async def _get_json(self, url: str) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=20)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status >= 400:
                            raise RuntimeError(f"GoPlus HTTP {resp.status}")
                        return await resp.json(content_type=None)
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.7 + attempt)
        raise RuntimeError(f"GoPlus unavailable: {last_error}")

    def _normalize(self, payload: dict[str, Any], contract: str) -> dict[str, Any]:
        block = payload.get("result") or {}
        if isinstance(block, dict):
            item = block.get(contract.lower()) or block.get(contract) or next(iter(block.values()), {})
        elif isinstance(block, list):
            item = block[0] if block else {}
        else:
            item = {}

        buy_tax = _f(item.get("buy_tax"), -1)
        sell_tax = _f(item.get("sell_tax"), -1)
        owner_address = str(item.get("owner_address") or "")
        is_renounced = _b(item.get("is_renounced")) or owner_address in {"", "0x0000000000000000000000000000000000000000"}
        lp_locked_pct = _f(item.get("lp_locked_total") or item.get("locked_lp_percent") or item.get("lp_lock_percent"))
        if lp_locked_pct <= 1:
            lp_locked_pct *= 100

        return {
            "is_honeypot": _b(item.get("is_honeypot")),
            "buy_tax": None if buy_tax < 0 else round(buy_tax, 2),
            "sell_tax": None if sell_tax < 0 else round(sell_tax, 2),
            "is_mintable": _b(item.get("is_mintable")),
            "is_blacklisted": _b(item.get("is_blacklisted")),
            "is_open_source": _b(item.get("is_open_source")) or _b(item.get("is_verified")),
            "is_proxy": _b(item.get("is_proxy")),
            "is_renounced": is_renounced,
            "owner_address": owner_address,
            "owner_percent": round(_f(item.get("owner_percent")) * 100, 2),
            "holder_count": _i(item.get("holder_count")),
            "lp_locked": lp_locked_pct > 0 or _b(item.get("is_locked")),
            "lp_lock_percent": round(lp_locked_pct, 2),
        }

    def _score(self, r: dict[str, Any]) -> dict[str, Any]:
        score = 100
        flags: list[str] = []
        if r["is_honeypot"]:
            score -= 60
            flags.append("Honeypot")
        if r["is_mintable"]:
            score -= 15
            flags.append("Mint active")
        if r["is_blacklisted"]:
            score -= 15
            flags.append("Blacklist")
        if r["buy_tax"] is not None and r["buy_tax"] > 10:
            score -= 10
            flags.append("Buy tax > 10%")
        if r["sell_tax"] is not None and r["sell_tax"] > 10:
            score -= 15
            flags.append("Sell tax > 10%")
        if not r["lp_locked"]:
            score -= 10
            flags.append("LP unlocked")
        if not r["is_open_source"]:
            score -= 10
            flags.append("Unverified contract")
        if r["holder_count"] and r["holder_count"] < 100:
            score -= 10
            flags.append("Holders < 100")
        score = max(0, min(100, score))
        verdict = "SAFE" if score >= 75 else "CAUTION" if score >= 50 else "RISK"
        return {"risk_score": 100 - score, "safety_score": score, "verdict": verdict, "flags": flags}

    async def check_contract_security(self, contract_address: str, chain: str) -> dict[str, Any]:
        try:
            chain_key = CHAIN_MAP.get(chain, chain)
            if chain_key == "solana":
                url = f"{GOPLUS_API}/solana/token_security?contract_addresses={contract_address}"
            else:
                url = f"{GOPLUS_API}/token_security/{chain_key}?contract_addresses={contract_address}"
            raw = await self._get_json(url)
            normalized = self._normalize(raw, contract_address)
            return {**normalized, **self._score(normalized)}
        except Exception as exc:
            logger.error("contract check error %s %s: %s", chain, contract_address, exc)
            fallback = {
                "is_honeypot": False,
                "buy_tax": None,
                "sell_tax": None,
                "is_mintable": False,
                "is_blacklisted": False,
                "is_open_source": False,
                "is_proxy": False,
                "is_renounced": False,
                "owner_address": "",
                "owner_percent": 0.0,
                "holder_count": 0,
                "lp_locked": False,
                "lp_lock_percent": 0.0,
            }
            return {**fallback, "risk_score": 70, "safety_score": 30, "verdict": "RISK", "flags": ["Security API unavailable"]}

    async def check_and_format_report(self, contract_address: str, chain: str) -> str:
        r = await self.check_contract_security(contract_address, chain)
        return (
            f"🔍 Проверка контракта\n"
            f"Сеть: {chain.upper()}\n"
            f"Контракт: `{contract_address}`\n"
            f"Вердикт: {r['verdict']} | Риск: {r['risk_score']}/100\n"
            f"Honeypot: {'Да' if r['is_honeypot'] else 'Нет'}\n"
            f"Mint: {'Активен' if r['is_mintable'] else 'Отключен'}\n"
            f"Blacklist: {'Есть' if r['is_blacklisted'] else 'Нет'}\n"
            f"Налоги: {r['buy_tax'] if r['buy_tax'] is not None else 'N/A'}% / {r['sell_tax'] if r['sell_tax'] is not None else 'N/A'}%\n"
            f"Renounced: {'Да' if r['is_renounced'] else 'Нет'}\n"
            f"Ликвидность: {'Заблокирована' if r['lp_locked'] else 'Не заблокирована'}\n"
            f"Холдеры: {r['holder_count']}\n"
            f"Флаги: {', '.join(r['flags']) if r['flags'] else 'Нет'}"
        )
