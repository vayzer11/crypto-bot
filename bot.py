from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from analysis import CryptoAnalyzer
from alerts import AlertManager, check_alerts_loop
from sniper import get_sniper_status_text, scanner_loop

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USERS_FILE = Path(__file__).resolve().parent / "users.json"

analyzer = CryptoAnalyzer()
alert_manager = AlertManager()
dp = Dispatcher()


def save_user_id(user_id: int) -> None:
    try:
        if not USERS_FILE.exists():
            USERS_FILE.write_text("[]", encoding="utf-8")
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
        if user_id not in data:
            data.append(user_id)
            USERS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"save_user_id error: {e}")


def load_users() -> list[int]:
    try:
        if not USERS_FILE.exists():
            return []
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return [int(x) for x in data if str(x).isdigit()]
    except Exception:
        return []


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]])


def main_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Анализ монеты", callback_data="menu_analyze"),
        InlineKeyboardButton(text="🔥 Перекупленные", callback_data="menu_overbought"),
    )
    builder.row(
        InlineKeyboardButton(text="💎 Gem Finder", callback_data="menu_gems"),
        InlineKeyboardButton(text="🚨 Скам-детектор", callback_data="menu_scam"),
    )
    builder.row(
        InlineKeyboardButton(text="📡 Соц. сканер", callback_data="menu_social"),
        InlineKeyboardButton(text="🆕 Новые монеты", callback_data="menu_new"),
    )
    builder.row(
        InlineKeyboardButton(text="🐸 Meme Tracker", callback_data="menu_meme"),
        InlineKeyboardButton(text="🏦 DeFi Tracker", callback_data="menu_defi"),
    )
    builder.row(
        InlineKeyboardButton(text="🤖 AI Tracker", callback_data="menu_ai"),
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Снайпер", callback_data="menu_sniper"),
        InlineKeyboardButton(text="📈 Топ сигналы", callback_data="menu_signals"),
    )
    builder.row(
        InlineKeyboardButton(text="⚠️ Риск падения", callback_data="menu_dump"),
        InlineKeyboardButton(text="🔔 Мои алерты", callback_data="menu_alerts"),
    )
    builder.row(
        InlineKeyboardButton(text="📱 Открыть приложение", web_app=WebAppInfo(url="https://vayzer11.github.io/crypto-bot/"))
    )
    builder.row(InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help"))
    return builder.as_markup()


async def social_scan() -> str:
    results: list[dict] = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/search/trending") as r:
                data = await r.json(content_type=None)
        coins = data.get("coins", [])
        for item in coins[:7]:
            c = item.get("item", {})
            sym = str(c.get("symbol", "")).upper() or "?"
            score = int(c.get("score", 0))
            results.append({
                "symbol": sym,
                "source": "🚀 CoinGecko Trending",
                "score": 70 + score * 4,
                "reason": f"В топе поиска CoinGecko (#{score + 1})",
            })
    except Exception:
        pass
    await asyncio.sleep(1)
    try:
        headers = {"User-Agent": "CryptoSignalBot/1.0"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get("https://www.reddit.com/r/CryptoMoonShots/new.json?limit=25") as r:
                data = await r.json(content_type=None)
        import re

        pat = re.compile(r"\$([A-Z]{2,10})\b|\b([A-Z]{2,6})\b")
        bad = {"THE", "FOR", "AND", "NOT", "NEW", "TOP", "ALL", "HOW", "WHY", "API", "USD", "DEX", "CEX"}
        cnt: dict[str, int] = {}
        for post in (data.get("data", {}).get("children", []) or []):
            title = (post.get("data", {}) or {}).get("title", "")
            for m in pat.findall(title):
                sym = m[0] or m[1]
                if sym in bad:
                    continue
                cnt[sym] = cnt.get(sym, 0) + 1
        for sym, c in sorted(cnt.items(), key=lambda x: x[1], reverse=True)[:5]:
            results.append({
                "symbol": sym,
                "source": "📱 Reddit r/CryptoMoonShots",
                "score": min(95, 40 + c * 15),
                "reason": f"Упоминаний: {c}",
            })
    except Exception:
        pass
    if not results:
        return "📡 *Социальный сканер*\n\n⚠️ Нет данных прямо сейчас. Попробуй через минуту."
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    lines = ["📡 *СОЦИАЛЬНЫЙ СКАНЕР — Что обсуждают прямо сейчас*\n"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, r in enumerate(results[:10]):
        sym = r.get("symbol", "?")
        lines.append(f"\n{medals[i]} *{sym}*\n📡 {r.get('source')}\n💡 {r.get('reason')}\n📊 Score: {int(r.get('score',0))}/100")
        if sym != "?":
            lines.append(f"⚡ /analyze {sym}")
    lines.append("\n\n⚠️ Социальные упоминания ≠ сигнал к покупке. DYOR.")
    return "\n".join(lines)


@dp.message(CommandStart())
async def start(message: Message):
    save_user_id(message.from_user.id)
    await message.answer("🚀 CryptoSignalBot запущен. Выбери действие:", reply_markup=main_keyboard())


@dp.callback_query(F.data == "menu")
async def cb_menu(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer("Главное меню:", reply_markup=main_keyboard())


@dp.callback_query(F.data == "menu_help")
async def cb_help(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer(
        "Команды: /analyze BTC /gems /signals /social /scam /overbought /newcoins /meme /defi /ai",
        reply_markup=back_keyboard(),
    )


@dp.callback_query(F.data == "menu_gems")
async def cb_gems(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("💎 Ищу гемы...")
    msg = await query.message.answer("💎 Анализирую 2000+ монет...\n⏳ Это займёт ~15 секунд")
    try:
        result = await analyzer.find_gems_text()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        logger.error(f"gems error: {e}")
        await msg.edit_text("❌ Ошибка загрузки. Попробуй ещё раз через 30 секунд.", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_scam")
async def cb_scam(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("🚨 Сканирую...")
    msg = await query.message.answer("🔍 Ищу монеты с признаками манипуляций...")
    try:
        result = await analyzer.find_scam_whales()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_social")
async def cb_social(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("📡 Сканирую...")
    msg = await query.message.answer("📡 Анализирую Reddit и CoinGecko...\n⏳ ~20 секунд")
    try:
        result = await social_scan()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_overbought")
async def cb_overbought(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("🔥 Ищу...")
    msg = await query.message.answer("Поиск перекупленных монет...")
    try:
        await msg.edit_text(await analyzer.find_overbought(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_new")
async def cb_new(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("🆕 Ищу...")
    msg = await query.message.answer("Ищу новые идеи...")
    try:
        await msg.edit_text(await analyzer.find_new_coins(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_meme")
async def cb_meme(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("🐸 Загружаю...")
    msg = await query.message.answer("Сканирую meme-token сектор...")
    try:
        await msg.edit_text(await analyzer.meme_tracker(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_defi")
async def cb_defi(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("🏦 Загружаю...")
    msg = await query.message.answer("Получаю TVL из DeFiLlama...")
    try:
        await msg.edit_text(await analyzer.defi_tracker(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_ai")
async def cb_ai(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("🤖 Загружаю...")
    msg = await query.message.answer("Сканирую сектор AI токенов...")
    try:
        await msg.edit_text(await analyzer.ai_tracker(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_signals")
async def cb_signals(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("📈 Считаю...")
    msg = await query.message.answer("Собираю топ сигналы...")
    try:
        await msg.edit_text(await analyzer.top_signals(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_dump")
async def cb_dump(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer("⚠️ Сканирую...")
    msg = await query.message.answer("Ищу риск падения...")
    try:
        await msg.edit_text(await analyzer.find_scam_whales(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_sniper")
async def cb_sniper(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer(get_sniper_status_text(), parse_mode="Markdown", reply_markup=back_keyboard())


@dp.callback_query(F.data == "menu_alerts")
async def cb_alerts(query: CallbackQuery):
    save_user_id(query.from_user.id)
    await query.answer()
    await query.message.answer("🔔 Алерты будут расширены в следующем апдейте.", reply_markup=back_keyboard())


@dp.message(Command("analyze"))
async def cmd_analyze(message: Message):
    save_user_id(message.from_user.id)
    sym = ((message.text or "").split(maxsplit=1) + ["BTC"])[1].strip().upper()
    msg = await message.answer(f"📊 Анализ {sym}...")
    try:
        await msg.edit_text(await analyzer.full_analysis(sym), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("gems"))
async def cmd_gems(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("💎 Ищу gem'ы...")
    try:
        await msg.edit_text(await analyzer.find_gems_text(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("signals"))
async def cmd_signals(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("📈 Ищу сигналы...")
    try:
        await msg.edit_text(await analyzer.top_signals(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("social"))
async def cmd_social(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("📡 Сканирую соцсети...")
    try:
        await msg.edit_text(await social_scan(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("scam"))
async def cmd_scam(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("🚨 Сканирую...")
    try:
        await msg.edit_text(await analyzer.find_scam_whales(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("overbought"))
async def cmd_overbought(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("🔥 Ищу перекупленные...")
    try:
        await msg.edit_text(await analyzer.find_overbought(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("newcoins"))
async def cmd_newcoins(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("🆕 Ищу новые листинги...")
    try:
        await msg.edit_text(await analyzer.find_new_coins(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("meme"))
async def cmd_meme(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("🐸 Загружаю meme tracker...")
    try:
        await msg.edit_text(await analyzer.meme_tracker(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("defi"))
async def cmd_defi(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("🏦 Загружаю DeFiLlama...")
    try:
        await msg.edit_text(await analyzer.defi_tracker(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("ai"))
async def cmd_ai(message: Message):
    save_user_id(message.from_user.id)
    msg = await message.answer("🤖 Сканирую AI-токены...")
    try:
        await msg.edit_text(await analyzer.ai_tracker(), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message()
async def free_text(message: Message):
    save_user_id(message.from_user.id)
    txt = (message.text or "").strip()
    if not txt or txt.startswith("/"):
        return
    sym = txt.split()[0].upper()
    msg = await message.answer(f"📊 Анализирую {sym}...")
    try:
        await msg.edit_text(await analyzer.full_analysis(sym), parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


async def auto_broadcast_loop(bot: Bot) -> None:
    while True:
        try:
            users = load_users()
            if users:
                buys = await analyzer.find_gems()
                social_txt = await social_scan()
                scam = await analyzer.find_scam_whales_results()
                lines = [f"🔔 *АВТО-СКАНЕР* — {time.strftime('%H:%M:%S')}", "", "🟢 *ТОП СИГНАЛЫ НА ПОКУПКУ:*"]
                for i, c in enumerate(buys[:3], 1):
                    p = float(c.get("current_price") or 0)
                    lines.append(f"{i}. {str(c.get('symbol','')).upper()} — Вход {analyzer._fmt_price(p)} | Стоп {analyzer._fmt_price(p*0.93)} | Цель {analyzer._fmt_price(p*1.12)}")
                lines.extend(["", "📡 *СОЦИАЛЬНЫЕ ГЕМЫ:*", social_txt.split("\n")[0]])
                if scam:
                    s = scam[0]
                    lines.extend(["", "🚨 *СКАМ ПРЕДУПРЕЖДЕНИЕ:*", f"- {s.get('symbol')} — Риск {s.get('risk')}%"])
                lines.append("\n📱 Подробный анализ: напиши символ монеты")
                text = "\n".join(lines)
                for uid in users:
                    try:
                        await bot.send_message(uid, text, parse_mode="Markdown")
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("auto broadcast error: %s", e)
        await asyncio.sleep(6 * 3600)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    bot = Bot(token=BOT_TOKEN)  # ONE BOT INSTANCE ONLY
    scanner_task = asyncio.create_task(scanner_loop(bot), name="scanner_loop")
    auto_broadcast_task = asyncio.create_task(auto_broadcast_loop(bot), name="auto_broadcast_loop")
    check_alerts_task = asyncio.create_task(
        check_alerts_loop(bot, analyzer, alert_manager),
        name="check_alerts_loop",
    )
    auto_scan_task: asyncio.Task | None = None
    tasks = [scanner_task, auto_broadcast_task, check_alerts_task]
    if auto_scan_task is not None:
        tasks.append(auto_scan_task)
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            if task and not task.done():
                task.cancel()
        for task in tasks:
            if task:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await analyzer.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
