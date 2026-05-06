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
from social_scanner import SocialScanner

logger = logging.getLogger(__name__)
USERS_FILE = Path(__file__).resolve().parent / "users.json"

scanner = MultiChainScanner()
checker = ContractSecurityChecker()
ai = CryptoAnalyzer()
social = SocialScanner()

SCANNER_STATE: dict[str, Any] = {
    "running": False,
    "tokens_scanned_today": 0,
    "signals_sent_today": 0,
    "last_signal_time": None,
    "last_scan_time": None,
}


def load_users() -> set[int]:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data if str(x).isdigit()}
    except Exception:
        return set()


def _fmt(v: float) -> str:
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.2f}M"
    if v >= 1e3:
        return f"${v/1e3:.2f}K"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.8f}"


def get_sniper_status_text() -> str:
    return (
        "⚡ *Снайпер статус*\n\n"
        f"Запущен: {'Да' if SCANNER_STATE['running'] else 'Нет'}\n"
        f"Проверено сегодня: {SCANNER_STATE['tokens_scanned_today']}\n"
        f"Сигналов отправлено: {SCANNER_STATE['signals_sent_today']}\n"
        f"Последний сигнал: {SCANNER_STATE['last_signal_time'] or '—'}"
    )


async def _groq_signal_blurb(token: dict[str, Any], sec: dict[str, Any]) -> str:
    return await ai.groq_chat(
        "Ты опытный снайпер-трейдер. Только русский, 2 предложения.",
        (
            f"Новый токен {token.get('name')} ({token.get('symbol')}) на {token.get('chain')}. "
            f"Возраст: {token.get('age_hours'):.2f} часов. Капа: ${token.get('market_cap')}. Ликвидность: ${token.get('liquidity')}. "
            f"Объём за 1ч: ${token.get('volume_1h')} ({(token.get('volume_1h',0)/max(token.get('liquidity',1),1)):.2f}x от ликвидности). "
            f"Покупок/продаж: {token.get('txns_1h_buys',0)}/{token.get('txns_1h_sells',0)}. "
            f"Безопасность: налоги {sec.get('buy_tax')}%/{sec.get('sell_tax')}%, LP {'locked' if sec.get('lp_locked') else 'unlocked'}."
            " За 2 предложения: почему интересен и главный риск."
        ),
        max_tokens=120,
    )


async def _format_signal(token: dict[str, Any], sec: dict[str, Any], score: int) -> str:
    price = float(token.get("price") or 0)
    high = price * 1.025
    low = price * 0.94
    rng = high - low
    e1 = high - 0.5 * rng
    e2 = high - 0.618 * rng
    stop = e2 * 0.85
    avg = 0.3 * e1 + 0.7 * e2
    ai_txt = await _groq_signal_blurb(token, sec)
    return (
        f"🚀 [MEME-SNIPER] ${str(token.get('symbol','UNK')).upper()} — Score: {score}/100\n\n"
        f"🌐 Сеть: {str(token.get('chain','')).upper()}\n"
        f"🕐 Возраст: {int(float(token.get('age_hours',0)))} ч {int((float(token.get('age_hours',0))%1)*60)} мин\n"
        f"💰 Цена: {_fmt(price)}\n"
        f"📊 Капа: {_fmt(float(token.get('market_cap') or 0))} | Ликвидность: {_fmt(float(token.get('liquidity') or 0))}\n"
        f"📈 Объём 24ч: {_fmt(float(token.get('volume_24h') or 0))} ({float(token.get('volume_24h',0))/max(float(token.get('liquidity',1)),1):.1f}x от ликв.)\n\n"
        f"📋 Контракт: `{token.get('contract')}`\n"
        f"🔗 [DexScreener]({token.get('dex_url')})\n\n"
        "🛡️ Безопасность:\n"
        f"└ Налог: {sec.get('buy_tax', '?')}% / {sec.get('sell_tax', '?')}%\n"
        f"└ Холдеров: {sec.get('holder_count', 0)}\n"
        f"└ Honeypot: {'✅ НЕТ' if not sec.get('is_honeypot') else '❌ ДА'}\n"
        f"└ LP: {'заблокирована ✅' if sec.get('lp_locked') else 'не заблокирована ⚠️'}\n\n"
        f"🤖 AI: {ai_txt}\n\n"
        "🎯 Точки входа:\n"
        f"└ Вход 1: {_fmt(e1)} (30%) — Fib 0.500\n"
        f"└ Вход 2: {_fmt(e2)} (70%) — Fib 0.618\n"
        f"└ Стоп: {_fmt(stop)} (-15%)\n\n"
        "🎯 Цели:\n"
        f"└ x2: {_fmt(avg*2)}\n"
        f"└ x5: {_fmt(avg*5)}\n"
        f"└ x10: {_fmt(avg*10)}\n\n"
        "⚠️ Риск: ВЫСОКИЙ. DYOR.\n\n---"
    )


async def scanner_loop(bot: Any) -> None:
    SCANNER_STATE["running"] = True
    seen = load_seen_tokens()
    last_social = 0.0
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
                sec = await checker.check_contract_security(token["contract"], token["chain"])
                if sec.get("is_honeypot") is True:
                    continue
                sell_tax = sec.get("sell_tax")
                if sell_tax is None or float(sell_tax) >= 10:
                    continue
                score = calculate_x1000_score(token, sec)
                if score < 40:
                    continue
                text = await _format_signal(token, sec, score)
                for u in load_users():
                    try:
                        await bot.send_message(u, text, parse_mode="Markdown", disable_web_page_preview=True)
                        SCANNER_STATE["signals_sent_today"] += 1
                        SCANNER_STATE["last_signal_time"] = datetime.now(timezone.utc).isoformat()
                    except Exception:
                        logger.warning("send fail %s", u)
            if time.time() - last_social > 4 * 3600:
                last_social = time.time()
                gems = await social.find_social_gems_data()
                top = [g for g in gems if g.get("social_score", 0) >= 70][:3]
                if top:
                    alert = "📡 *АВТО-СКАНЕР: Найдены социальные сигналы!*\n\n" + "\n".join(
                        f"• {g.get('symbol')} — Score {g.get('social_score')}, {g.get('potential')}" for g in top
                    )
                    for u in load_users():
                        try:
                            await bot.send_message(u, alert, parse_mode="Markdown")
                        except Exception:
                            pass
        except Exception as e:
            logger.exception("scanner loop error: %s", e)
        await asyncio.sleep(12)
