from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scanner import MultiChainScanner, calculate_x1000_score, load_seen_tokens, save_seen_tokens, token_uid

logger = logging.getLogger(__name__)
USERS_FILE = Path(__file__).resolve().parent / "users.json"

scanner = MultiChainScanner()

SCANNER_STATE: dict[str, Any] = {
    "running": False,
    "tokens_scanned_today": 0,
    "signals_sent_today": 0,
    "last_signal_time": None,
    "last_scan_time": None,
    "last_error": None,
    "day": datetime.now(timezone.utc).date().isoformat(),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_users() -> set[int]:
    if not USERS_FILE.exists():
        try:
            USERS_FILE.write_text("[]", encoding="utf-8")
        except Exception:
            pass
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {int(x) for x in data if str(x).isdigit()}
    except Exception as exc:
        logger.error("Ошибка users.json: %s", exc)
    return set()


def get_sniper_status_text() -> str:
    status = "🟢 RUNNING" if SCANNER_STATE["running"] else "🔴 STOPPED"
    return (
        "🎯 *MEME SNIPER СТАТУС*\n\n"
        f"• Сканер: {status}\n"
        f"• Токенов проверено сегодня: {SCANNER_STATE['tokens_scanned_today']}\n"
        f"• Сигналов отправлено сегодня: {SCANNER_STATE['signals_sent_today']}\n"
        f"• Последний сигнал: {SCANNER_STATE['last_signal_time'] or '—'}\n"
        f"• Последнее сканирование: {SCANNER_STATE['last_scan_time'] or '—'}"
    )


def _reset_counters_daily() -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if SCANNER_STATE["day"] != today:
        SCANNER_STATE["day"] = today
        SCANNER_STATE["tokens_scanned_today"] = 0
        SCANNER_STATE["signals_sent_today"] = 0


def _entry_levels(price: float) -> tuple[float, float, float, float, float, float]:
    """Вход1, Вход2, стоп (-15%), x2, x5, x10 от условной базы ~цена."""
    p = max(price, 1e-18)
    e1 = p * 0.99
    e2 = p * 0.965
    sl = p * 0.85
    return e1, e2, sl, p * 2.0, p * 5.0, p * 10.0


def _fmt_usd(v: float) -> str:
    v = float(v)
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.2f}K"
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.0001:
        return f"${v:.6f}"
    return f"${v:.8f}"


async def _ai_text_ru(token: dict[str, Any], score: int) -> str:
    _ = score
    return "Сигнал снайпера валиден только при строгом риск-менеджменте и проверке контракта. Размер позиции держи минимальным."


def _format_signal(token: dict[str, Any], security: dict[str, Any], ai_text: str, score: int) -> str:
    chain = str(token.get("chain") or "")
    chain_ru = {"ethereum": "Ethereum", "bsc": "BSC", "base": "Base", "arbitrum": "Arbitrum", "polygon": "Polygon", "solana": "Solana"}.get(chain, chain)
    dex_slug = chain
    contract = str(token.get("contract") or "")
    dex_link = f"https://dexscreener.com/{dex_slug}/{contract}"

    price = _safe_float(token.get("price"))
    if price <= 0:
        price = _safe_float(token.get("market_cap")) / 1_000_000 or 1e-8

    age_h = _safe_float(token.get("age_hours"), 0.0)
    age_txt = f"{max(1, int(age_h))} часов" if age_h >= 1 else f"{max(1, int(age_h * 60))} минут"

    mcap = _safe_float(token.get("market_cap"))
    liq = _safe_float(token.get("liquidity"))
    vol24 = _safe_float(token.get("volume_24h"))
    vol_x = (vol24 / liq) if liq > 0 else _safe_float(token.get("vol_liq_ratio"))

    buy_tax = _safe_float(security.get("buy_tax"))
    sell_tax = _safe_float(security.get("sell_tax"))
    honeypot_line = "✅ НЕТ" if not token.get("is_honeypot") else "🚨 ДА"
    lp_ok = bool(token.get("lp_locked"))
    lp_line = "заблокирована ✅" if lp_ok else "не подтверждена ⚠️"

    e1, e2, sl, t2, t5, t10 = _entry_levels(price)

    sym = str(token.get("symbol") or "???").upper()

    return (
        f"🚀 [MEME-SNIPER] ${sym} — Score: {score}/100\n\n"
        f"🌐 Сеть: {chain_ru}\n"
        f"🕐 Возраст: {age_txt}\n"
        f"💰 Цена: {_fmt_usd(price)}\n"
        f"📊 Капа: {_fmt_usd(mcap)} | Ликвидность: {_fmt_usd(liq)}\n"
        f"📈 Объём 24ч: {_fmt_usd(vol24)} ({vol_x:.1f}x от ликвидности)\n\n"
        f"📋 Контракт: `{contract}`\n"
        f"🔗 [DexScreener]({dex_link})\n\n"
        f"🛡️ Безопасность:\n"
        f"└ Налог: {buy_tax:.1f}% / {sell_tax:.1f}%\n"
        f"└ Холдеров: {int(token.get('holders', 0))}\n"
        f"└ Honeypot: {honeypot_line}\n"
        f"└ LP: {lp_line}\n\n"
        f"⚠️ Scam Risk: {token.get('risk_score', 0)}/100\n"
        f"🤖 AI: {ai_text}\n\n"
        f"🎯 Точки входа:\n"
        f"└ Вход 1: {_fmt_usd(e1)} (30% позиции)\n"
        f"└ Вход 2: {_fmt_usd(e2)} (70% позиции)\n"
        f"└ Стоп: {_fmt_usd(sl)} (-15%)\n\n"
        f"🎯 Цели:\n"
        f"└ x2: {_fmt_usd(t2)}\n"
        f"└ x5: {_fmt_usd(t5)}\n"
        f"└ x10: {_fmt_usd(t10)}\n\n"
        f"⚠️ Риск: ВЫСОКИЙ. DYOR."
    )


async def scanner_loop(bot: Any) -> None:
    """Один цикл на всех пользователей: только bot.py вызывает start_polling; здесь нет Bot/Dispatcher."""
    SCANNER_STATE["running"] = True
    seen_tokens = load_seen_tokens()
    while True:
        _reset_counters_daily()
        SCANNER_STATE["last_scan_time"] = datetime.now(timezone.utc).isoformat()
        try:
            candidates = await scanner.scan_new_tokens(max_age_hours=0.5)
            for token in candidates:
                uid = token_uid(token["chain"], token["contract"])
                if uid in seen_tokens:
                    continue
                seen_tokens.add(uid)
                save_seen_tokens(seen_tokens)
                SCANNER_STATE["tokens_scanned_today"] += 1

                if token.get("is_honeypot"):
                    continue
                if _safe_float(token.get("sell_tax")) != 0.0 or _safe_float(token.get("buy_tax")) != 0.0:
                    continue
                if not token.get("lp_locked"):
                    continue
                if not token.get("is_renounced"):
                    continue

                score = calculate_x1000_score(token, token)
                if score < 50:
                    continue

                ai_text = await _ai_text_ru(token, score)
                message = _format_signal(token, token, ai_text, score)
                users = load_users()
                sent_any = False
                for user_id in users:
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=message,
                            parse_mode="Markdown",
                            disable_web_page_preview=True,
                        )
                        sent_any = True
                        await asyncio.sleep(0.08)
                    except Exception as exc:
                        logger.error("Ошибка отправки %s: %s", user_id, exc)
                if sent_any:
                    SCANNER_STATE["signals_sent_today"] += 1
                    SCANNER_STATE["last_signal_time"] = datetime.now(timezone.utc).isoformat()
                await asyncio.sleep(1.5)
        except Exception as exc:
            SCANNER_STATE["last_error"] = str(exc)
            logger.exception("Ошибка scanner_loop: %s", exc)
        await asyncio.sleep(180)
