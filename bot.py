"""
Crypto Signal Bot — Telegram (aiogram 3.x)
Совместим с Python 3.14
"""

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from analysis import CryptoAnalyzer
from alerts import AlertManager

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8537036845:AAFSl7SgBnBtX9v5HIB_9DY6CImiKkyRcAk")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
analyzer = CryptoAnalyzer()
alert_manager = AlertManager()

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
        InlineKeyboardButton(text="🔔 Мои алерты", callback_data="menu_alerts"),
        InlineKeyboardButton(text="📈 Топ сигналы", callback_data="menu_signals")
    )
    builder.row(
        InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")
    )
    return builder.as_markup()

def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="menu_main"))
    return builder.as_markup()

# ─── COMMANDS ─────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start(message: Message):
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
    msg = await message.answer("🔍 Сканирую рынок...")
    result = await analyzer.market_scan()
    await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())

@dp.message(Command("alert"))
async def alert_command(message: Message):
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

# ─── TEXT HANDLER ─────────────────────────────────────────────────────────────

@dp.message(F.text)
async def text_handler(message: Message):
    text = message.text.strip().upper()
    if 2 <= len(text) <= 10 and text.isalpha():
        msg = await message.answer(f"⏳ Анализирую {text}...")
        result = await analyzer.full_analysis(text)
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())

# ─── CALLBACKS ────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "menu_main")
async def cb_main(query: CallbackQuery):
    await query.message.edit_text(
        "👋 *Crypto Signal Bot*\nВыбери действие:",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )
    await query.answer()

@dp.callback_query(F.data == "menu_analyze")
async def cb_analyze(query: CallbackQuery):
    await query.message.edit_text(
        "📊 *Анализ монеты*\n\nОтправь команду:\n`/analyze BTC`\n`/analyze ETH`\n`/analyze SOL`\n\n"
        "Или просто напиши символ монеты: `BTC`",
        parse_mode="Markdown",
        reply_markup=back_keyboard()
    )
    await query.answer()

@dp.callback_query(F.data == "menu_overbought")
async def cb_overbought(query: CallbackQuery):
    await query.message.edit_text("🔍 Ищу перекупленные монеты...")
    result = await analyzer.find_overbought()
    await query.message.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    await query.answer()

@dp.callback_query(F.data == "menu_new")
async def cb_new(query: CallbackQuery):
    await query.message.edit_text("🔍 Ищу новые монеты с потенциалом...")
    result = await analyzer.find_new_potential()
    await query.message.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    await query.answer()

@dp.callback_query(F.data == "menu_dump")
async def cb_dump(query: CallbackQuery):
    await query.message.edit_text("🔍 Ищу монеты под риском дампа...")
    result = await analyzer.find_dump_risk()
    await query.message.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    await query.answer()

@dp.callback_query(F.data == "menu_signals")
async def cb_signals(query: CallbackQuery):
    await query.message.edit_text("🔍 Генерирую топ сигналы...")
    result = await analyzer.top_signals()
    await query.message.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    await query.answer()

@dp.callback_query(F.data == "menu_alerts")
async def cb_alerts(query: CallbackQuery):
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
    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await query.answer()

@dp.callback_query(F.data == "menu_help")
async def cb_help(query: CallbackQuery):
    text = (
        "❓ *Команды бота*\n\n"
        "`/analyze BTC` — полный теханализ\n"
        "`/scan` — скан топ-50 рынка\n"
        "`/alert BTC 90000` — алерт выше цены\n"
        "`/alert ETH 2500 below` — алерт ниже цены\n"
        "`/alerts` — мои алерты\n"
        "`/delalert 1` — удалить алерт №1\n\n"
        "*Индикаторы:*\n"
        "• RSI — перекуплен/перепродан\n"
        "• MACD — тренд и импульс\n"
        "• Bollinger Bands — волатильность\n"
        "• EMA 20/50 — тренд\n\n"
        "*Сигналы:*\n"
        "🟢 ПОКУПАТЬ | 🟡 ЖДАТЬ | 🔴 ПРОДАВАТЬ"
    )
    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=back_keyboard())
    await query.answer()

# ─── ALERTS BACKGROUND TASK ───────────────────────────────────────────────────

async def check_alerts_loop():
    while True:
        try:
            triggered = await alert_manager.check_alerts(analyzer)
            for user_id, message in triggered:
                try:
                    await bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Alert send error for {user_id}: {e}")
        except Exception as e:
            logger.error(f"Alert check error: {e}")
        await asyncio.sleep(60)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    logger.info("Bot started. Polling...")
    asyncio.create_task(check_alerts_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())