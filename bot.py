"""
Telegram bot + lightweight HTTP API for the web app.

Railway-friendly behavior:
- if PORT/WEB_PORT is set, starts an aiohttp server for the mini app and JSON API
- if BOT_TOKEN is set, starts Telegram polling
- if BOT_TOKEN is missing, serves the web app only
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from alerts import AlertManager
from analysis import CryptoAnalyzer

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent
BOT_TOKEN = os.getenv("BOT_TOKEN")
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN")
DEFAULT_WEB_APP_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}" if RAILWAY_PUBLIC_DOMAIN else "https://vayzer11.github.io/crypto-bot/"
WEB_APP_URL = os.getenv("WEB_APP_URL", DEFAULT_WEB_APP_URL)
WEB_PORT = int(os.getenv("PORT") or os.getenv("WEB_PORT") or (8000 if not BOT_TOKEN else 0))

bot: Optional[Bot] = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()
analyzer = CryptoAnalyzer()
alert_manager = AlertManager()


def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Анализ", callback_data="menu_analyze"),
        InlineKeyboardButton(text="🧰 Скринер", callback_data="menu_screener"),
    )
    builder.row(
        InlineKeyboardButton(text="📰 Новости", callback_data="menu_news"),
        InlineKeyboardButton(text="📈 Топ сигналы", callback_data="menu_signals"),
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Fresh cap", callback_data="menu_fresh"),
        InlineKeyboardButton(text="🧨 Large-cap risk", callback_data="menu_listing_risk"),
    )
    builder.row(
        InlineKeyboardButton(text="🔥 Перекупленные", callback_data="menu_overbought"),
        InlineKeyboardButton(text="⚠️ Риск дампа", callback_data="menu_dump"),
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Мои алерты", callback_data="menu_alerts"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help"),
    )
    builder.row(
        InlineKeyboardButton(text="📱 Открыть приложение", web_app=WebAppInfo(url=WEB_APP_URL))
    )
    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_main"))
    return builder.as_markup()


def parse_screener_args(parts: list[str]) -> dict[str, str]:
    aliases = {
        "mincap": "cap_min",
        "maxcap": "cap_max",
        "minvol": "vol_ratio_min",
        "maxvol": "vol_ratio_max",
        "minrsi": "rsi_min",
        "maxrsi": "rsi_max",
        "min24": "change_24h_min",
        "max24": "change_24h_max",
        "min7": "change_7d_min",
        "max7": "change_7d_max",
        "riskmin": "risk_min",
        "riskmax": "risk_max",
        "freshmin": "fresh_min",
        "freshmax": "fresh_max",
    }
    filters: dict[str, str] = {}
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        if "=" not in part:
            filters["preset"] = part.lower()
            continue
        key, value = part.split("=", 1)
        normalized_key = aliases.get(key.lower(), key.lower())
        filters[normalized_key] = value
    return filters


async def run_report(message: Message, title: str, coro) -> None:
    status = await message.answer(title)
    try:
        result = await coro
        await status.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as exc:
        logger.exception("Report error: %s", exc)
        await status.edit_text(
            f"❌ Ошибка: `{str(exc)[:180]}`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )


async def run_callback_report(query: CallbackQuery, title: str, coro) -> None:
    await query.answer("Загружаю...")
    if not query.message:
        return
    await run_report(query.message, title, coro)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    text = (
        "👋 *Crypto Signal Bot* запущен.\n\n"
        "Что умею сейчас:\n"
        "• полный анализ монеты\n"
        "• новостная крипто-лента\n"
        "• скринер по фильтрам\n"
        "• поиск fresh-cap монет\n"
        "• поиск large-cap монет с риском распределения\n"
        "• сигналы, перекупленность и дамп-риск\n\n"
        "Выбери действие в меню или просто отправь тикер: `BTC`, `ETH`, `SOL`."
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=main_keyboard())


@dp.message(Command(commands=["analyze", "a"]))
async def analyze_command(message: Message) -> None:
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "📊 Укажи тикер.\n\nПримеры:\n`/analyze BTC`\n`/analyze ETH`\n`/analyze SOL`",
            parse_mode="Markdown",
        )
        return
    symbol = parts[1].upper()
    await run_report(message, f"⏳ Анализирую {symbol}...", analyzer.full_analysis(symbol))


@dp.message(Command("scan"))
async def scan_command(message: Message) -> None:
    await run_report(message, "🔎 Сканирую рынок...", analyzer.market_scan())


@dp.message(Command("news"))
async def news_command(message: Message) -> None:
    await run_report(message, "📰 Собираю новости...", analyzer.latest_news())


@dp.message(Command("fresh"))
async def fresh_command(message: Message) -> None:
    await run_report(message, "🚀 Ищу fresh-cap монеты...", analyzer.find_new_potential())


@dp.message(Command("risk"))
async def risk_command(message: Message) -> None:
    await run_report(message, "🧨 Ищу fresh large-cap под раздачу...", analyzer.find_listing_risk())


@dp.message(Command("dump"))
async def dump_command(message: Message) -> None:
    await run_report(message, "⚠️ Сканирую дамп-риск...", analyzer.find_dump_risk())


@dp.message(Command("overbought"))
async def overbought_command(message: Message) -> None:
    await run_report(message, "🔥 Ищу перегретые монеты...", analyzer.find_overbought())


@dp.message(Command("signals"))
async def signals_command(message: Message) -> None:
    await run_report(message, "📈 Собираю лучшие сигналы...", analyzer.top_signals())


@dp.message(Command("screener"))
async def screener_command(message: Message) -> None:
    parts = message.text.split()[1:]
    filters = parse_screener_args(parts)
    if not filters:
        await message.answer(
            "🧰 Скринер работает по preset и фильтрам.\n\n"
            "Примеры:\n"
            "`/screener fresh`\n"
            "`/screener risk limit=12`\n"
            "`/screener momentum cap_min=100000000 vol_ratio_min=0.1`\n"
            "`/screener preset=fresh cap_min=50000000 risk_max=7 limit=10`\n\n"
            "Ключи: `cap_min`, `cap_max`, `vol_ratio_min`, `rsi_max`, `change_24h_min`, `risk_max`, `fresh_min`, `limit`.",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )
        return
    await run_report(message, "🧰 Прогоняю фильтры...", analyzer.market_screener(filters))


@dp.message(Command("alert"))
async def alert_command(message: Message) -> None:
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer(
            "🔔 Добавить алерт:\n`/alert BTC 90000`\n`/alert ETH 2500 below`",
            parse_mode="Markdown",
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
        f"✅ Алерт добавлен.\n*{symbol}* {dir_text} ${price:,.2f}",
        parse_mode="Markdown",
    )


@dp.message(Command("alerts"))
async def alerts_command(message: Message) -> None:
    user_id = message.from_user.id
    alerts = alert_manager.get_user_alerts(user_id)
    if not alerts:
        await message.answer("🔔 Алертов пока нет. Добавить: `/alert BTC 90000`", parse_mode="Markdown")
        return
    lines = ["🔔 *Мои алерты*", ""]
    for index, alert in enumerate(alerts, 1):
        dir_symbol = "≤" if alert["direction"] == "below" else "≥"
        lines.append(f"{index}. *{alert['symbol']}* {dir_symbol} ${alert['price']:,.2f}")
    lines.append("")
    lines.append("Удалить: `/delalert 1`")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@dp.message(Command("delalert"))
async def delalert_command(message: Message) -> None:
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: `/delalert 1`", parse_mode="Markdown")
        return
    try:
        index = int(parts[1]) - 1
    except ValueError:
        await message.answer("❌ Неверный номер")
        return
    user_id = message.from_user.id
    if alert_manager.remove_alert(user_id, index):
        await message.answer("✅ Алерт удален")
    else:
        await message.answer("❌ Алерт не найден")


@dp.message(F.text)
async def text_handler(message: Message) -> None:
    if not message.text or message.text.startswith("/"):
        return
    text = message.text.strip().upper()
    if 2 <= len(text) <= 12 and text.isalpha():
        await run_report(message, f"⏳ Анализирую {text}...", analyzer.full_analysis(text))


@dp.callback_query(F.data == "menu_main")
async def cb_main(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.answer(
            "🏠 *Главное меню*\nВыбери, что смотрим дальше.",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )


@dp.callback_query(F.data == "menu_analyze")
async def cb_analyze(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.answer(
            "📊 *Анализ монеты*\n\n"
            "Просто пришли тикер: `BTC`, `ETH`, `SOL`, `TAO`, `RENDER`\n\n"
            "или команду: `/analyze BTC`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )


@dp.callback_query(F.data == "menu_screener")
async def cb_screener(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        await query.message.answer(
            "🧰 *Скринер*\n\n"
            "Готовые пресеты:\n"
            "`/screener fresh`\n"
            "`/screener risk`\n"
            "`/screener momentum`\n"
            "`/screener oversold`\n\n"
            "Фильтры можно сочетать:\n"
            "`/screener fresh cap_min=50000000 risk_max=7 limit=10`\n"
            "`/screener momentum vol_ratio_min=0.12 change_24h_min=4`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )


@dp.callback_query(F.data == "menu_news")
async def cb_news(query: CallbackQuery) -> None:
    await run_callback_report(query, "📰 Собираю новости...", analyzer.latest_news())


@dp.callback_query(F.data == "menu_signals")
async def cb_signals(query: CallbackQuery) -> None:
    await run_callback_report(query, "📈 Сканирую топ сигналы...", analyzer.top_signals())


@dp.callback_query(F.data == "menu_fresh")
async def cb_fresh(query: CallbackQuery) -> None:
    await run_callback_report(query, "🚀 Ищу fresh-cap идеи...", analyzer.find_new_potential())


@dp.callback_query(F.data == "menu_listing_risk")
async def cb_listing_risk(query: CallbackQuery) -> None:
    await run_callback_report(query, "🧨 Ищу fresh large-cap под раздачу...", analyzer.find_listing_risk())


@dp.callback_query(F.data == "menu_overbought")
async def cb_overbought(query: CallbackQuery) -> None:
    await run_callback_report(query, "🔥 Ищу перегретые монеты...", analyzer.find_overbought())


@dp.callback_query(F.data == "menu_dump")
async def cb_dump(query: CallbackQuery) -> None:
    await run_callback_report(query, "⚠️ Сканирую дамп-риск...", analyzer.find_dump_risk())


@dp.callback_query(F.data == "menu_alerts")
async def cb_alerts(query: CallbackQuery) -> None:
    await query.answer()
    if not query.message:
        return
    alerts = alert_manager.get_user_alerts(query.from_user.id)
    if not alerts:
        text = "🔔 *Мои алерты*\n\nАлертов пока нет.\n\nДобавить: `/alert BTC 90000`"
    else:
        lines = ["🔔 *Мои алерты*", ""]
        for index, alert in enumerate(alerts, 1):
            dir_symbol = "≤" if alert["direction"] == "below" else "≥"
            lines.append(f"{index}. *{alert['symbol']}* {dir_symbol} ${alert['price']:,.2f}")
        lines.append("")
        lines.append("Удалить: `/delalert 1`")
        text = "\n".join(lines)
    await query.message.answer(text, parse_mode="Markdown", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_help")
async def cb_help(query: CallbackQuery) -> None:
    await query.answer()
    if query.message:
        text = (
            "❓ *Команды*\n\n"
            "`/analyze BTC` - полный анализ\n"
            "`/scan` - скан рынка по широкому покрытию\n"
            "`/news` - новостная лента\n"
            "`/fresh` - свежие монеты с потенциалом\n"
            "`/risk` - fresh large-cap монеты под риск распределения\n"
            "`/dump` - дамп-риск\n"
            "`/overbought` - перегретые монеты\n"
            "`/signals` - лучшие long/risk сигналы\n"
            "`/screener fresh cap_min=50000000 risk_max=7` - кастомный скрин\n"
            "`/alert BTC 90000` - алерт выше цены\n"
            "`/alert ETH 2500 below` - алерт ниже цены\n"
            "`/alerts` - мои алерты\n"
            "`/delalert 1` - удалить алерт\n\n"
            "Web app берет данные из встроенного API, поэтому на Railway лучше держать `BOT_TOKEN`, `WEB_APP_URL` и при возможности `COINGECKO_API_KEY`."
        )
        await query.message.answer(text, parse_mode="Markdown", reply_markup=back_keyboard())


async def check_alerts_loop() -> None:
    while True:
        try:
            triggered = await alert_manager.check_alerts(analyzer)
            if bot:
                for user_id, message in triggered:
                    try:
                        await bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                    except Exception as exc:
                        logger.error("Alert send error for %s: %s", user_id, exc)
        except Exception as exc:
            logger.error("Alert check error: %s", exc)
        await asyncio.sleep(60)


async def index_handler(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(PROJECT_DIR / "index.html")


async def health_handler(request: web.Request) -> web.Response:
    payload = {
        "ok": True,
        "bot_enabled": bool(BOT_TOKEN),
        "web_port": WEB_PORT,
    }
    return web.json_response(payload)


async def api_market_handler(request: web.Request) -> web.Response:
    try:
        limit = max(100, min(1000, int(request.query.get("limit", "700"))))
    except ValueError:
        limit = 700
    payload = await analyzer.get_market_overview(limit=limit)
    return web.json_response(payload)


async def api_news_handler(request: web.Request) -> web.Response:
    try:
        limit = max(3, min(20, int(request.query.get("limit", "10"))))
    except ValueError:
        limit = 10
    payload = {"items": await analyzer.get_news_items(limit=limit)}
    return web.json_response(payload)


async def api_coin_handler(request: web.Request) -> web.Response:
    coin_id = request.match_info.get("coin_id", "")
    try:
        days = max(1, min(365, int(request.query.get("days", "30"))))
    except ValueError:
        days = 30
    try:
        payload = await analyzer.get_coin_dashboard(coin_id, chart_days=days)
        return web.json_response(payload)
    except ValueError:
        return web.json_response({"error": "coin_not_found"}, status=404)


async def start_http_server() -> Optional[web.AppRunner]:
    if not WEB_PORT:
        return None
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/api/market", api_market_handler)
    app.router.add_get("/api/news", api_news_handler)
    app.router.add_get("/api/coin/{coin_id}", api_coin_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=WEB_PORT)
    await site.start()
    logger.info("HTTP server started on port %s", WEB_PORT)
    return runner


async def main() -> None:
    runner = await start_http_server()
    alert_task = asyncio.create_task(check_alerts_loop())
    if bot:
        logger.info("Bot polling started")
        try:
            await dp.start_polling(bot)
        finally:
            alert_task.cancel()
            if runner:
                await runner.cleanup()
    else:
        logger.warning("BOT_TOKEN is not set. Running web app/API only.")
        try:
            await asyncio.Event().wait()
        finally:
            alert_task.cancel()
            if runner:
                await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
