"""
Crypto Signal Bot — Telegram (aiogram 3.x)
Совместим с Python 3.14
"""

import asyncio
import logging
import os
import json
from pathlib import Path
from typing import Optional
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from analysis import CryptoAnalyzer
from alerts import AlertManager
from sniper import scanner_loop, get_sniper_status_text

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "8537036845:AAFSl7SgBnBtX9v5HIB_9DY6CImiKkyRcAk").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
USERS_FILE = Path(__file__).resolve().parent / "users.json"
ENTRY_WAITING_USERS: set[int] = set()

bot: Optional[Bot] = None
dp = Dispatcher()
analyzer = CryptoAnalyzer()
alert_manager = AlertManager()


def ensure_users_file() -> None:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")


def load_users() -> set[int]:
    ensure_users_file()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data if isinstance(x, int) or str(x).isdigit()}
    except Exception:
        return set()


def save_user_id(user_id: int) -> None:
    users = load_users()
    users.add(user_id)
    USERS_FILE.write_text(json.dumps(sorted(users), ensure_ascii=False), encoding="utf-8")

# ─── KEYBOARDS ────────────────────────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Анализ монеты", callback_data="menu_analyze"),
        InlineKeyboardButton(text="🔥 Перекупленные", callback_data="menu_overbought")
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Новые с потенциалом", callback_data="menu_new"),
        InlineKeyboardButton(text="⚠️ Риск падения", callback_data="menu_dump")
    )
    builder.row(
        InlineKeyboardButton(text="🆕 Новые монеты", callback_data="menu_new_coins"),
        InlineKeyboardButton(text="💎 Gem Finder", callback_data="menu_gems")
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Точка входа", callback_data="menu_entry"),
        InlineKeyboardButton(text="📈 Топ сигналы", callback_data="menu_signals")
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Мои алерты", callback_data="menu_alerts"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")
    )
    builder.row(
        InlineKeyboardButton(
            text="📱 Открыть приложение",
            web_app=WebAppInfo(url="https://vayzer11.github.io/crypto-bot/")
        )
    )
    return builder.as_markup()

def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main"))
    return builder.as_markup()

# ─── COMMANDS ─────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start(message: Message):
    save_user_id(message.from_user.id)
    text = (
        "👋 *Crypto Signal Bot* запущен!\n\n"
        "Я анализирую рынок в реальном времени и нахожу:\n"
        "• Точки входа и выхода\n"
        "• Перекупленные монеты (RSI > 70)\n"
        "• Новые монеты с крупным капиталом\n"
        "• Монеты под риском дампа\n\n"
        "Выбери действие:"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_keyboard())

@dp.message(Command(commands=["analyze", "a"]))
async def analyze_command(message: Message):
    save_user_id(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "📊 Укажи символ монеты:\n`/analyze BTC`\n`/analyze ETH`\n`/analyze SOL`",
            parse_mode="Markdown"
        )
        return
    symbol = parts[1].upper()
    msg = await message.answer(f"⏳ Анализирую {symbol}...")
    result = await analyzer.full_analysis(symbol)
    await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())

@dp.message(Command("scan"))
async def scan_command(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("🔍 Сканирую рынок...")
    result = await analyzer.market_scan()
    await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())

@dp.message(Command("alert"))
async def alert_command(message: Message):
    save_user_id(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer(
            "🔔 Добавить алерт:\n`/alert BTC 90000`\n`/alert ETH 2500 below`",
            parse_mode="Markdown"
        )
        return
    symbol = parts[1].upper()
    try:
        price = float(parts[2])
    except ValueError:
        await message.answer("❌ Неверная цена")
        return
    direction = "below" if len(parts) > 3 and parts[3].lower() == "below" else "above"
    user_id = message.from_user.id
    alert_manager.add_alert(user_id, symbol, price, direction)
    dir_text = "≤" if direction == "below" else "≥"
    await message.answer(
        f"✅ Алерт добавлен!\n*{symbol}* {dir_text} ${price:,.2f}",
        parse_mode="Markdown"
    )

@dp.message(Command("alerts"))
async def alerts_command(message: Message):
    save_user_id(message.from_user.id)
    user_id = message.from_user.id
    alerts = alert_manager.get_user_alerts(user_id)
    if not alerts:
        await message.answer("Алертов нет. Добавить: `/alert BTC 90000`", parse_mode="Markdown")
        return
    lines = ["🔔 *Мои алерты*\n"]
    for i, a in enumerate(alerts, 1):
        dir_sym = "≤" if a["direction"] == "below" else "≥"
        lines.append(f"{i}. *{a['symbol']}* {dir_sym} ${a['price']:,.2f}")
    await message.answer("\n".join(lines), parse_mode="Markdown")

@dp.message(Command("delalert"))
async def delalert_command(message: Message):
    save_user_id(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: `/delalert 1`", parse_mode="Markdown")
        return
    try:
        idx = int(parts[1]) - 1
    except ValueError:
        await message.answer("❌ Неверный номер")
        return
    user_id = message.from_user.id
    if alert_manager.remove_alert(user_id, idx):
        await message.answer("✅ Алерт удалён")
    else:
        await message.answer("❌ Алерт не найден")


@dp.message(Command("sniper"))
async def sniper_status_command(message: Message):
    save_user_id(message.from_user.id)
    await message.answer(get_sniper_status_text(), parse_mode="Markdown")

# ─── TEXT HANDLER ─────────────────────────────────────────────────────────────

@dp.message(F.text)
async def text_handler(message: Message):
    save_user_id(message.from_user.id)
    text = message.text.strip().upper()
    if message.from_user.id in ENTRY_WAITING_USERS:
        ENTRY_WAITING_USERS.discard(message.from_user.id)
        msg = await message.answer(f"⏳ Ищу точку входа для {text}...")
        result = await analyzer.detect_entry_signal(text)
        if result.get("error"):
            await msg.edit_text(f"❌ {result['error']}", reply_markup=back_keyboard())
            return
        patterns = result.get("patterns", [])
        if not patterns:
            body = f"⚡ *Точка входа: {text}*\n\nСильных паттернов пока нет.\nЦена: {result.get('price', 0):.6f}"
        else:
            lines = [f"⚡ *Точка входа: {text}*\n", f"Цена: `{result.get('price', 0):.6f}` | 24ч: `{result.get('change_24h', 0):+.2f}%`\n", "*Найденные паттерны:*"]
            for p in patterns:
                lines.append(f"• `{p['type']}` — {p['name']} ({p['strength']})")
            body = "\n".join(lines)
        await msg.edit_text(body, parse_mode="Markdown", reply_markup=back_keyboard())
        return
    if 2 <= len(text) <= 10 and text.isalpha():
        msg = await message.answer(f"⏳ Анализирую {text}...")
        result = await analyzer.full_analysis(text)
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())

# ─── CALLBACKS ────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "menu_main")
async def cb_main(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer(
        "👋 *Crypto Signal Bot*\nВыбери действие:",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "menu_analyze")
async def cb_analyze(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer(
        "📊 *Анализ монеты*\n\nПросто напиши символ монеты:\n`BTC` `ETH` `SOL` `RENDER` `TAO`\n\n"
        "Или команда: `/analyze BTC`",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )

@dp.callback_query(F.data == "menu_overbought")
async def cb_overbought(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("Загружаю данные...")
    msg = await query.message.answer("🔍 Ищу перекупленные монеты (RSI > 68)...")
    try:
        result = await analyzer.find_overbought()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_new")
async def cb_new(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("Загружаю данные...")
    msg = await query.message.answer("🔍 Ищу монеты с потенциалом...")
    try:
        result = await analyzer.find_new_potential()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_dump")
async def cb_dump(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("Загружаю данные...")
    msg = await query.message.answer("🔍 Ищу монеты под риском дампа...")
    try:
        result = await analyzer.find_dump_risk()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_new_coins")
async def cb_new_coins(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("Сканирую рынок...")
    msg = await query.message.answer("🆕 Ищу новые монеты, листинги и тренды...")
    try:
        result = await analyzer.find_new_coins()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_gems")
async def cb_gems(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("Ищу gems...")
    msg = await query.message.answer("💎 Запускаю Gem Finder...")
    try:
        result = await analyzer.find_gems()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_entry")
async def cb_entry_menu(query: CallbackQuery):
    save_user_id(query.from_user.id)
    ENTRY_WAITING_USERS.add(query.from_user.id)
    await query.answer()
    await query.message.answer(
        "⚡ Введи символ монеты для поиска точки входа.\nПример: `BTC` или `SOL`",
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )

@dp.callback_query(F.data == "menu_signals")
async def cb_signals(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("Загружаю данные...")
    msg = await query.message.answer("🔍 Генерирую топ сигналы...")
    try:
        result = await analyzer.top_signals()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_alerts")
async def cb_alerts(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    user_id = query.from_user.id
    alerts = alert_manager.get_user_alerts(user_id)
    if not alerts:
        text = "🔔 *Мои алерты*\n\nАлертов нет.\n\nДобавить: `/alert BTC 90000`"
    else:
        lines = ["🔔 *Мои алерты*\n"]
        for i, a in enumerate(alerts, 1):
            dir_sym = "≤" if a["direction"] == "below" else "≥"
            lines.append(f"{i}. *{a['symbol']}* {dir_sym} ${a['price']:,.2f}")
        lines.append("\nУдалить: `/delalert 1`")
        text = "\n".join(lines)
    await query.message.answer(text, parse_mode="Markdown", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_help")
async def cb_help(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    text = (
        "❓ *Команды бота*\n\n"
        "`/analyze BTC` — полный теханализ\n"
        "`/scan` — скан топ-50 рынка\n"
        "`/alert BTC 90000` — алерт выше цены\n"
        "`/alert ETH 2500 below` — алерт ниже цены\n"
        "`/alerts` — мои алерты\n"
        "`/delalert 1` — удалить алерт №1\n\n"
        "`/entry BTC` — детектор точки входа\n\n"
        "Или просто напиши символ: `BTC` `ETH` `SOL`\n\n"
        "*Индикаторы:*\n"
        "• RSI — перекуплен/перепродан\n"
        "• MACD — тренд и импульс\n"
        "• Bollinger Bands — волатильность\n"
        "• EMA 20/50 — тренд\n"
        "• Fear and Greed Index — настроение рынка\n"
        "• TVL — ликвидность протокола\n\n"
        "*Сигналы:*\n"
        "🟢 ПОКУПАТЬ | 🟡 ЖДАТЬ | 🔴 ПРОДАВАТЬ"
    )
    await query.message.answer(text, parse_mode="Markdown", reply_markup=back_keyboard())

@dp.message(Command("entry"))
async def entry_command(message: Message):
    save_user_id(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 2:
        ENTRY_WAITING_USERS.add(message.from_user.id)
        await message.answer("⚡ Укажи символ: `/entry BTC` или просто отправь символ следующим сообщением.", parse_mode="Markdown")
        return
    symbol = parts[1].upper()
    msg = await message.answer(f"⏳ Анализирую точку входа для {symbol}...")
    result = await analyzer.detect_entry_signal(symbol)
    if result.get("error"):
        await msg.edit_text(f"❌ {result['error']}", reply_markup=back_keyboard())
        return
    patterns = result.get("patterns", [])
    if not patterns:
        await msg.edit_text(
            f"⚡ *{symbol}*: сильных входных паттернов пока нет.\nЦена: `{result.get('price', 0):.6f}`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
        return
    lines = [f"⚡ *Точка входа: {symbol}*", f"Цена: `{result.get('price', 0):.6f}` | 24ч: `{result.get('change_24h', 0):+.2f}%`", "", "*Паттерны:*"]
    for p in patterns:
        lines.append(f"• `{p['type']}` — {p['name']} ({p['strength']})")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=back_keyboard())

# ─── ALERTS BACKGROUND TASK ───────────────────────────────────────────────────

async def check_alerts_loop(bot_instance: Bot):
    while True:
        try:
            triggered = await alert_manager.check_alerts(analyzer)
            for user_id, message in triggered:
                try:
                    await bot_instance.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Alert send error for {user_id}: {e}")
        except Exception as e:
            logger.error(f"Alert check error: {e}")
        await asyncio.sleep(60)

async def auto_broadcast_loop(bot_instance: Bot) -> None:
    """Каждые 6 часов: топ-3 сигнала на покупку + 2 гема всем пользователям."""
    await asyncio.sleep(120)
    while True:
        try:
            text = await analyzer.auto_broadcast_message()
            if not text:
                await asyncio.sleep(21_600)
                continue
            for user_id in load_users():
                try:
                    await bot_instance.send_message(user_id, text, parse_mode="Markdown")
                except Exception as exc:
                    logger.error("auto_broadcast send %s: %s", user_id, exc)
        except Exception as exc:
            logger.error("auto_broadcast_loop: %s", exc)
        await asyncio.sleep(21_600)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    ensure_users_file()
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY не задан — блок Claude (если используется) недоступен.")
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY не задан — AI-анализ Groq будет с заглушками.")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Укажи токен в переменных окружения Railway или терминала.")

    global bot
    try:
        bot = Bot(token=BOT_TOKEN)
    except Exception as exc:
        raise RuntimeError("BOT_TOKEN невалидный. Получи новый токен у BotFather и обнови переменную окружения.") from exc
    logger.info("Bot started. Polling...")
    asyncio.create_task(check_alerts_loop(bot))
    asyncio.create_task(auto_broadcast_loop(bot))
    asyncio.create_task(scanner_loop(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        logger.error(str(exc))
        raise SystemExit(1)
