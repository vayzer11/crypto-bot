"""
sniper.py
Отдельный Telegram crypto sniper bot (aiogram 3.x, Python 3.14).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

from contract_checker import ContractSecurityChecker
from scanner import DEFAULT_FILTERS, TokenScanner, load_seen_tokens, save_seen_tokens

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"

BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"

dp = Dispatcher()
scanner = TokenScanner(filters=DEFAULT_FILTERS)
checker = ContractSecurityChecker()

SCANNER_STATE: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "scan_cycles": 0,
    "tokens_scanned": 0,
    "signals_sent": 0,
    "last_scan_at": None,
    "last_signal_at": None,
    "last_error": None,
}


def ensure_users_file() -> None:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")


def load_users() -> set[int]:
    ensure_users_file()
    try:
        raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {int(item) for item in raw if str(item).isdigit()}
    except Exception as exc:
        logger.error("Не удалось загрузить users.json: %s", exc)
    return set()


def save_users(users: set[int]) -> None:
    try:
        USERS_FILE.write_text(json.dumps(sorted(users), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("Не удалось сохранить users.json: %s", exc)


def subscribe_user(user_id: int) -> bool:
    users = load_users()
    if user_id in users:
        return False
    users.add(user_id)
    save_users(users)
    return True


def unsubscribe_user(user_id: int) -> bool:
    users = load_users()
    if user_id not in users:
        return False
    users.remove(user_id)
    save_users(users)
    return True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _escape_md(text: str) -> str:
    # Для parse_mode="Markdown" экранируем только критичные символы.
    specials = "\\`*_[]()"
    escaped = ""
    for char in text:
        escaped += f"\\{char}" if char in specials else char
    return escaped


def _format_ts(iso_ts: Optional[str]) -> str:
    if not iso_ts:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.astimezone().strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return str(iso_ts)


def _guess_chain_and_chain_id(contract: str, maybe_chain: Optional[str] = None) -> tuple[str, str]:
    if maybe_chain:
        chain = maybe_chain.lower()
        if chain in {"eth", "ethereum"}:
            return "ethereum", "1"
        if chain in {"sol", "solana"}:
            return "solana", "solana"
    # Простая эвристика:
    if contract.lower().startswith("0x") and len(contract) == 42:
        return "ethereum", "1"
    return "solana", "solana"


def calculate_fibonacci_entries(high: float, low: float, current_price: float) -> dict[str, Any]:
    if high <= 0 or low <= 0:
        high = max(current_price, 1e-8)
        low = max(current_price * 0.85, 1e-8)
    if high < low:
        high, low = low, high

    diff = max(high - low, high * 0.02)
    fib_levels = {
        "0.0": high,
        "0.236": high - 0.236 * diff,
        "0.382": high - 0.382 * diff,
        "0.500": high - 0.500 * diff,
        "0.618": high - 0.618 * diff,
        "0.786": high - 0.786 * diff,
        "1.0": low,
    }

    entry_zones: list[dict[str, Any]] = []
    for level, price in fib_levels.items():
        if price < current_price:
            allocation = "20%"
            if level == "0.500":
                allocation = "30%"
            elif level == "0.618":
                allocation = "70%"
            entry_zones.append({"level": level, "price": price, "allocation": allocation})

    stop_loss = fib_levels["0.786"] * 0.95
    targets = {
        "TP1 (1.272)": low + 1.272 * diff,
        "TP2 (1.618)": low + 1.618 * diff,
        "TP3 (2.618)": low + 2.618 * diff,
    }

    return {
        "levels": fib_levels,
        "entry_zones": entry_zones[:2],
        "stop_loss": stop_loss,
        "targets": targets,
    }


async def ai_analyze_token(token_data: dict[str, Any], security: dict[str, Any]) -> str:
    if not GROQ_API_KEY:
        return "Ключ GROQ_API_KEY не задан. AI-анализ недоступен, ориентируйся на метрики и риск-флаги."

    prompt = f"""Ты профессиональный крипто-трейдер и аналитик DeFi.

Проанализируй новый токен и дай торговую рекомендацию на русском языке.

ДАННЫЕ ТОКЕНА:
- Название: {token_data['name']} ({token_data['symbol']})
- Сеть: {token_data['chain']}
- Контракт: {token_data['contract']}
- Возраст: {token_data['age_hours']:.2f} часов
- Цена: ${token_data['price']}
- Капитализация: ${token_data['market_cap']:,.0f}
- Ликвидность: ${token_data['liquidity']:,.0f}
- Объём 24ч: ${token_data['volume_24h']:,.0f}
- Объём/Ликвидность: {token_data['vol_liq_ratio']:.1f}x
- Изменение цены 1ч: {token_data['price_change_1h']}%
- Изменение цены 24ч: {token_data['price_change_24h']}%
- Транзакций 24ч: {token_data['txns_24h']}
- Покупок/Продаж: {token_data['buys_24h']}/{token_data['sells_24h']}

БЕЗОПАСНОСТЬ:
- Оценка безопасности: {security['safety_score']}/100
- Вердикт: {security['verdict']}
- Налог покупки/продажи: {security['buy_tax']}%/{security['sell_tax']}%
- Холдеров: {security['holder_count']}
- Ликвидность заблокирована: {security['lp_locked']}
- Красные флаги: {', '.join(security['red_flags']) if security['red_flags'] else 'Нет'}

Дай анализ в формате:
1. НАРРАТИВ (1-2 предложения)
2. ОЦЕНКА СЕТАПА: CONFLUENCE ACHIEVED / WEAK SETUP / AVOID
3. РИСКИ (1-2 главных риска)
4. РЕКОМЕНДАЦИЯ: ВХОДИТЬ / ЖДАТЬ / ИЗБЕГАТЬ

Будь честным. Если токен выглядит как скам — скажи прямо."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.3,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(GROQ_API, json=body, headers=headers) as response:
                if response.status != 200:
                    logger.warning("Groq HTTP %s", response.status)
                    return "AI-сервис временно недоступен. Следуй риск-менеджменту и проверяй ончейн метрики."
                payload = await response.json()
                text = payload["choices"][0]["message"]["content"]
                return text.strip() if text else "AI-анализ пустой, принимай решение по метрикам."
    except Exception as exc:
        logger.error("Ошибка Groq анализа: %s", exc)
        return "Ошибка AI-анализа. Используй базовые метрики ликвидности, объёма и безопасности."


def format_signal_message(token: dict[str, Any], security: dict[str, Any], fib: dict[str, Any], ai_analysis: str) -> str:
    chain_emoji = "🔷" if token["chain"] == "ethereum" else "🟣"
    status = (
        "✅ CONFLUENCE ACHIEVED"
        if security["safety_score"] >= 70
        else "⚠️ WEAK SETUP"
        if security["safety_score"] >= 50
        else "🔴 ИЗБЕГАЙ"
    )

    msg = f"""🎯 *[{token['chain'].upper()}-SNIPER: СЕТАП ПОДТВЕРЖДЕН]* ${token['symbol']}

{chain_emoji} *NEW ALPHA* (Свежий Листинг)
⚡ Статус: {status}
🕐 Возраст: {token['age_hours']:.1f} часов

📋 *Контракт ({token['chain'].upper()}):*
`{token['contract']}`
📊 Аналитика: [Открыть график](https://dexscreener.com/{token['chain']}/{token['contract']})

💼 *Экономика сделки (Sizing):*
Капитализация (MC): ${token['market_cap']:,.0f}
Ликвидность (TVL): ${token['liquidity']:,.0f}
Объём 24ч: ${token['volume_24h']:,.0f} ({token['vol_liq_ratio']:.1f}x liq)

🛡️ *Ончейн Щит (Anti-Bundling):*
└ Налоги: {security['buy_tax']}% / {security['sell_tax']}%
└ Холдеров: {security['holder_count']}
└ Анти-банд: {'Распределение в норме' if security['owner_percent'] < 20 else f"⚠️ Девелопер {security['owner_percent']}%"}
└ LP {'заблокирована ✅' if security['lp_locked'] else 'НЕ заблокирована ⚠️'}

🤖 *Нарратив:* {_escape_md(ai_analysis)}

📈 *MSB ПОДТВЕРЖДЕН* (Тренд: {'↗️ Бычий' if token['price_change_1h'] > 0 else '↘️ Медвежий'})
└ Пик: ${token['price_high_24h']:.8f}
└ Дно: ${token['price_low_24h']:.8f}

🎯 *ENTRY ZONE (Лимитные покупки):*"""

    for i, zone in enumerate(fib["entry_zones"][:2], 1):
        msg += f"\n└ Вход {i} ({zone['level']}): ${zone['price']:.8f} ({zone['allocation']})"

    msg += f"""

🚫 *INVALIDATION (Стоп-лосс):*
└ Закрытие свечи ниже ${fib['stop_loss']:.8f}

🎯 *Цели:*"""

    for name, price in list(fib["targets"].items())[:3]:
        msg += f"\n└ {name}: ${price:.8f}"

    if security["red_flags"]:
        msg += "\n\n🚨 *Красные флаги:*"
        for flag in security["red_flags"]:
            msg += f"\n└ {_escape_md(flag)}"

    msg += "\n\n⚠️ _Это не финансовый совет. DYOR. Входи только то что готов потерять._"
    return msg


async def scanner_loop(bot: Bot) -> None:
    logger.info("Фоновый сканер запущен")
    seen_tokens = load_seen_tokens()

    while True:
        SCANNER_STATE["scan_cycles"] += 1
        SCANNER_STATE["last_scan_at"] = datetime.now(timezone.utc).isoformat()
        try:
            all_tokens = await scanner.get_candidates(seen_tokens)
            logger.info("Найдено кандидатов: %s", len(all_tokens))

            for token in all_tokens:
                contract = str(token.get("contract", "")).lower()
                chain = str(token.get("chain", "")).lower()
                uid = f"{chain}:{contract}"
                if uid in seen_tokens:
                    continue

                seen_tokens.add(uid)
                save_seen_tokens(seen_tokens)
                SCANNER_STATE["tokens_scanned"] += 1

                chain_id = "1" if chain == "ethereum" else "solana"
                security = await checker.check_contract_security(contract, chain_id)

                if security["safety_score"] < scanner.filters["min_safety_score"]:
                    logger.info("%s пропущен: низкий safety_score=%s", token["symbol"], security["safety_score"])
                    continue
                if scanner.filters["exclude_honeypots"] and security["is_honeypot"]:
                    logger.info("%s пропущен: honeypot", token["symbol"])
                    continue
                if security["holder_count"] < scanner.filters["min_holders"]:
                    logger.info("%s пропущен: holder_count=%s", token["symbol"], security["holder_count"])
                    continue
                if security["buy_tax"] > scanner.filters["max_buy_tax"] or security["sell_tax"] > scanner.filters["max_sell_tax"]:
                    logger.info(
                        "%s пропущен: налоги buy/sell=%s/%s",
                        token["symbol"],
                        security["buy_tax"],
                        security["sell_tax"],
                    )
                    continue

                fib = calculate_fibonacci_entries(
                    high=_safe_float(token.get("price_high_24h"), _safe_float(token.get("price"), 0.0)),
                    low=_safe_float(token.get("price_low_24h"), _safe_float(token.get("price"), 0.0) * 0.9),
                    current_price=_safe_float(token.get("price"), 0.0),
                )

                ai_text = await ai_analyze_token(token, security)
                message = format_signal_message(token, security, fib, ai_text)

                users = load_users()
                if not users:
                    logger.info("Подписчиков нет, сигнал не отправлен")
                    continue

                for user_id in users:
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=message,
                            parse_mode="Markdown",
                            disable_web_page_preview=True,
                        )
                        SCANNER_STATE["signals_sent"] += 1
                        SCANNER_STATE["last_signal_at"] = datetime.now(timezone.utc).isoformat()
                        await asyncio.sleep(0.05)
                    except Exception as exc:
                        logger.error("Ошибка отправки в %s: %s", user_id, exc)

                await asyncio.sleep(2)

        except Exception as exc:
            logger.error("Scanner error: %s", exc)
            SCANNER_STATE["last_error"] = str(exc)

        await asyncio.sleep(60)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    try:
        user_id = message.from_user.id
        added = subscribe_user(user_id)
        text = (
            "🚀 *Sniper Bot активирован*\n\n"
            "Ты подписан на сигналы по новым листингам ETH/Solana.\n"
            "Сканирование идёт каждые 60 секунд.\n\n"
            "Команды:\n"
            "`/status` — статус сканера\n"
            "`/filters` — текущие фильтры\n"
            "`/check <адрес> [eth|sol]` — ручная проверка контракта\n"
            "`/stop` — отписаться"
        )
        if not added:
            text = "✅ Ты уже подписан на sniper-сигналы.\n\n" + text
        await message.answer(text, parse_mode="Markdown")
    except Exception as exc:
        logger.error("Ошибка /start: %s", exc)
        await message.answer("❌ Ошибка подписки. Попробуй позже.")


@dp.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    try:
        removed = unsubscribe_user(message.from_user.id)
        if removed:
            await message.answer("🛑 Ты отписан от sniper-сигналов.")
        else:
            await message.answer("ℹ️ Ты и так не был подписан.")
    except Exception as exc:
        logger.error("Ошибка /stop: %s", exc)
        await message.answer("❌ Ошибка отписки.")


@dp.message(Command("filters"))
async def cmd_filters(message: Message) -> None:
    try:
        lines = [
            "⚙️ *Текущие фильтры sniper:*",
            f"• Min liquidity: ${scanner.filters['min_liquidity']:,}",
            f"• Max liquidity: ${scanner.filters['max_liquidity']:,}",
            f"• Min volume 24h: ${scanner.filters['min_volume_24h']:,}",
            f"• Max age: {scanner.filters['max_age_hours']}ч",
            f"• Min safety score: {scanner.filters['min_safety_score']}",
            f"• Min holders: {scanner.filters['min_holders']}",
            f"• Max buy/sell tax: {scanner.filters['max_buy_tax']}%/{scanner.filters['max_sell_tax']}%",
            f"• Chains: {', '.join(scanner.filters['chains'])}",
            f"• Exclude honeypots: {'да' if scanner.filters['exclude_honeypots'] else 'нет'}",
        ]
        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        logger.error("Ошибка /filters: %s", exc)
        await message.answer("❌ Не удалось показать фильтры.")


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    try:
        users_count = len(load_users())
        seen_count = len(load_seen_tokens())
        started = _format_ts(SCANNER_STATE.get("started_at"))
        last_scan = _format_ts(SCANNER_STATE.get("last_scan_at"))
        last_signal = _format_ts(SCANNER_STATE.get("last_signal_at"))
        last_error = SCANNER_STATE.get("last_error") or "нет"

        text = (
            "📡 *Статус sniper-сканера*\n\n"
            f"• Запущен: {started}\n"
            f"• Циклов сканирования: {SCANNER_STATE['scan_cycles']}\n"
            f"• Токенов проверено: {SCANNER_STATE['tokens_scanned']}\n"
            f"• Сигналов отправлено: {SCANNER_STATE['signals_sent']}\n"
            f"• Последний scan: {last_scan}\n"
            f"• Последний сигнал: {last_signal}\n"
            f"• Подписчиков: {users_count}\n"
            f"• Seen contracts: {seen_count}\n"
            f"• Последняя ошибка: `{_escape_md(last_error[:180])}`"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as exc:
        logger.error("Ошибка /status: %s", exc)
        await message.answer("❌ Ошибка получения статуса.")


@dp.message(Command("check"))
async def cmd_check(message: Message) -> None:
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer(
                "Использование:\n`/check 0x... eth`\n`/check So111... sol`",
                parse_mode="Markdown",
            )
            return

        contract = parts[1].strip()
        chain_hint = parts[2].strip() if len(parts) >= 3 else None
        chain, chain_id = _guess_chain_and_chain_id(contract, chain_hint)

        msg = await message.answer("🔍 Проверяю контракт через GoPlus...")
        sec = await checker.check_contract_security(contract, chain_id)

        flags = "\n".join([f"• {flag}" for flag in sec["red_flags"] + sec["yellow_flags"]]) or "• Флаги не обнаружены"
        report = (
            f"🛡️ *Проверка контракта ({chain.upper()})*\n"
            f"`{contract}`\n\n"
            f"• Safety Score: *{sec['safety_score']}/100*\n"
            f"• Вердикт: *{sec['verdict']}*\n"
            f"• Honeypot: {'ДА' if sec['is_honeypot'] else 'нет'}\n"
            f"• Buy/Sell tax: {sec['buy_tax']}% / {sec['sell_tax']}%\n"
            f"• Mintable: {'да' if sec['is_mintable'] else 'нет'}\n"
            f"• Proxy: {'да' if sec['is_proxy'] else 'нет'}\n"
            f"• Holders: {sec['holder_count']}\n"
            f"• LP lock: {'да' if sec['lp_locked'] else 'нет'} ({sec['lp_lock_percent']}%)\n"
            f"• Owner %: {sec['owner_percent']}%\n"
            f"• Open source: {'да' if sec['is_open_source'] else 'нет'}\n\n"
            f"*Флаги:*\n{_escape_md(flags)}"
        )
        await msg.edit_text(report, parse_mode="Markdown")
    except Exception as exc:
        logger.error("Ошибка /check: %s", exc)
        await message.answer("❌ Ошибка проверки контракта.")


@dp.message()
async def fallback_handler(message: Message) -> None:
    await message.answer(
        "Команда не распознана.\nИспользуй: `/start`, `/stop`, `/status`, `/filters`, `/check`",
        parse_mode="Markdown",
    )


async def main() -> None:
    ensure_users_file()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в переменных окружения.")

    bot = Bot(token=BOT_TOKEN)
    logger.info("Sniper bot запущен")
    asyncio.create_task(scanner_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Sniper bot остановлен вручную")
    except Exception as exc:
        logger.error("Критическая ошибка sniper bot: %s", exc)
        raise SystemExit(1)
