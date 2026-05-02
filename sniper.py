from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contract_checker import ContractSecurityChecker
from scanner import MultiChainScanner, calculate_x1000_score, load_seen_tokens, save_seen_tokens, token_uid

logger = logging.getLogger(__name__)
USERS_FILE = Path(__file__).resolve().parent / "users.json"

scanner = MultiChainScanner()
checker = ContractSecurityChecker()

SCANNER_STATE: dict[str, Any] = {
    "running": False,
    "tokens_scanned_today": 0,
    "signals_sent_today": 0,
    "last_signal_time": None,
    "last_scan_time": None,
    "last_error": None,
    "day": datetime.now(timezone.utc).date().isoformat(),
}


def load_users() -> set[int]:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data if str(x).isdigit()}
    except Exception:
        return set()


def get_sniper_status_text() -> str:
    status = "🟢 RUNNING" if SCANNER_STATE["running"] else "🔴 STOPPED"
    return (
        "🎯 *SNIPER STATUS*\n\n"
        f"• Scanner: {status}\n"
        f"• Scanned today: {SCANNER_STATE['tokens_scanned_today']}\n"
        f"• Alerts sent: {SCANNER_STATE['signals_sent_today']}\n"
        f"• Last signal: {SCANNER_STATE['last_signal_time'] or '—'}\n"
        f"• Last scan: {SCANNER_STATE['last_scan_time'] or '—'}"
    )


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.2f}K"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.8f}"


def _risk_score(sec: dict[str, Any]) -> int:
    score = 0
    if sec.get("is_honeypot"):
        score += 55
    if sec.get("is_mintable"):
        score += 15
    if sec.get("is_blacklisted"):
        score += 15
    if sec.get("sell_tax") not in (None, 0):
        score += 10
    if sec.get("buy_tax") not in (None, 0):
        score += 10
    if not sec.get("lp_locked"):
        score += 10
    if not sec.get("is_renounced"):
        score += 8
    return min(100, score)


def _format_signal(token: dict[str, Any], sec: dict[str, Any], score: int) -> str:
    age_min = int(float(token.get("age_hours", 0)) * 60)
    risk = _risk_score(sec)
    return (
        f"🚨 *SNIPER ALERT*\n\n"
        f"Token: *{token['name']} ({token['symbol']})*\n"
        f"Chain: {token['chain'].upper()}\n"
        f"Listed: {age_min} min ago\n"
        f"Liquidity: {_fmt_usd(float(token.get('liquidity', 0)))}\n"
        f"Market Cap: {_fmt_usd(float(token.get('market_cap', 0)))}\n"
        f"Volume 1h: {_fmt_usd(float(token.get('volume_1h', 0)))}\n"
        f"Tax: {sec.get('buy_tax')}% / {sec.get('sell_tax')}%\n"
        f"Honeypot: {'YES' if sec.get('is_honeypot') else 'NO'}\n"
        f"Renounced: {'YES' if sec.get('is_renounced') else 'NO'}\n"
        f"LP Locked: {'YES' if sec.get('lp_locked') else 'NO'}\n"
        f"Scam Risk: *{risk}/100*\n"
        f"Sniper Score: *{score}/100*\n"
        f"Contract: `{token['contract']}`\n"
        f"[Open Pair]({token['dex_url']})"
    )


async def scanner_loop(bot: Any) -> None:
    SCANNER_STATE["running"] = True
    seen = load_seen_tokens()
    while True:
        SCANNER_STATE["last_scan_time"] = datetime.now(timezone.utc).isoformat()
        try:
            tokens = await scanner.scan_new_tokens(max_age_hours=1.0)
            for token in tokens:
                uid = token_uid(token["chain"], token["contract"])
                if uid in seen:
                    continue
                seen.add(uid)
                save_seen_tokens(seen)
                SCANNER_STATE["tokens_scanned_today"] += 1

                # Requested endpoint: token_security/1
                sec = await checker.check_contract_security(token["contract"], "eth")
                if sec.get("is_honeypot"):
                    continue
                if sec.get("buy_tax") not in (0, 0.0) or sec.get("sell_tax") not in (0, 0.0):
                    continue
                if not sec.get("lp_locked"):
                    continue
                if not sec.get("is_renounced"):
                    continue

                token["risk_score"] = _risk_score(sec)
                score = calculate_x1000_score(token, sec)
                message = _format_signal(token, sec, score)
                for user_id in load_users():
                    try:
                        await bot.send_message(user_id, message, parse_mode="Markdown", disable_web_page_preview=True)
                        SCANNER_STATE["signals_sent_today"] += 1
                        SCANNER_STATE["last_signal_time"] = datetime.now(timezone.utc).isoformat()
                    except Exception as exc:
                        logger.warning("sniper send failed %s: %s", user_id, exc)
        except Exception as exc:
            SCANNER_STATE["last_error"] = str(exc)
            logger.exception("sniper loop error: %s", exc)
        await asyncio.sleep(10)
