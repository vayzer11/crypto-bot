from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from dotenv import load_dotenv

from analysis import CryptoAnalyzer
from sniper import get_sniper_status_text, scanner_loop
from social_scanner import SocialScanner

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USERS_FILE = Path(__file__).resolve().parent / "users.json"

dp = Dispatcher()
analyzer = CryptoAnalyzer()
social_scanner = SocialScanner()

def ensure_users_file() -> None:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")

def load_users() -> set[int]:
    ensure_users_file()
    try:
        arr = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {int(x) for x in arr if str(x).isdigit()}
    except Exception:
        return set()

def save_user_id(user_id: int) -> None:
    ensure_users_file()
    users = load_users()
    users.add(int(user_id))
    USERS_FILE.write_text(json.dumps(sorted(users), ensure_ascii=False, indent=2), encoding="utf-8")

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Анализ монеты", callback_data="menu_analyze"), InlineKeyboardButton(text="🔥 Перекупленные", callback_data="menu_overbought")],
        [InlineKeyboardButton(text="💎 Gem Finder", callback_data="menu_gems"), InlineKeyboardButton(text="🚨 Скам-детектор", callback_data="menu_scam")],
        [InlineKeyboardButton(text="📡 Соц. сканер", callback_data="menu_social"), InlineKeyboardButton(text="🆕 Новые монеты", callback_data="menu_new")],
        [InlineKeyboardButton(text="⚡ Снайпер статус", callback_data="menu_sniper_status"), InlineKeyboardButton(text="📈 Топ сигналы", callback_data="menu_top_signals")],
        [InlineKeyboardButton(text="⚠️ Риск падения", callback_data="menu_risk_drop"), InlineKeyboardButton(text="🔔 Мои алерты", callback_data="menu_alerts")],
        [InlineKeyboardButton(text="📱 Открыть приложение", web_app=WebAppInfo(url="https://example.com"))],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")],
    ])

def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]])

@dp.message(CommandStart())
async def start_cmd(message: Message) -> None:
    save_user_id(message.from_user.id)
    await message.answer("Привет! Выбери действие:", reply_markup=main_keyboard())

@dp.callback_query(F.data == "menu")
async def cb_menu(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer("Главное меню", reply_markup=main_keyboard())

@dp.callback_query(F.data == "menu_help")
async def cb_help(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer("Команды: /analyze BTC /gems /social /scam /signals /sniper", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_analyze")
async def cb_analyze_hint(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer("Напиши: /analyze BTC", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_gems")
async def cb_gems(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer("Сканирую...")
    msg = await query.message.answer("💎 Ищу gem'ы...")
    try:
        await msg.edit_text(await analyzer.find_new_potential(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_overbought")
async def cb_overbought(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer("Сканирую...")
    msg = await query.message.answer("🔥 Ищу перекупленные...")
    try:
        await msg.edit_text(await analyzer.find_overbought(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_scam")
async def cb_scam(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer("Сканирую...")
    msg = await query.message.answer("🚨 Проверяю рынок...")
    try:
        await msg.edit_text(await analyzer.find_scam_whales(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_social")
async def cb_social(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer("Сканирую...")
    msg = await query.message.answer("📡 Сканирую соцсигналы...")
    try:
        await msg.edit_text(await social_scanner.find_social_gems(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_new")
async def cb_new(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer("Сканирую...")
    msg = await query.message.answer("🆕 Ищу новые монеты...")
    try:
        await msg.edit_text(await analyzer.find_new_potential(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_sniper_status")
async def cb_sniper_status(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer(get_sniper_status_text(), parse_mode="Markdown", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_top_signals")
async def cb_top_signals(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer("Сканирую...")
    msg = await query.message.answer("📈 Подбираю сигналы...")
    try:
        await msg.edit_text(await analyzer.top_signals(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_risk_drop")
async def cb_risk_drop(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer()
    msg = await query.message.answer("⚠️ Ищу риск падения...")
    try:
        await msg.edit_text(await analyzer.find_scam_whales(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "menu_alerts")
async def cb_alerts(query: CallbackQuery) -> None:
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer("🔔 Алерты активны. Управление будет расширено.", reply_markup=back_keyboard())

@dp.message(Command("analyze"))
async def cmd_analyze(message: Message) -> None:
    save_user_id(message.from_user.id)
    sym = ((message.text or "").split(maxsplit=1) + ["BTC"])[1].strip().upper()
    wait = await message.answer(f"📊 Анализ {sym}...")
    try:
        await wait.edit_text(await analyzer.full_analysis(sym), parse_mode="Markdown")
    except Exception as e:
        await wait.edit_text(f"❌ Ошибка: {str(e)[:120]}")

@dp.message(Command("gems"))
async def cmd_gems(message: Message) -> None:
    save_user_id(message.from_user.id)
    msg = await message.answer("💎 Ищу гемы...")
    try:
        await msg.edit_text(await analyzer.find_new_potential(), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}")

@dp.message(Command("social"))
async def cmd_social(message: Message) -> None:
    save_user_id(message.from_user.id)
    msg = await message.answer("📡 Сканирую соцсигналы...")
    try:
        await msg.edit_text(await social_scanner.find_social_gems(), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}")

@dp.message(Command("scam"))
async def cmd_scam(message: Message) -> None:
    save_user_id(message.from_user.id)
    msg = await message.answer("🚨 Сканирую скам...")
    try:
        await msg.edit_text(await analyzer.find_scam_whales(), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}")

@dp.message(Command("signals"))
async def cmd_signals(message: Message) -> None:
    save_user_id(message.from_user.id)
    msg = await message.answer("📈 Ищу сигналы...")
    try:
        await msg.edit_text(await analyzer.top_signals(), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}")

@dp.message(Command("sniper"))
async def cmd_sniper(message: Message) -> None:
    save_user_id(message.from_user.id)
    await message.answer(get_sniper_status_text(), parse_mode="Markdown")

@dp.message()
async def free_symbol(message: Message) -> None:
    save_user_id(message.from_user.id)
    txt = (message.text or "").strip()
    if not txt or txt.startswith("/"):
        return
    sym = txt.split()[0].upper()
    msg = await message.answer(f"📊 Анализирую {sym}...")
    try:
        await msg.edit_text(await analyzer.full_analysis(sym), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:120]}")

async def auto_broadcast_loop(bot: Bot) -> None:
    while True:
        try:
            users = sorted(load_users())
            if users:
                gems = await analyzer.find_gems()
                social = await social_scanner.find_social_gems_data()
                scams = await analyzer.find_scam_whales_results()
                lines = [f"🔔 *АВТО-СКАНЕР* — {time.strftime('%H:%M:%S')}", "", "🟢 *ТОП СИГНАЛЫ НА ПОКУПКУ:*"]
                for i, g in enumerate(gems[:3], start=1):
                    p = float(g.get("current_price") or 0)
                    lines.append(f"{i}. {str(g.get('symbol','')).upper()} — Вход ${p:.6f} | Стоп ${p*0.93:.6f} | Цель ${p*1.12:.6f}")
                lines.extend(["", "📡 *СОЦИАЛЬНЫЕ ГЕМЫ:*"])
                for g in social[:2]:
                    lines.append(f"- {g.get('symbol')} — Score {g.get('social_score')}, {g.get('potential')}")
                lines.extend(["", "🚨 *СКАМ ПРЕДУПРЕЖДЕНИЕ:*"])
                if scams:
                    s = scams[0]
                    lines.append(f"- {s.get('symbol')} — Риск {s.get('risk')}%, памп {s.get('ch7',0):+.0f}% за 7д, теперь дамп")
                lines.extend(["", "📱 Подробный анализ: напиши символ монеты"])
                text = "\n".join(lines)
                for uid in users:
                    try:
                        await bot.send_message(uid, text, parse_mode="Markdown")
                    except Exception:
                        pass
        except Exception as e:
            logger.exception("auto broadcast error: %s", e)
        await asyncio.sleep(6 * 3600)

async def main() -> None:
    ensure_users_file()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в окружении.")
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(scanner_loop(bot))
    asyncio.create_task(auto_broadcast_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        await analyzer.close()
        await social_scanner.close()

if __name__ == "__main__":
    asyncio.run(main())
