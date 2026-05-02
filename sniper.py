from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis import CryptoAnalyzer
from contract_checker import ContractSecurityChecker
from scanner import MultiChainScanner, calculate_x1000_score, load_seen_tokens, save_seen_tokens, token_uid

logger = logging.getLogger(__name__)
USERS_FILE = Path(__file__).resolve().parent / "users.json"

scanner = MultiChainScanner()
checker = ContractSecurityChecker()
ai_analyzer = CryptoAnalyzer()

SCANNER_STATE: dict[str, Any] = {
    "running": False,
    "tokens_scanned_today": 0,
    "signals_sent_today": 0,
    "last_signal_time": None,
    "last_scan_time": None,
    "last_error": None,
    "day": datetime.now(timezone.utc).date().isoformat(),
}

CHAIN_CHECK = {
    "ethereum": "eth",
    "bsc": "bsc",
    "solana": "solana",
    "base": "base",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
}

CHAIN_LABEL_RU = {
    "ethereum": "ETHEREUM",
    "bsc": "BSC",
    "solana": "SOLANA",
    "base": "BASE",
    "arbitrum": "ARBITRUM",
    "polygon": "POLYGON",
}

_last_scam_broadcast: float | None = None
SCAM_ALERT_INTERVAL_SEC = 2 * 3600


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
    if v >= 1_000_000_000:
        return f"${v / 1e9:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.2f}K"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.8f}"


def _age_hours_minutes(age_hours: float) -> str:
    h = int(age_hours)
    m = max(0, int(round((age_hours - h) * 60)))
    return f"{h} ч {m} мин"


async def _groq_signal_blurb(name: str, symbol: str, score: int, chain: str) -> str:
    return await ai_analyzer.groq_chat(
        "Пиши только по-русски. Ровно 2 коротких предложения. Без Markdown и без эмодзи в начале.",
        f"Мем-токен {name} ({symbol}), сеть {chain}, условный скор снайпера {score}/100. "
        f"Кратко: риск и что учесть входящему.",
        max_tokens=120,
    )


async def _format_signal(token: dict[str, Any], sec: dict[str, Any], score: int) -> str:
    chain_key = str(token.get("chain", "")).lower()
    net = CHAIN_LABEL_RU.get(chain_key, chain_key.upper())
    sym = str(token.get("symbol", "UNK")).upper()
    name = str(token.get("name", sym))
    price = float(token.get("price") or 0)
    liq = float(token.get("liquidity") or 0)
    mcap = float(token.get("market_cap") or 0)
    v24 = float(token.get("volume_24h") or 0)
    vol_x = (v24 / liq) if liq > 0 else 0.0

    buy_t = sec.get("buy_tax")
    sell_t = sec.get("sell_tax")
    tax_buy_s = f"{buy_t:.0f}" if buy_t is not None else "?"
    tax_sell_s = f"{sell_t:.0f}" if sell_t is not None else "?"

    holders = sec.get("holder_count")
    holders_s = f"{int(holders):,}" if holders else "—"

    hp_ok = not sec.get("is_honeypot")
    hp_line = "✅ НЕТ" if hp_ok else "❌ ДА"

    lp = sec.get("lp_locked")
    if lp is True:
        lp_line = "заблокирована ✅"
    elif lp is False:
        lp_line = "не заблокирована ⚠️"
    else:
        lp_line = "неизвестно"

    high = price * 1.025
    low = price * 0.94
    rng = high - low
    entry1 = high - 0.5 * rng
    entry2 = high - 0.618 * rng
    stop = entry2 * 0.85
    avg = 0.3 * entry1 + 0.7 * entry2
    tp2 = avg * 2
    tp5 = avg * 5
    tp10 = avg * 10

    ai_txt = await _groq_signal_blurb(name, sym, score, net)

    dex_url = str(token.get("dex_url") or "https://dexscreener.com")

    return (
        f"🚀 [MEME-SNIPER] ${sym} — Score: {score}/100\n\n"
        f"🌐 Сеть: {net}\n"
        f"🕐 Возраст: {_age_hours_minutes(float(token.get('age_hours') or 0))}\n"
        f"💰 Цена: {_fmt_usd(price)}\n"
        f"📊 Капа: {_fmt_usd(mcap)} | Ликвидность: {_fmt_usd(liq)}\n"
        f"📈 Объём 24ч: {_fmt_usd(v24)} ({vol_x:.1f}x от ликв.)\n\n"
        f"📋 Контракт: `{token['contract']}`\n"
        f"🔗 [DexScreener]({dex_url})\n\n"
        f"🛡️ Безопасность:\n"
        f"└ Налог: {tax_buy_s}% / {tax_sell_s}%\n"
        f"└ Холдеров: {holders_s}\n"
        f"└ Honeypot: {hp_line}\n"
        f"└ LP: {lp_line}\n\n"
        f"🤖 AI: {ai_txt}\n\n"
        f"🎯 Точки входа:\n"
        f"└ Вход 1: {_fmt_usd(entry1)} (30% позиции) — Fib 0.5\n"
        f"└ Вход 2: {_fmt_usd(entry2)} (70% позиции) — Fib 0.618\n"
        f"└ Стоп: {_fmt_usd(stop)} (-15%)\n\n"
        f"🎯 Цели:\n"
        f"└ x2: {_fmt_usd(tp2)}\n"
        f"└ x5: {_fmt_usd(tp5)}\n"
        f"└ x10: {_fmt_usd(tp10)}\n\n"
        f"⚠️ Риск: ВЫСОКИЙ. DYOR."
    )


async def _maybe_broadcast_scam(bot: Any) -> None:
    global _last_scam_broadcast
    now = time.time()
    if _last_scam_broadcast is None:
        _last_scam_broadcast = now
        return
    if now - _last_scam_broadcast < SCAM_ALERT_INTERVAL_SEC:
        return
    _last_scam_broadcast = now
    try:
        rows = await ai_analyzer.find_scam_whales_results()
        dangerous = [r for r in rows if r.get("risk", 0) > 75]
        if not dangerous:
            return
        text = ai_analyzer.format_scam_whales_text(dangerous)
        for uid in load_users():
            try:
                await bot.send_message(uid, text, parse_mode="Markdown", disable_web_page_preview=True)
            except Exception as exc:
                logger.warning("scam broadcast failed %s: %s", uid, exc)
    except Exception as exc:
        logger.exception("scam broadcast error: %s", exc)


async def scanner_loop(bot: Any) -> None:
    SCANNER_STATE["running"] = True
    seen = load_seen_tokens()
    while True:
        SCANNER_STATE["last_scan_time"] = datetime.now(timezone.utc).isoformat()
        try:
            await _maybe_broadcast_scam(bot)

            tokens = await scanner.scan_new_tokens(max_age_hours=1.0)
            for token in tokens:
                uid = token_uid(token["chain"], token["contract"])
                if uid in seen:
                    continue
                seen.add(uid)
                save_seen_tokens(seen)
                SCANNER_STATE["tokens_scanned_today"] += 1

                ck = CHAIN_CHECK.get(str(token.get("chain", "")).lower(), "eth")
                sec = await checker.check_contract_security(token["contract"], ck)

                if sec.get("is_honeypot"):
                    continue
                st = sec.get("sell_tax")
                if st is not None and float(st) >= 10:
                    continue

                score = calculate_x1000_score(token, sec)
                if score < 40:
                    continue

                token["risk_score"] = sec.get("risk_score", 0)
                message = await _format_signal(token, sec, score)
                for user_id in load_users():
                    try:
                        await bot.send_message(
                            user_id, message, parse_mode="Markdown", disable_web_page_preview=True
                        )
                        SCANNER_STATE["signals_sent_today"] += 1
                        SCANNER_STATE["last_signal_time"] = datetime.now(timezone.utc).isoformat()
                    except Exception as exc:
                        logger.warning("sniper send failed %s: %s", user_id, exc)
        except Exception as exc:
            SCANNER_STATE["last_error"] = str(exc)
            logger.exception("sniper loop error: %s", exc)
        await asyncio.sleep(10)
