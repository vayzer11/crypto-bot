"""
contract_checker.py
Проверка безопасности контрактов через GoPlus (бесплатный API).
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

GOPLUS_API = "https://api.gopluslabs.io/api/v1"


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


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _calc_safety(result: dict[str, Any]) -> dict[str, Any]:
    safety_score = 100
    red_flags: list[str] = []
    yellow_flags: list[str] = []

    if result["is_honeypot"]:
        safety_score -= 100
        red_flags.append("🚨 HONEYPOT — продать невозможно!")
    if result["sell_tax"] > 10:
        safety_score -= 30
        red_flags.append(f"🚨 Налог продажи {result['sell_tax']}%")
    elif result["sell_tax"] > 5:
        safety_score -= 15
        yellow_flags.append(f"⚠️ Налог продажи {result['sell_tax']}%")
    if result["buy_tax"] > 10:
        safety_score -= 20
        yellow_flags.append(f"⚠️ Налог покупки {result['buy_tax']}%")
    if result["is_mintable"]:
        safety_score -= 20
        yellow_flags.append("⚠️ Можно минтить новые токены")
    if result["owner_percent"] > 50:
        safety_score -= 25
        red_flags.append(f"🚨 Девелопер держит {result['owner_percent']}% токенов")
    elif result["owner_percent"] > 20:
        safety_score -= 10
        yellow_flags.append(f"⚠️ Девелопер держит {result['owner_percent']}% токенов")
    if not result["lp_locked"]:
        safety_score -= 15
        yellow_flags.append("⚠️ Ликвидность не заблокирована")
    if result["holder_count"] < 50:
        safety_score -= 10
        yellow_flags.append(f"⚠️ Мало холдеров: {result['holder_count']}")
    if result["is_blacklisted"]:
        safety_score -= 20
        red_flags.append("🚨 Обнаружены blacklist-функции")
    if result["is_proxy"]:
        safety_score -= 8
        yellow_flags.append("⚠️ Прокси-контракт (можно изменить логику)")
    if not result["is_open_source"]:
        safety_score -= 10
        yellow_flags.append("⚠️ Контракт не верифицирован")

    safety_score = max(0, min(100, safety_score))
    if safety_score >= 80:
        verdict = "✅ БЕЗОПАСНО"
    elif safety_score >= 60:
        verdict = "🟡 ОСТОРОЖНО"
    elif safety_score >= 40:
        verdict = "🟠 ВЫСОКИЙ РИСК"
    else:
        verdict = "🔴 ОПАСНО / СКАМ"

    return {
        "safety_score": safety_score,
        "verdict": verdict,
        "red_flags": red_flags,
        "yellow_flags": yellow_flags,
    }


class ContractSecurityChecker:
    async def _get_json(self, url: str) -> dict[str, Any] | None:
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning("GoPlus HTTP %s: %s", response.status, url)
                        return None
                    return await response.json()
        except Exception as exc:
            logger.error("Ошибка GoPlus запроса: %s", exc)
            return None

    def _normalize_goplus_payload(self, payload: dict[str, Any], contract: str) -> dict[str, Any]:
        result_block = payload.get("result") or {}
        if isinstance(result_block, list):
            token_data = result_block[0] if result_block else {}
        elif isinstance(result_block, dict):
            token_data = result_block.get(contract.lower()) or result_block.get(contract) or {}
            if not token_data and result_block:
                token_data = next(iter(result_block.values()), {})
        else:
            token_data = {}

        owner_percent = _safe_float(
            token_data.get("owner_percent")
            or token_data.get("creator_percent")
            or token_data.get("creator_address_percent")
        ) * 100
        lp_lock_percent = _safe_float(
            token_data.get("lp_locked_total")
            or token_data.get("lp_lock_percent")
            or token_data.get("locked_lp_percent")
        ) * (100 if _safe_float(token_data.get("lp_locked_total")) <= 1 else 1)
        holder_count = _safe_int(token_data.get("holder_count") or token_data.get("holders"))
        buy_tax = _safe_float(token_data.get("buy_tax"))
        sell_tax = _safe_float(token_data.get("sell_tax"))

        normalized = {
            "is_honeypot": _safe_bool(token_data.get("is_honeypot")),
            "buy_tax": round(buy_tax, 2),
            "sell_tax": round(sell_tax, 2),
            "is_mintable": _safe_bool(token_data.get("is_mintable")),
            "is_proxy": _safe_bool(token_data.get("is_proxy")),
            "owner_percent": round(owner_percent, 2),
            "holder_count": holder_count,
            "is_blacklisted": _safe_bool(token_data.get("is_blacklisted")),
            "lp_locked": lp_lock_percent > 0.0 or _safe_bool(token_data.get("is_locked")),
            "lp_lock_percent": round(lp_lock_percent, 2),
            "creator_address": str(token_data.get("creator_address") or ""),
            "is_open_source": _safe_bool(token_data.get("is_open_source")) or _safe_bool(token_data.get("is_verified")),
        }
        return normalized

    async def check_contract_security(self, contract_address: str, chain_id: str) -> dict[str, Any]:
        try:
            if chain_id == "solana":
                url = f"{GOPLUS_API}/solana/token_security?contract_addresses={contract_address}"
            else:
                url = f"{GOPLUS_API}/token_security/{chain_id}?contract_addresses={contract_address}"

            payload = await self._get_json(url)
            if not payload:
                raise RuntimeError("Пустой ответ GoPlus")

            result = self._normalize_goplus_payload(payload, contract_address)
            safety = _calc_safety(result)
            return {**result, **safety}
        except Exception as exc:
            logger.error("Ошибка проверки контракта %s: %s", contract_address, exc)
            fallback = {
                "is_honeypot": False,
                "buy_tax": 0.0,
                "sell_tax": 0.0,
                "is_mintable": False,
                "is_proxy": False,
                "owner_percent": 0.0,
                "holder_count": 0,
                "is_blacklisted": False,
                "lp_locked": False,
                "lp_lock_percent": 0.0,
                "creator_address": "",
                "is_open_source": True,
            }
            return {
                **fallback,
                "safety_score": 35,
                "verdict": "🟠 ВЫСОКИЙ РИСК",
                "red_flags": [],
                "yellow_flags": ["⚠️ Не удалось получить полный отчёт безопасности"],
            }
