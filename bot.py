from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from alerts import AlertManager
from analysis import CryptoAnalyzer
from contract_checker import ContractSecurityChecker
from scanner import MultiChainScanner
from sniper import get_sniper_status_text, scanner_loop as sniper_loop

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USERS_FILE = Path(__file__).resolve().parent / "users.json"
SUPPORTED_CHAINS = {"ethereum", "bsc", "solana", "base", "arbitrum", "polygon"}

dp = Dispatcher()
scanner = MultiChainScanner()
checker = ContractSecurityChecker()
alerts = AlertManager()
analyzer = CryptoAnalyzer()


def ensure_users_file() -> None:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")


def load_users() -> set[int]:
    ensure_users_file()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data if str(x).isdigit()}
    except Exception:
        return set()


def save_user(user_id: int) -> None:
    users = load_users()
    users.add(user_id)
    USERS_FILE.write_text(json.dumps(sorted(users), ensure_ascii=False), encoding="utf-8")


def _fmt_money(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆕 /new", callback_data="run:new"), InlineKeyboardButton(text="🌍 /scan", callback_data="run:scan")],
            [InlineKeyboardButton(text="🐸 /meme", callback_data="run:meme"), InlineKeyboardButton(text="🏦 /defi", callback_data="run:defi")],
            [InlineKeyboardButton(text="🔥 /hot", callback_data="run:hot"), InlineKeyboardButton(text="✅ /safe", callback_data="run:safe")],
            [InlineKeyboardButton(text="🎯 /snipe", callback_data="run:snipe"), InlineKeyboardButton(text="🛡 /check", callback_data="help:check")],
        ]
    )


def token_actions(contract: str, chain: str, symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"refresh:{chain}:{contract}"), InlineKeyboardButton(text="🔍 Check Contract", callback_data=f"check:{chain}:{contract}")],
            [InlineKeyboardButton(text="📊 Analyze", callback_data=f"analyze:{symbol}")],
        ]
    )


def token_card(row: dict[str, Any]) -> str:
    listed = f"{row['age_hours']:.1f} hours ago" if row["age_hours"] >= 1 else f"{int(row['age_hours'] * 60)} min ago"
    tax = f"{row['buy_tax']}% / {row['sell_tax']}%" if row.get("buy_tax") is not None and row.get("sell_tax") is not None else "N/A"
    signal = "WATCH" if row.get("risk_score", 0) <= 30 else "CAUTION"
    return (
        "🆕 NEW TOKEN FOUND\n"
        f"📛 Name: {row['name']} ({row['symbol']})\n"
        f"🌐 Chain: {row['chain'].capitalize()}\n"
        f"⏰ Listed: {listed}\n"
        f"💧 Liquidity: {_fmt_money(float(row['liquidity']))}\n"
        f"💰 Market Cap: {_fmt_money(float(row['market_cap']))}\n"
        f"📊 Volume 24h: {_fmt_money(float(row['volume_24h']))}\n"
        f"🟢 Buy/Sell Tax: {tax}\n"
        f"✅ Honeypot: {'No' if not row.get('is_honeypot') else 'Yes'}\n"
        f"📈 Signal: {signal}\n"
        f"Contract: `{row['contract']}`"
    )


async def _scan_mode(mode: str) -> list[dict[str, Any]]:
    rows = await scanner.scan_new_tokens(max_age_hours=24)
    if mode == "new":
        rows = [x for x in rows if x["age_hours"] <= 6]
    elif mode == "meme":
        rows = [x for x in rows if x.get("category") == "meme"]
    elif mode == "defi":
        rows = [x for x in rows if x.get("category") == "defi"]
    elif mode == "hot":
        rows.sort(key=lambda x: x.get("volume_1h", 0), reverse=True)
    elif mode == "safe":
        rows = [x for x in rows if not x.get("is_honeypot")]
    return rows[:12]


async def _send_scan_response(message: Message, mode: str) -> None:
    waiting = await message.answer("🔍 Scanning live DexScreener data...")
    try:
        rows = await _scan_mode(mode)
    except Exception as exc:
        logger.exception("scan command failed: %s", exc)
        await waiting.edit_text(f"❌ Scan failed: {str(exc)[:160]}")
        return
    if not rows:
        await waiting.edit_text("No tokens found.")
        return
    first = rows[0]
    await waiting.edit_text(token_card(first), parse_mode="Markdown", reply_markup=token_actions(first["contract"], first["chain"], first["symbol"]))
    for row in rows[1:4]:
        await message.answer(token_card(row), parse_mode="Markdown", reply_markup=token_actions(row["contract"], row["chain"], row["symbol"]))


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    save_user(message.from_user.id)
    await message.answer("🚀 Crypto Bot online\nCommands: /new /scan /meme /defi /hot /safe /check /snipe", reply_markup=main_menu())


@dp.callback_query(F.data.startswith("run:"))
async def cb_run(query: CallbackQuery) -> None:
    save_user(query.from_user.id)
    mode = query.data.split(":", 1)[1]
    await query.answer()
    if mode == "snipe":
        await query.message.answer(get_sniper_status_text(), parse_mode="Markdown")
        return
    await _send_scan_response(query.message, mode)


@dp.callback_query(F.data.startswith("refresh:"))
async def cb_refresh(query: CallbackQuery) -> None:
    await query.answer("Refreshing...")
    _, chain, contract = query.data.split(":", 2)
    rows = await scanner.scan_new_tokens(max_age_hours=24)
    for row in rows:
        if row["chain"] == chain and row["contract"].lower() == contract.lower():
            await query.message.edit_text(token_card(row), parse_mode="Markdown", reply_markup=token_actions(row["contract"], row["chain"], row["symbol"]))
            return
    await query.message.answer("Token not found in latest scan.")


@dp.callback_query(F.data.startswith("check:"))
async def cb_check(query: CallbackQuery) -> None:
    await query.answer("Checking contract...")
    _, chain, contract = query.data.split(":", 2)
    await query.message.answer(await checker.check_and_format_report(contract, chain), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("analyze:"))
async def cb_analyze(query: CallbackQuery) -> None:
    await query.answer("Analyzing...")
    symbol = query.data.split(":", 1)[1]
    snap = await analyzer.indicator_snapshot(symbol)
    await query.message.answer(f"📊 Analysis {snap['symbol']}\nRSI: {snap['rsi']}\nMACD: {snap['macd']}\nSignal: {snap['macd_signal']}\nBB: {snap['bb_low']} / {snap['bb_mid']} / {snap['bb_high']}")


@dp.callback_query(F.data == "help:check")
async def cb_help_check(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer("Usage: /check <chain> <contract>\nExample: /check ethereum 0x...")


@dp.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "scan")


@dp.message(Command("new"))
async def cmd_new(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "new")


@dp.message(Command("meme"))
async def cmd_meme(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "meme")


@dp.message(Command("defi"))
async def cmd_defi(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "defi")


@dp.message(Command("hot"))
async def cmd_hot(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "hot")


@dp.message(Command("safe"))
async def cmd_safe(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "safe")


@dp.message(Command("snipe"))
async def cmd_snipe(message: Message) -> None:
    save_user(message.from_user.id)
    await message.answer(get_sniper_status_text(), parse_mode="Markdown")


@dp.message(Command("check"))
async def cmd_check(message: Message) -> None:
    save_user(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /check <chain> <contract>")
        return
    chain, contract = parts[1].lower(), parts[2].strip()
    if chain not in SUPPORTED_CHAINS:
        await message.answer("Supported chains: ethereum, bsc, solana, base, arbitrum, polygon")
        return
    await message.answer(await checker.check_and_format_report(contract, chain), parse_mode="Markdown")


@dp.message(Command("alertprice"))
async def cmd_alertprice(message: Message) -> None:
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("Usage: /alertprice PEPE above 0.000001")
        return
    alerts.add_price_alert(message.from_user.id, parts[1], float(parts[3]), parts[2].lower())
    await message.answer("✅ Price alert added")


@dp.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    data = alerts.get_user_alerts(message.from_user.id)
    if not data:
        await message.answer("No alerts.")
        return
    await message.answer("\n".join(["🔔 Alerts:"] + [f"{i}. {a['type']} {a['symbol']} {a['condition']} {a['value']}" for i, a in enumerate(data, 1)]))


async def alerts_loop(bot: Bot) -> None:
    while True:
        try:
            rows = await scanner.scan_new_tokens(max_age_hours=24)
            for user_id, txt in alerts.evaluate(rows):
                await bot.send_message(user_id, txt)
        except Exception as exc:
            logger.exception("alerts loop error: %s", exc)
        await asyncio.sleep(20)


async def run_bot_forever() -> None:
    ensure_users_file()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    await scanner.start()
    bot = Bot(BOT_TOKEN)
    asyncio.create_task(alerts_loop(bot))
    asyncio.create_task(sniper_loop(bot))
    retry = 3
    while True:
        try:
            logger.info("start polling")
            await dp.start_polling(bot)
        except Exception as exc:
            logger.exception("polling crash: %s", exc)
            await asyncio.sleep(retry)
            retry = min(60, retry * 2)


if __name__ == "__main__":
    asyncio.run(run_bot_forever())


import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from dotenv import load_dotenv

from alerts import AlertManager
from analysis import CryptoAnalyzer
from contract_checker import ContractSecurityChecker
from scanner import MultiChainScanner
from sniper import scanner_loop as sniper_loop, get_sniper_status_text

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USERS_FILE = Path(__file__).resolve().parent / "users.json"
SUPPORTED_CHAINS = {"ethereum", "bsc", "solana", "base", "arbitrum", "polygon"}

dp = Dispatcher()
scanner = MultiChainScanner()
checker = ContractSecurityChecker()
alerts = AlertManager()
analyzer = CryptoAnalyzer()


def ensure_users_file() -> None:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")


def load_users() -> set[int]:
    ensure_users_file()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data if str(x).isdigit()}
    except Exception:
        return set()


def save_user(user_id: int) -> None:
    users = load_users()
    users.add(user_id)
    USERS_FILE.write_text(json.dumps(sorted(users), ensure_ascii=False), encoding="utf-8")


def _fmt_money(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆕 /new", callback_data="run:new"), InlineKeyboardButton(text="🌍 /scan", callback_data="run:scan")],
            [InlineKeyboardButton(text="🐸 /meme", callback_data="run:meme"), InlineKeyboardButton(text="🏦 /defi", callback_data="run:defi")],
            [InlineKeyboardButton(text="🔥 /hot", callback_data="run:hot"), InlineKeyboardButton(text="✅ /safe", callback_data="run:safe")],
            [InlineKeyboardButton(text="🎯 /snipe", callback_data="run:snipe"), InlineKeyboardButton(text="🛡 /check", callback_data="help:check")],
        ]
    )


def token_actions(contract: str, chain: str, symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"refresh:{chain}:{contract}"), InlineKeyboardButton(text="🔍 Check Contract", callback_data=f"check:{chain}:{contract}")],
            [InlineKeyboardButton(text="📊 Analyze", callback_data=f"analyze:{symbol}")],
        ]
    )


def token_card(row: dict[str, Any]) -> str:
    listed = f"{row['age_hours']:.1f} hours ago" if row["age_hours"] >= 1 else f"{int(row['age_hours'] * 60)} min ago"
    tax = (
        f"{row['buy_tax']}% / {row['sell_tax']}%"
        if row.get("buy_tax") is not None and row.get("sell_tax") is not None
        else "N/A"
    )
    signal = "WATCH" if row.get("risk_score", 0) <= 30 else "CAUTION"
    return (
        "🆕 NEW TOKEN FOUND\n"
        f"📛 Name: {row['name']} ({row['symbol']})\n"
        f"🌐 Chain: {row['chain'].capitalize()}\n"
        f"⏰ Listed: {listed}\n"
        f"💧 Liquidity: {_fmt_money(float(row['liquidity']))}\n"
        f"💰 Market Cap: {_fmt_money(float(row['market_cap']))}\n"
        f"📊 Volume 24h: {_fmt_money(float(row['volume_24h']))}\n"
        f"🟢 Buy/Sell Tax: {tax}\n"
        f"✅ Honeypot: {'No' if not row.get('is_honeypot') else 'Yes'}\n"
        f"📈 Signal: {signal}\n"
        f"Contract: `{row['contract']}`"
    )


async def _scan_mode(mode: str) -> list[dict[str, Any]]:
    rows = await scanner.scan_new_tokens(max_age_hours=24)
    if mode == "new":
        rows = [x for x in rows if x["age_hours"] <= 6]
    elif mode == "meme":
        rows = [x for x in rows if x.get("category") == "meme"]
    elif mode == "defi":
        rows = [x for x in rows if x.get("category") == "defi"]
    elif mode == "hot":
        rows.sort(key=lambda x: x.get("volume_1h", 0), reverse=True)
    elif mode == "safe":
        rows = [x for x in rows if not x.get("is_honeypot")]
    return rows[:12]


async def _send_scan_response(message: Message, mode: str) -> None:
    waiting = await message.answer("🔍 Scanning live DexScreener data...")
    try:
        rows = await _scan_mode(mode)
    except Exception as exc:
        logger.exception("scan command failed: %s", exc)
        await waiting.edit_text(f"❌ Scan failed: {str(exc)[:160]}")
        return
    if not rows:
        await waiting.edit_text("No tokens found.")
        return
    first = rows[0]
    await waiting.edit_text(token_card(first), parse_mode="Markdown", reply_markup=token_actions(first["contract"], first["chain"], first["symbol"]))
    for row in rows[1:4]:
        await message.answer(token_card(row), parse_mode="Markdown", reply_markup=token_actions(row["contract"], row["chain"], row["symbol"]))


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    save_user(message.from_user.id)
    await message.answer(
        "🚀 Crypto Bot online\nUse commands: /new /scan /meme /defi /hot /safe /check /snipe",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data.startswith("run:"))
async def cb_run(query: CallbackQuery) -> None:
    save_user(query.from_user.id)
    mode = query.data.split(":", 1)[1]
    await query.answer()
    if mode == "snipe":
        await query.message.answer(get_sniper_status_text(), parse_mode="Markdown")
        return
    await _send_scan_response(query.message, mode)


@dp.callback_query(F.data.startswith("refresh:"))
async def cb_refresh(query: CallbackQuery) -> None:
    await query.answer("Refreshing...")
    _, chain, contract = query.data.split(":", 2)
    rows = await scanner.scan_new_tokens(max_age_hours=24)
    for row in rows:
        if row["chain"] == chain and row["contract"].lower() == contract.lower():
            await query.message.edit_text(token_card(row), parse_mode="Markdown", reply_markup=token_actions(row["contract"], row["chain"], row["symbol"]))
            return
    await query.message.answer("Token not found in latest scan.")


@dp.callback_query(F.data.startswith("check:"))
async def cb_check(query: CallbackQuery) -> None:
    await query.answer("Checking contract...")
    _, chain, contract = query.data.split(":", 2)
    report = await checker.check_and_format_report(contract, chain)
    await query.message.answer(report, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("analyze:"))
async def cb_analyze(query: CallbackQuery) -> None:
    await query.answer("Analyzing...")
    symbol = query.data.split(":", 1)[1]
    snap = await analyzer.indicator_snapshot(symbol)
    await query.message.answer(
        f"📊 Analysis {snap['symbol']}\nRSI: {snap['rsi']}\nMACD: {snap['macd']}\nSignal: {snap['macd_signal']}\nBB: {snap['bb_low']} / {snap['bb_mid']} / {snap['bb_high']}",
    )


@dp.callback_query(F.data == "help:check")
async def cb_help_check(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer("Usage: /check <chain> <contract>\nExample: /check ethereum 0x...")


@dp.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "scan")


@dp.message(Command("new"))
async def cmd_new(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "new")


@dp.message(Command("meme"))
async def cmd_meme(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "meme")


@dp.message(Command("defi"))
async def cmd_defi(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "defi")


@dp.message(Command("hot"))
async def cmd_hot(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "hot")


@dp.message(Command("safe"))
async def cmd_safe(message: Message) -> None:
    save_user(message.from_user.id)
    await _send_scan_response(message, "safe")


@dp.message(Command("snipe"))
async def cmd_snipe(message: Message) -> None:
    save_user(message.from_user.id)
    await message.answer(get_sniper_status_text(), parse_mode="Markdown")


@dp.message(Command("check"))
async def cmd_check(message: Message) -> None:
    save_user(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /check <chain> <contract>")
        return
    chain = parts[1].lower()
    contract = parts[2].strip()
    if chain not in SUPPORTED_CHAINS:
        await message.answer("Supported chains: ethereum, bsc, solana, base, arbitrum, polygon")
        return
    report = await checker.check_and_format_report(contract, chain)
    await message.answer(report, parse_mode="Markdown")


@dp.message(Command("alertprice"))
async def cmd_alertprice(message: Message) -> None:
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("Usage: /alertprice PEPE above 0.000001")
        return
    alerts.add_price_alert(message.from_user.id, parts[1], float(parts[3]), parts[2].lower())
    await message.answer("✅ Price alert added")


@dp.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    data = alerts.get_user_alerts(message.from_user.id)
    if not data:
        await message.answer("No alerts.")
        return
    lines = ["🔔 Alerts:"]
    for i, a in enumerate(data, 1):
        lines.append(f"{i}. {a['type']} {a['symbol']} {a['condition']} {a['value']}")
    await message.answer("\n".join(lines))


async def alerts_loop(bot: Bot) -> None:
    while True:
        try:
            rows = await scanner.scan_new_tokens(max_age_hours=24)
            for user_id, txt in alerts.evaluate(rows):
                await bot.send_message(user_id, txt)
        except Exception as exc:
            logger.exception("alerts loop error: %s", exc)
        await asyncio.sleep(20)


async def run_bot_forever() -> None:
    ensure_users_file()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    await scanner.start()
    bot = Bot(BOT_TOKEN)
    asyncio.create_task(alerts_loop(bot))
    asyncio.create_task(sniper_loop(bot))

    retry = 3
    while True:
        try:
            logger.info("start polling")
            await dp.start_polling(bot)
        except Exception as exc:
            logger.exception("polling crash: %s", exc)
            await asyncio.sleep(retry)
            retry = min(60, retry * 2)


if __name__ == "__main__":
    asyncio.run(run_bot_forever())


import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

from alerts import AlertManager
from contract_checker import ContractSecurityChecker
from scanner import MultiChainScanner
from sniper import scanner_loop as sniper_loop

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USERS_FILE = Path(__file__).resolve().parent / "users.json"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

dp = Dispatcher()
scanner = MultiChainScanner()
checker = ContractSecurityChecker()
alerts = AlertManager()


def ensure_users_file() -> None:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")


def load_users() -> set[int]:
    ensure_users_file()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in data if str(x).isdigit()}
    except Exception:
        return set()


def save_user(user_id: int) -> None:
    users = load_users()
    users.add(user_id)
    USERS_FILE.write_text(json.dumps(sorted(users), ensure_ascii=False), encoding="utf-8")


def fmt_money(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def format_token(row: dict[str, Any]) -> str:
    listed = f"{row['age_hours']:.1f} ч" if row["age_hours"] >= 1 else f"{int(row['age_hours'] * 60)} мин"
    return (
        "🆕 *НОВЫЙ ТОКЕН*\n"
        f"📛 Название: {row['name']} ({row['symbol']})\n"
        f"🔗 Сеть: {row['chain'].upper()}\n"
        f"⏰ Листинг: {listed} назад\n"
        f"👥 Холдеры: {int(row['holders']):,}\n"
        f"💧 Ликвидность: {fmt_money(float(row['liquidity']))}\n"
        f"💰 Капа: {fmt_money(float(row['market_cap']))}\n"
        f"📊 Объём 24ч: {fmt_money(float(row['volume_24h']))}\n"
        f"🟢 Налог: {row['buy_tax']}% / {row['sell_tax']}%\n"
        f"✅ Honeypot: {'Нет' if not row['is_honeypot'] else 'Да'}\n"
        f"✅ Mint: {'Отключен' if not row['is_mintable'] else 'Активен'}\n"
        f"🔒 Ликвидность: {'Заблокирована' if row['lp_locked'] else 'Не заблокирована'}\n"
        f"📈 Сигнал: {'СМОТРЕТЬ' if row['risk_score'] <= 25 else 'ОСТОРОЖНО'}\n"
        f"⚠️ Риск скама: {row['risk_score']}/100\n"
        f"🔍 [DexScreener]({row['dex_url']})"
    )


async def run_scan(message: Message, mode: str) -> None:
    save_user(message.from_user.id)
    msg = await message.answer("🔍 Сканирую все сети...")
    try:
        rows = await scanner.scan_new_tokens(max_age_hours=24)
    except Exception as exc:
        logger.exception("scan error")
        await msg.edit_text(f"❌ Ошибка сканирования: {str(exc)[:180]}")
        return

    if mode == "new":
        rows = [x for x in rows if x["age_hours"] <= 6]
    elif mode == "hot":
        rows.sort(key=lambda x: x["volume_1h"], reverse=True)
    elif mode == "safe":
        rows = [x for x in rows if x["risk_score"] <= 20 and not x["is_honeypot"]]
    elif mode == "meme":
        rows = [x for x in rows if any(k in (x["name"] + x["symbol"]).lower() for k in ("pepe", "doge", "inu", "cat", "frog"))]
    elif mode == "defi":
        rows = [x for x in rows if any(k in (x["name"] + x["symbol"]).lower() for k in ("swap", "dex", "yield", "farm", "vault"))]

    if not rows:
        await msg.edit_text("Подходящих токенов не найдено.")
        return
    await msg.edit_text("\n\n".join(format_token(x) for x in rows[:4]), parse_mode="Markdown", disable_web_page_preview=True)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    save_user(message.from_user.id)
    await message.answer(
        "🚀 *CRYPTO_BOT Screener Online*\n\n"
        "`/new` — листинг до 6 часов\n"
        "`/scan` — новые токены до 24ч\n"
        "`/meme` — мем-коины\n"
        "`/defi` — DeFi\n"
        "`/check <chain> <contract>` — проверка контракта\n"
        "`/hot` — топ по объёму 1ч\n"
        "`/safe` — без скам-флагов\n"
        "`/alertprice <SYM> <above|below> <price>`\n"
        "`/alertvol <SYM> <min_volume_1h>`\n"
        "`/alertnew <chain> [minutes]`\n"
        "`/alerts`",
        parse_mode="Markdown",
    )


@dp.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    await run_scan(message, "scan")


@dp.message(Command("new"))
async def cmd_new(message: Message) -> None:
    await run_scan(message, "new")


@dp.message(Command("meme"))
async def cmd_meme(message: Message) -> None:
    await run_scan(message, "meme")


@dp.message(Command("defi"))
async def cmd_defi(message: Message) -> None:
    await run_scan(message, "defi")


@dp.message(Command("hot"))
async def cmd_hot(message: Message) -> None:
    await run_scan(message, "hot")


@dp.message(Command("safe"))
async def cmd_safe(message: Message) -> None:
    await run_scan(message, "safe")


@dp.message(Command("check"))
async def cmd_check(message: Message) -> None:
    save_user(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: `/check eth 0x...`", parse_mode="Markdown")
        return
    chain = parts[1].lower()
    contract = parts[2].strip()
    msg = await message.answer("🧪 Проверяю контракт...")
    report = await checker.check_and_format_report(contract, chain)
    await msg.edit_text(report, parse_mode="Markdown")


@dp.message(Command("alertprice"))
async def cmd_alert_price(message: Message) -> None:
    save_user(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("Использование: `/alertprice PEPE above 0.000001`", parse_mode="Markdown")
        return
    alerts.add_price_alert(message.from_user.id, parts[1], float(parts[3]), parts[2].lower())
    await message.answer("✅ Price alert добавлен.")


@dp.message(Command("alertvol"))
async def cmd_alert_vol(message: Message) -> None:
    save_user(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: `/alertvol PEPE 150000`", parse_mode="Markdown")
        return
    alerts.add_volume_alert(message.from_user.id, parts[1], float(parts[2]))
    await message.answer("✅ Volume alert добавлен.")


@dp.message(Command("alertnew"))
async def cmd_alert_new(message: Message) -> None:
    save_user(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: `/alertnew eth 30`", parse_mode="Markdown")
        return
    minutes = int(parts[2]) if len(parts) > 2 else 30
    alerts.add_listing_alert(message.from_user.id, parts[1], minutes)
    await message.answer("✅ New listing alert добавлен.")


@dp.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    save_user(message.from_user.id)
    user_alerts = alerts.get_user_alerts(message.from_user.id)
    if not user_alerts:
        await message.answer("Алертов нет.")
        return
    lines = ["🔔 *Мои алерты*"]
    for idx, a in enumerate(user_alerts, 1):
        lines.append(f"{idx}. `{a['type']}` {a['symbol']} {a['condition']} {a['value']}")
    await message.answer("\n".join(lines), parse_mode="Markdown")


async def alert_worker(bot: Bot) -> None:
    while True:
        try:
            rows = await scanner.scan_new_tokens(max_age_hours=24)
            for user_id, text in alerts.evaluate(rows):
                try:
                    await bot.send_message(user_id, text)
                except Exception as exc:
                    logger.warning("alert send failed %s: %s", user_id, exc)
        except Exception as exc:
            logger.exception("alert worker error: %s", exc)
        await asyncio.sleep(30)


async def auto_digest_worker(bot: Bot) -> None:
    while True:
        try:
            rows = await scanner.scan_new_tokens(max_age_hours=6)
            if rows:
                text = "🔄 Автообновление (3 мин)\n\n" + "\n\n".join(format_token(x) for x in rows[:2])
                for uid in load_users():
                    try:
                        await bot.send_message(uid, text, parse_mode="Markdown", disable_web_page_preview=True)
                    except Exception:
                        continue
        except Exception as exc:
            logger.exception("digest worker error: %s", exc)
        await asyncio.sleep(180)


async def run_bot_forever() -> None:
    ensure_users_file()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN отсутствует.")
    await scanner.start()
    bot = Bot(BOT_TOKEN)
    asyncio.create_task(alert_worker(bot))
    asyncio.create_task(auto_digest_worker(bot))
    asyncio.create_task(sniper_loop(bot))

    delay = 3
    while True:
        try:
            logger.info("Starting polling...")
            await dp.start_polling(bot)
        except Exception as exc:
            logger.exception("Polling crashed, restarting in %s sec: %s", delay, exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        else:
            break


if __name__ == "__main__":
    asyncio.run(run_bot_forever())
"""
Professional Multi-Chain New Token Screener Bot (aiogram 3.x).
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "").strip()
DEXTOOLS_API_KEY = os.getenv("DEXTOOLS_API_KEY", "").strip()  # optional, reserved for extension
USERS_FILE = Path(__file__).resolve().parent / "users.json"

SUPPORTED_CHAINS = {
    "ethereum": {"name": "Ethereum", "goplus": "1", "moralis": "eth"},
    "bsc": {"name": "BSC", "goplus": "56", "moralis": "bsc"},
    "solana": {"name": "Solana", "goplus": None, "moralis": "solana"},
    "base": {"name": "Base", "goplus": "8453", "moralis": "base"},
    "arbitrum": {"name": "Arbitrum", "goplus": "42161", "moralis": "arbitrum"},
    "polygon": {"name": "Polygon", "goplus": "137", "moralis": "polygon"},
}

MEME_KEYWORDS = ("pepe", "doge", "inu", "meme", "cat", "frog", "elon", "moon")
DEFI_KEYWORDS = ("swap", "dex", "yield", "farm", "lending", "vault", "staked", "finance")

dp = Dispatcher()


@dataclass
class TokenRecord:
    chain: str
    name: str
    symbol: str
    contract: str
    listed_hours: float
    holders: Optional[int]
    liquidity_usd: float
    market_cap: float
    volume_24h: float
    volume_1h: float
    buy_tax: Optional[float]
    sell_tax: Optional[float]
    honeypot: Optional[bool]
    mintable: Optional[bool]
    blacklist: Optional[bool]
    renounced: Optional[bool]
    contract_verified: Optional[bool]
    liquidity_locked: Optional[bool]
    dex_url: str
    category: str


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


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    txt = str(value).strip().lower()
    if txt in {"1", "true", "yes"}:
        return True
    if txt in {"0", "false", "no"}:
        return False
    return None


def _age_hours(created_at_ms: Any) -> float:
    if not created_at_ms:
        return 999.0
    now_ms = int(time.time() * 1000)
    return max((now_ms - int(created_at_ms)) / 3_600_000, 0.0)


def _fmt_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


class TokenScreener:
    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get_json(self, url: str, *, params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None) -> Any:
        assert self.session is not None
        for attempt in range(3):
            try:
                async with self.session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(1.2 + attempt)
                        continue
                    if resp.status >= 400:
                        body = (await resp.text())[:200]
                        raise RuntimeError(f"{resp.status} {url} {body}")
                    return await resp.json(content_type=None)
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.8 + attempt)
        return None

    async def _fetch_candidates(self) -> list[dict[str, Any]]:
        # DexScreener profiles/boosts are the entrypoint for fresh tokens.
        profiles = await self._get_json("https://api.dexscreener.com/token-profiles/latest/v1")
        boosts = await self._get_json("https://api.dexscreener.com/token-boosts/latest/v1")
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for row in (profiles or []):
            chain = str(row.get("chainId", "")).lower()
            address = str(row.get("tokenAddress", "")).lower()
            if chain in SUPPORTED_CHAINS and address:
                merged[(chain, address)] = row
        for row in (boosts or []):
            chain = str(row.get("chainId", "")).lower()
            address = str(row.get("tokenAddress", "")).lower()
            if chain in SUPPORTED_CHAINS and address:
                merged[(chain, address)] = row
        return list(merged.values())[:120]

    async def _fetch_token_pairs(self, token_address: str) -> list[dict[str, Any]]:
        raw = await self._get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
        return raw.get("pairs", []) if isinstance(raw, dict) else []

    async def _fetch_goplus_security(self, chain_key: str, contract: str) -> dict[str, Any]:
        chain_meta = SUPPORTED_CHAINS[chain_key]
        chain_id = chain_meta["goplus"]
        if not chain_id:
            return {}
        url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
        data = await self._get_json(url, params={"contract_addresses": contract})
        result = (data or {}).get("result", {})
        return result.get(contract.lower()) or result.get(contract) or {}

    async def _fetch_moralis_holders(self, chain_key: str, contract: str) -> Optional[int]:
        if not MORALIS_API_KEY:
            return None
        chain = SUPPORTED_CHAINS[chain_key]["moralis"]
        headers = {"X-API-Key": MORALIS_API_KEY}
        url = f"https://deep-index.moralis.io/api/v2.2/erc20/{contract}/holders"
        data = await self._get_json(url, params={"chain": chain}, headers=headers)
        return _to_int((data or {}).get("total"))

    def _classify(self, name: str, symbol: str) -> str:
        text = f"{name} {symbol}".lower()
        if any(k in text for k in MEME_KEYWORDS):
            return "meme"
        if any(k in text for k in DEFI_KEYWORDS):
            return "defi"
        return "other"

    def _scam_flags(self, t: TokenRecord) -> list[str]:
        flags: list[str] = []
        if t.honeypot is True:
            flags.append("Honeypot")
        if t.mintable is True:
            flags.append("Mint active")
        if t.blacklist is True:
            flags.append("Blacklist function")
        if (t.buy_tax is not None and t.buy_tax > 10) or (t.sell_tax is not None and t.sell_tax > 10):
            flags.append("Tax > 10%")
        if t.liquidity_locked is False:
            flags.append("Liquidity not locked")
        if t.holders is not None and t.holders < 100:
            flags.append("Holders < 100")
        if t.contract_verified is False:
            flags.append("Contract not verified")
        return flags

    def _is_safe(self, t: TokenRecord) -> bool:
        return len(self._scam_flags(t)) == 0

    def _score(self, t: TokenRecord) -> float:
        score = 0.0
        score += min(t.liquidity_usd / 100_000, 2.0) * 20
        score += min(t.volume_24h / 250_000, 2.0) * 20
        score += 15 if t.buy_tax == 0 and t.sell_tax == 0 else 0
        score += 15 if self._is_safe(t) else 0
        score += min((t.holders or 0) / 4000, 2.0) * 15
        score += 10 if t.listed_hours <= 6 else 0
        return round(score, 1)

    async def _build_record(self, pair: dict[str, Any]) -> Optional[TokenRecord]:
        chain_key = str(pair.get("chainId", "")).lower()
        if chain_key not in SUPPORTED_CHAINS:
            return None
        base = pair.get("baseToken", {}) or {}
        contract = str(base.get("address", "")).strip()
        if not contract:
            return None
        sec = await self._fetch_goplus_security(chain_key, contract)
        holders = _to_int(sec.get("holder_count"))
        if holders is None:
            holders = await self._fetch_moralis_holders(chain_key, contract)

        liquidity_locked = _to_bool(sec.get("lp_locked"))
        if liquidity_locked is None:
            liquidity_locked = _to_bool(sec.get("is_locked"))
        renounced = _to_bool(sec.get("owner_address")) is False if "owner_address" in sec else _to_bool(sec.get("is_renounced"))
        verified = _to_bool(sec.get("is_open_source"))
        buy_tax = _to_float(sec.get("buy_tax"), default=-1.0)
        sell_tax = _to_float(sec.get("sell_tax"), default=-1.0)

        return TokenRecord(
            chain=SUPPORTED_CHAINS[chain_key]["name"],
            name=str(base.get("name", "Unknown")),
            symbol=str(base.get("symbol", "UNK")),
            contract=contract,
            listed_hours=_age_hours(pair.get("pairCreatedAt")),
            holders=holders,
            liquidity_usd=_to_float((pair.get("liquidity") or {}).get("usd")),
            market_cap=_to_float(pair.get("marketCap") or pair.get("fdv")),
            volume_24h=_to_float((pair.get("volume") or {}).get("h24")),
            volume_1h=_to_float((pair.get("volume") or {}).get("h1")),
            buy_tax=None if buy_tax < 0 else buy_tax,
            sell_tax=None if sell_tax < 0 else sell_tax,
            honeypot=_to_bool(sec.get("is_honeypot")),
            mintable=_to_bool(sec.get("is_mintable")),
            blacklist=_to_bool(sec.get("is_blacklisted")),
            renounced=renounced,
            contract_verified=verified,
            liquidity_locked=liquidity_locked,
            dex_url=str(pair.get("url", "")),
            category=self._classify(str(base.get("name", "")), str(base.get("symbol", ""))),
        )

    async def scan(self) -> list[TokenRecord]:
        candidates = await self._fetch_candidates()
        records: list[TokenRecord] = []
        seen: set[str] = set()

        for c in candidates:
            token_address = str(c.get("tokenAddress", "")).strip()
            if not token_address or token_address.lower() in seen:
                continue
            seen.add(token_address.lower())
            try:
                pairs = await self._fetch_token_pairs(token_address)
            except Exception as exc:
                logger.warning("pairs error %s: %s", token_address, exc)
                continue
            # Keep the most liquid pair in supported chains.
            valid_pairs = [p for p in pairs if str((p or {}).get("chainId", "")).lower() in SUPPORTED_CHAINS]
            if not valid_pairs:
                continue
            chosen = max(valid_pairs, key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")))
            try:
                rec = await self._build_record(chosen)
            except Exception as exc:
                logger.warning("record build error %s: %s", token_address, exc)
                continue
            if rec is not None:
                records.append(rec)
        return records


screener = TokenScreener()
last_auto_digest = ""


def _passes_base_filters(t: TokenRecord) -> bool:
    return t.holders is not None and t.holders >= 2000 and t.liquidity_usd >= 50_000


def _signal(t: TokenRecord) -> str:
    flags = screener._scam_flags(t)
    if flags:
        return "ОСТОРОЖНО"
    if t.buy_tax == 0 and t.sell_tax == 0 and t.liquidity_usd >= 100_000:
        return "СМОТРЕТЬ"
    return "НЕЙТРАЛЬНО"


def _format_token(t: TokenRecord) -> str:
    tax_text = "N/A" if t.buy_tax is None or t.sell_tax is None else f"{t.buy_tax:.0f}% / {t.sell_tax:.0f}%"
    flags = screener._scam_flags(t)
    warning = "⚠️ " + ", ".join(flags) if flags else "✅ Рисков не обнаружено"
    listed = f"{t.listed_hours:.1f} ч" if t.listed_hours >= 1 else f"{int(t.listed_hours * 60)} мин"
    return (
        "🆕 *НОВЫЙ ТОКЕН*\n"
        f"📛 Название: {t.name} ({t.symbol})\n"
        f"🔗 Сеть: {t.chain}\n"
        f"⏰ Листинг: {listed} назад\n"
        f"👥 Холдеры: {t.holders:,}\n"
        f"💧 Ликвидность: {_fmt_money(t.liquidity_usd)}\n"
        f"💰 Капа: {_fmt_money(t.market_cap)}\n"
        f"📊 Объём 24ч: {_fmt_money(t.volume_24h)}\n"
        f"🟢 Налог: {tax_text}\n"
        f"✅ Honeypot: {'Нет' if t.honeypot is False else 'Да' if t.honeypot else 'Неизвестно'}\n"
        f"✅ Mint: {'Отключен' if t.mintable is False else 'Активен' if t.mintable else 'Неизвестно'}\n"
        f"🔒 Ликвидность: {'Заблокирована' if t.liquidity_locked else 'Не заблокирована' if t.liquidity_locked is False else 'Неизвестно'}\n"
        f"📈 Сигнал: {_signal(t)}\n"
        f"{warning}\n"
        f"🔍 [DexScreener]({t.dex_url})"
    )


async def _run_scan(message: Message, *, mode: str) -> None:
    save_user_id(message.from_user.id)
    msg = await message.answer("🔍 Сканирую новые токены по всем сетям...")
    try:
        rows = await screener.scan()
    except Exception as exc:
        logger.exception("scan failed")
        await msg.edit_text(f"❌ Ошибка сканирования: {str(exc)[:160]}")
        return

    filtered = [t for t in rows if _passes_base_filters(t)]
    if mode == "new":
        filtered = [t for t in filtered if t.listed_hours <= 6]
    elif mode == "meme":
        filtered = [t for t in filtered if t.category == "meme"]
    elif mode == "defi":
        filtered = [t for t in filtered if t.category == "defi"]
    elif mode == "safe":
        filtered = [t for t in filtered if screener._is_safe(t)]
    elif mode == "hot":
        filtered.sort(key=lambda x: x.volume_1h, reverse=True)
        filtered = filtered[:8]

    if mode != "hot":
        filtered.sort(key=lambda x: (screener._score(x), x.volume_24h), reverse=True)
        filtered = filtered[:8]

    if not filtered:
        await msg.edit_text("Нет токенов под критерии: холдеры >= 2000, ликвидность >= $50k, поддерживаемые сети.")
        return

    text = "\n\n".join(_format_token(t) for t in filtered[:4])
    await msg.edit_text(text, parse_mode="Markdown", disable_web_page_preview=True)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    save_user_id(message.from_user.id)
    text = (
        "🚀 *New Token Screener Bot активен*\n\n"
        "Сети: Ethereum, BSC, Solana, Base, Arbitrum, Polygon.\n"
        "Фильтры: холдеры >= 2000, ликвидность >= $50k, антискам-проверки.\n\n"
        "*Команды:*\n"
        "`/new` — листинг до 6 часов\n"
        "`/scan` — все новые токены\n"
        "`/meme` — только мем-коины\n"
        "`/defi` — только DeFi\n"
        "`/check 0x...` — проверка контракта\n"
        "`/hot` — топ по объему за 1ч\n"
        "`/safe` — токены без scam-флагов"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    await _run_scan(message, mode="scan")


@dp.message(Command("new"))
async def cmd_new(message: Message) -> None:
    await _run_scan(message, mode="new")


@dp.message(Command("meme"))
async def cmd_meme(message: Message) -> None:
    await _run_scan(message, mode="meme")


@dp.message(Command("defi"))
async def cmd_defi(message: Message) -> None:
    await _run_scan(message, mode="defi")


@dp.message(Command("hot"))
async def cmd_hot(message: Message) -> None:
    await _run_scan(message, mode="hot")


@dp.message(Command("safe"))
async def cmd_safe(message: Message) -> None:
    await _run_scan(message, mode="safe")


@dp.message(Command("check"))
async def cmd_check(message: Message) -> None:
    save_user_id(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: `/check 0x...`", parse_mode="Markdown")
        return
    contract = parts[1].strip()
    if len(contract) < 20:
        await message.answer("Неверный адрес контракта.")
        return

    msg = await message.answer("🧪 Проверяю контракт...")
    try:
        pairs = await screener._fetch_token_pairs(contract)
    except Exception as exc:
        await msg.edit_text(f"❌ Ошибка DexScreener: {str(exc)[:160]}")
        return
    if not pairs:
        await msg.edit_text("Контракт не найден в DexScreener.")
        return

    supported_pairs = [p for p in pairs if str((p or {}).get("chainId", "")).lower() in SUPPORTED_CHAINS]
    if not supported_pairs:
        await msg.edit_text("Контракт найден, но сеть не входит в поддерживаемые ETH/BSC/SOL/BASE/ARB/POL.")
        return

    pair = max(supported_pairs, key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")))
    rec = await screener._build_record(pair)
    if rec is None:
        await msg.edit_text("Не удалось построить профиль токена.")
        return
    await msg.edit_text(_format_token(rec), parse_mode="Markdown", disable_web_page_preview=True)


async def auto_update_loop(bot_instance: Bot) -> None:
    global last_auto_digest
    await asyncio.sleep(20)
    while True:
        try:
            rows = await screener.scan()
            filtered = [t for t in rows if _passes_base_filters(t) and t.listed_hours <= 6]
            filtered.sort(key=lambda x: (screener._score(x), x.volume_24h), reverse=True)
            top = filtered[:3]
            if top:
                body = "🔄 *Автообновление (каждые 3 мин)*\n\n" + "\n\n".join(_format_token(t) for t in top)
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                if digest != last_auto_digest:
                    last_auto_digest = digest
                    for user_id in load_users():
                        try:
                            await bot_instance.send_message(
                                user_id, body, parse_mode="Markdown", disable_web_page_preview=True
                            )
                        except Exception as exc:
                            logger.warning("auto update send failed for %s: %s", user_id, exc)
        except Exception as exc:
            logger.exception("auto update loop error: %s", exc)
        await asyncio.sleep(180)


async def main() -> None:
    ensure_users_file()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в окружении.")
    if not MORALIS_API_KEY:
        logger.warning("MORALIS_API_KEY не задан: холдеры будут только из GoPlus, где доступны.")
    if not DEXTOOLS_API_KEY:
        logger.info("DEXTOOLS_API_KEY не задан: бот работает без расширенных метрик DexTools.")

    await screener.start()
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(auto_update_loop(bot))
    logger.info("New token screener bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await screener.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        logger.error(str(exc))
        raise SystemExit(1)
