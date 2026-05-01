from __future__ import annotations


async def scanner_loop(bot) -> None:
    """
    Единственная публичная точка входа модуля.
    Фоновый MEME-sniper: скан, фильтры, GoPlus, Groq, рассылка в Telegram.
    Без Bot(), без Dispatcher, без start_polling.
    """
    import asyncio
    import json
    import logging
    import os
    from datetime import datetime, timezone
    from pathlib import Path
    from typing import Any

    import aiohttp

    from contract_checker import ContractSecurityChecker
    from scanner import (
        MemecoinScanner,
        SNIPER_LOOP_STATE,
        calculate_x1000_score,
        load_seen_tokens,
        save_seen_tokens,
        token_uid,
    )

    logger = logging.getLogger(__name__)
    scan_interval_sec = 120

    groq_key = os.getenv("GROQ_API_KEY", "")
    groq_url = "https://api.groq.com/openai/v1/chat/completions"
    users_file = Path(__file__).resolve().parent / "users.json"

    mem_scanner = MemecoinScanner()
    security_checker = ContractSecurityChecker()

    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def load_users() -> set[int]:
        if not users_file.exists():
            users_file.write_text("[]", encoding="utf-8")
        try:
            data = json.loads(users_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {int(x) for x in data if str(x).isdigit()}
        except Exception as exc:
            logger.error("Ошибка users.json: %s", exc)
        return set()

    def reset_counters_daily() -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if SNIPER_LOOP_STATE["day"] != today:
            SNIPER_LOOP_STATE["day"] = today
            SNIPER_LOOP_STATE["tokens_scanned_today"] = 0
            SNIPER_LOOP_STATE["signals_sent_today"] = 0

    def fib_levels(price: float) -> tuple[float, float, float]:
        price = max(price, 1e-10)
        high = price * 1.25
        low = price * 0.70
        diff = high - low
        e1 = high - 0.5 * diff
        e2 = high - 0.618 * diff
        return e1, e2, e1 * 0.85

    async def ai_text(token: dict[str, Any], score: int) -> str:
        if not groq_key:
            return (
                "Есть импульс по объёму и покупкам, но риск экстремально высокий. "
                "Вход только микропозицией с жёстким стопом."
            )
        prompt = (
            "Ты crypto analyst. Дай 2-3 предложения на русском: почему memecoin может пампить, "
            "главный риск, и входить или ждать.\n"
            f"Token: {token['symbol']} | chain: {token['chain']} | age_h: {token['age_hours']:.2f} | score: {score}"
        )
        body = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 180,
            "temperature": 0.3,
        }
        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
                async with session.post(groq_url, json=body, headers=headers) as resp:
                    if resp.status != 200:
                        return "Потенциал есть, но риски скама и дампа крайне высокие."
                    data = await resp.json()
                    return str(data["choices"][0]["message"]["content"]).strip()
        except Exception:
            return "Есть ранний импульс, но риск потери капитала очень высокий."

    def format_signal(token: dict[str, Any], security: dict[str, Any], ai_block: str) -> str:
        e1, e2, sl = fib_levels(safe_float(token.get("price"), 0.0))
        return f"""🚀 [MEME-SNIPER: СЕТАП ПОДТВЕРЖДЕН] ${token['symbol']}

🐸 MEME ALPHA (Свежий Листинг)
⚡ Статус: ✅ CONFLUENCE ACHIEVED
🕐 Возраст: {int(safe_float(token['age_hours']))} часов {int((safe_float(token['age_hours']) % 1) * 60)} минут
🌐 Сеть: {"ETHEREUM" if token['chain'] == 'ethereum' else 'SOLANA'}

📋 Контракт:
`{token['contract']}`
📊 [Открыть на DexScreener]({token['dex_url']})

💼 Экономика:
└ Капа (MC): ${safe_float(token['market_cap']):,.0f}
└ Ликвидность: ${safe_float(token['liquidity']):,.0f}
└ Объём 1ч: ${safe_float(token['volume_1h']):,.0f}
└ Vol/Liq: {safe_float(token['vol_liq_ratio']):.2f}x

📈 Давление покупок:
└ Покупок: {safe_int(token['buys_1h'])} | Продаж: {safe_int(token['sells_1h'])}
└ Тренд: {"🟢 БЫЧИЙ" if safe_int(token['buys_1h']) > safe_int(token['sells_1h']) else "🔴 МЕДВЕЖИЙ"}

🛡️ Безопасность:
└ Налог: BUY {safe_float(security['buy_tax']):.2f}% / SELL {safe_float(security['sell_tax']):.2f}%
└ Honeypot: {"✅ НЕТ" if not security['is_honeypot'] else "🚨 ДА"}
└ Холдеров: {safe_int(security['holder_count'])}

🤖 AI Анализ (Groq):
{ai_block}

🎯 ТОЧКИ ВХОДА:
└ Вход 1 (0.5 Fib): ${e1:.8f} — 30% позиции
└ Вход 2 (0.618 Fib): ${e2:.8f} — 70% позиции

🛑 СТОП-ЛОСС: ${sl:.8f} (-15% от входа 1)

🎯 ЦЕЛИ:
└ x2: ${e1 * 2:.8f}
└ x5: ${e1 * 5:.8f}
└ x10: ${e1 * 10:.8f}
└ x50: ${e1 * 50:.8f} (если нарратив взлетит)

⚠️ Потенциал: x10-x1000
⚠️ Риск: ОЧЕНЬ ВЫСОКИЙ. Входи только то что готов потерять полностью. DYOR."""

    SNIPER_LOOP_STATE["running"] = True
    seen_tokens = load_seen_tokens()

    while True:
        reset_counters_daily()
        SNIPER_LOOP_STATE["last_scan_time"] = datetime.now(timezone.utc).isoformat()
        try:
            raw = await mem_scanner.fetch_sources()
            candidates = mem_scanner.prefilter_memecoins(raw, seen_tokens)
            for token in candidates:
                uid = token_uid(token["chain"], token["contract"])
                if uid in seen_tokens:
                    continue
                seen_tokens.add(uid)
                save_seen_tokens(seen_tokens)
                SNIPER_LOOP_STATE["tokens_scanned_today"] += 1

                chain_id = "1" if token["chain"] == "ethereum" else "solana"
                security = await security_checker.check_contract_security(token["contract"], chain_id)
                if security["is_honeypot"]:
                    continue
                if safe_float(security["buy_tax"]) > mem_scanner.filters["max_buy_tax"] or safe_float(
                    security["sell_tax"]
                ) > mem_scanner.filters["max_sell_tax"]:
                    continue

                score = calculate_x1000_score(token, security)
                if score < mem_scanner.filters["min_score"]:
                    continue

                message = format_signal(token, security, await ai_text(token, score))
                for user_id in load_users():
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=message,
                            parse_mode="Markdown",
                            disable_web_page_preview=True,
                        )
                        SNIPER_LOOP_STATE["signals_sent_today"] += 1
                        SNIPER_LOOP_STATE["last_signal_time"] = datetime.now(timezone.utc).isoformat()
                        await asyncio.sleep(0.05)
                    except Exception as exc:
                        logger.error("Ошибка отправки %s: %s", user_id, exc)
                await asyncio.sleep(1.5)
        except Exception as exc:
            SNIPER_LOOP_STATE["last_error"] = str(exc)
            logger.error("Ошибка scanner_loop: %s", exc)

        await asyncio.sleep(scan_interval_sec)
