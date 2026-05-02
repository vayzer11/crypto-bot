from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import json

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from analysis import CryptoAnalyzer
from sniper import scanner_loop

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "").strip()
USERS_FILE = Path(__file__).resolve().parent / "users.json"
analyzer = CryptoAnalyzer()

SUPPORTED_CHAINS = {
    "ethereum": {"name": "Ethereum", "goplus": "1"},
    "bsc":      {"name": "BSC",      "goplus": "56"},
    "solana":   {"name": "Solana",   "goplus": None},
    "base":     {"name": "Base",     "goplus": "8453"},
    "arbitrum": {"name": "Arbitrum", "goplus": "42161"},
    "polygon":  {"name": "Polygon",  "goplus": "137"},
}

MEME_KEYWORDS = ("pepe", "doge", "inu", "meme", "cat", "frog", "elon", "moon", "shib", "bonk", "wojak", "baby", "safe")
DEFI_KEYWORDS = ("swap", "dex", "yield", "farm", "lending", "vault", "staked", "finance", "protocol", "dao")

dp = Dispatcher()


# ═══ USERS ═══════════════════════════════════════════════════════

def ensure_users_file() -> None:
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")

def load_users() -> set[int]:
    ensure_users_file()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return set()
        return {int(x) for x in data if str(x).isdigit()}
    except Exception:
        return set()


def save_user_id(user_id: int) -> None:
    ensure_users_file()
    users = load_users()
    uid = int(user_id)
    if uid not in users:
        users.add(uid)
        try:
            USERS_FILE.write_text(
                json.dumps(sorted(users), ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            USERS_FILE.write_text(json.dumps([uid], ensure_ascii=False), encoding="utf-8")


# ═══ HELPERS ═════════════════════════════════════════════════════

def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _to_int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None

def _to_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    txt = str(v).strip().lower()
    if txt in {"1", "true", "yes"}:
        return True
    if txt in {"0", "false", "no"}:
        return False
    return None

def _age_hours(created_at_ms: Any) -> float:
    if not created_at_ms:
        return 999.0
    return max((int(time.time() * 1000) - int(created_at_ms)) / 3_600_000, 0.0)

def _fmt(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v/1e9:.2f}B"
    if v >= 1_000_000:
        return f"${v/1e6:.2f}M"
    if v >= 1_000:
        return f"${v/1000:.1f}K"
    return f"${v:,.0f}"


# ═══ TOKEN ═══════════════════════════════════════════════════════

@dataclass
class Token:
    chain: str
    name: str
    symbol: str
    contract: str
    age_hours: float
    holders: Optional[int]
    liquidity: float
    market_cap: float
    volume_24h: float
    volume_1h: float
    buy_tax: Optional[float]
    sell_tax: Optional[float]
    honeypot: Optional[bool]
    mintable: Optional[bool]
    blacklist: Optional[bool]
    lp_locked: Optional[bool]
    dex_url: str
    category: str
    risk_score: int = 0


# ═══ SCREENER ════════════════════════════════════════════════════

class Screener:
    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

    async def _get(self, url: str, params: dict = None, headers: dict = None) -> Any:
        for attempt in range(3):
            try:
                async with self.session.get(url, params=params, headers=headers) as r:
                    if r.status == 429:
                        await asyncio.sleep(2 + attempt)
                        continue
                    if r.status >= 400:
                        return None
                    return await r.json(content_type=None)
            except Exception:
                if attempt == 2:
                    return None
                await asyncio.sleep(1 + attempt)
        return None

    async def _candidates(self) -> list[dict]:
        profiles = await self._get("https://api.dexscreener.com/token-profiles/latest/v1") or []
        boosts = await self._get("https://api.dexscreener.com/token-boosts/latest/v1") or []
        seen: dict[tuple, dict] = {}
        for row in profiles + boosts:
            chain = str(row.get("chainId", "")).lower()
            addr = str(row.get("tokenAddress", "")).lower()
            if chain in SUPPORTED_CHAINS and addr:
                seen[(chain, addr)] = row
        return list(seen.values())[:100]

    async def _pairs(self, address: str) -> list[dict]:
        data = await self._get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        return (data or {}).get("pairs", [])

    async def _goplus(self, chain: str, contract: str) -> dict:
        chain_id = SUPPORTED_CHAINS[chain]["goplus"]
        if not chain_id:
            return {}
        data = await self._get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": contract}
        )
        result = (data or {}).get("result", {})
        return result.get(contract.lower()) or result.get(contract) or {}

    def _classify(self, name: str, symbol: str) -> str:
        text = f"{name} {symbol}".lower()
        if any(k in text for k in MEME_KEYWORDS):
            return "meme"
        if any(k in text for k in DEFI_KEYWORDS):
            return "defi"
        return "other"

    def _calc_risk(self, t: Token) -> int:
        score = 0
        if t.honeypot is True:
            score += 40
        if t.mintable is True:
            score += 20
        if t.blacklist is True:
            score += 15
        if t.buy_tax is not None and t.buy_tax > 10:
            score += 15
        if t.sell_tax is not None and t.sell_tax > 10:
            score += 15
        if t.lp_locked is False:
            score += 10
        if t.holders is not None and t.holders < 100:
            score += 10
        return min(score, 100)

    async def _build(self, pair: dict) -> Optional[Token]:
        chain = str(pair.get("chainId", "")).lower()
        if chain not in SUPPORTED_CHAINS:
            return None
        base = pair.get("baseToken") or {}
        contract = str(base.get("address", "")).strip()
        if not contract:
            return None

        sec = await self._goplus(chain, contract)
        holders = _to_int(sec.get("holder_count"))

        buy_tax = _to_float(sec.get("buy_tax"), -1)
        sell_tax = _to_float(sec.get("sell_tax"), -1)

        t = Token(
            chain=SUPPORTED_CHAINS[chain]["name"],
            name=str(base.get("name", "Unknown")),
            symbol=str(base.get("symbol", "UNK")),
            contract=contract,
            age_hours=_age_hours(pair.get("pairCreatedAt")),
            holders=holders,
            liquidity=_to_float((pair.get("liquidity") or {}).get("usd")),
            market_cap=_to_float(pair.get("marketCap") or pair.get("fdv")),
            volume_24h=_to_float((pair.get("volume") or {}).get("h24")),
            volume_1h=_to_float((pair.get("volume") or {}).get("h1")),
            buy_tax=None if buy_tax < 0 else buy_tax,
            sell_tax=None if sell_tax < 0 else sell_tax,
            honeypot=_to_bool(sec.get("is_honeypot")),
            mintable=_to_bool(sec.get("is_mintable")),
            blacklist=_to_bool(sec.get("is_blacklisted")),
            lp_locked=_to_bool(sec.get("lp_locked") or sec.get("is_locked")),
            dex_url=str(pair.get("url", "")),
            category=self._classify(str(base.get("name", "")), str(base.get("symbol", ""))),
        )
        t.risk_score = self._calc_risk(t)
        return t

    async def scan(self, max_age: float = 24) -> list[Token]:
        candidates = await self._candidates()
        results: list[Token] = []
        seen: set[str] = set()

        for c in candidates:
            addr = str(c.get("tokenAddress", "")).lower()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            try:
                pairs = await self._pairs(addr)
            except Exception:
                continue
            valid = [p for p in pairs if str((p or {}).get("chainId", "")).lower() in SUPPORTED_CHAINS]
            if not valid:
                continue
            best = max(valid, key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")))
            try:
                tok = await self._build(best)
            except Exception:
                continue
            if tok and tok.age_hours <= max_age and tok.liquidity >= 5000:
                results.append(tok)

        return results


screener = Screener()


# ═══ FORMAT ══════════════════════════════════════════════════════

def format_token(t: Token) -> str:
    age = f"{t.age_hours:.1f} ч" if t.age_hours >= 1 else f"{int(t.age_hours*60)} мин"
    tax = "N/A" if t.buy_tax is None else f"{t.buy_tax:.0f}% / {t.sell_tax:.0f}%"
    hp = "Нет ✅" if t.honeypot is False else "Да ❌" if t.honeypot else "Неизвестно"
    mint = "Отключен ✅" if t.mintable is False else "Активен ⚠️" if t.mintable else "Неизвестно"
    lp = "Заблокирована 🔒" if t.lp_locked else "Не заблокирована ⚠️" if t.lp_locked is False else "Неизвестно"
    signal = "🟢 СМОТРЕТЬ" if t.risk_score <= 20 else "🟡 ОСТОРОЖНО" if t.risk_score <= 50 else "🔴 ВЫСОКИЙ РИСК"
    holders_str = f"{t.holders:,}" if t.holders else "N/A"

    return (
        f"🆕 *НОВЫЙ ТОКЕН*\n"
        f"📛 Название: {t.name} ({t.symbol})\n"
        f"🌐 Сеть: {t.chain}\n"
        f"⏰ Листинг: {age} назад\n"
        f"👥 Холдеры: {holders_str}\n"
        f"💧 Ликвидность: {_fmt(t.liquidity)}\n"
        f"💰 Капа: {_fmt(t.market_cap)}\n"
        f"📊 Объём 24ч: {_fmt(t.volume_24h)}\n"
        f"🟢 Налог: {tax}\n"
        f"🍯 Honeypot: {hp}\n"
        f"🪙 Mint: {mint}\n"
        f"🔒 Ликвидность: {lp}\n"
        f"📈 Сигнал: {signal}\n"
        f"⚠️ Риск скама: {t.risk_score}/100\n"
        f"🔍 [DexScreener]({t.dex_url})"
    )

def format_scam_alert(t: Token) -> str:
    age = f"{t.age_hours:.1f} ч" if t.age_hours >= 1 else f"{int(t.age_hours*60)} мин"
    return (
        f"🚨 *СКАМ АЛЕРТ — РИСК ДАМПА*\n"
        f"📛 Токен: {t.name} ({t.symbol})\n"
        f"🌐 Сеть: {t.chain}\n"
        f"⏰ Возраст: {age}\n"
        f"💰 Капа: {_fmt(t.market_cap)}\n"
        f"💧 Ликвидность: {_fmt(t.liquidity)}\n"
        f"💣 Риск дампа: {t.risk_score}/100\n"
        f"🍯 Honeypot: {'Да ❌' if t.honeypot else 'Нет'}\n"
        f"🪙 Mint: {'Активен ⚠️' if t.mintable else 'Нет'}\n"
        f"📉 РЕКОМЕНДАЦИЯ: НЕ ПОКУПАТЬ\n"
        f"🔍 [DexScreener]({t.dex_url})"
    )


# ═══ KEYBOARDS ═══════════════════════════════════════════════════

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🆕 Новые", callback_data="scan:new"),
            InlineKeyboardButton(text="🌍 Все токены", callback_data="scan:all"),
        ],
        [
            InlineKeyboardButton(text="🐸 Мем-коины", callback_data="scan:meme"),
            InlineKeyboardButton(text="🏦 DeFi", callback_data="scan:defi"),
        ],
        [
            InlineKeyboardButton(text="🔥 Топ по объёму", callback_data="scan:hot"),
            InlineKeyboardButton(text="✅ Безопасные", callback_data="scan:safe"),
        ],
        [
            InlineKeyboardButton(text="🚨 Скам алерты", callback_data="scan:scam"),
            InlineKeyboardButton(text="🎯 Снайпер", callback_data="scan:snipe"),
        ],
        [
            InlineKeyboardButton(text="🚨 Скам-детектор", callback_data="menu_scam_hunter"),
        ],
        [
            InlineKeyboardButton(text="🔍 Проверить контракт", callback_data="help:check"),
        ],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
        ]
    )

def token_keyboard(t: Token) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh:{t.chain.lower()}:{t.contract}"),
            InlineKeyboardButton(text="🔍 Проверить", callback_data=f"check:{t.chain.lower()}:{t.contract}"),
        ],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
    ])


# ═══ SCAN LOGIC ══════════════════════════════════════════════════

async def do_scan(mode: str) -> list[Token]:
    tokens = await screener.scan(max_age=24)

    if mode == "new":
        tokens = [t for t in tokens if t.age_hours <= 6]
    elif mode == "meme":
        tokens = [t for t in tokens if t.category == "meme"]
    elif mode == "defi":
        tokens = [t for t in tokens if t.category == "defi"]
    elif mode == "safe":
        tokens = [t for t in tokens if t.risk_score <= 20 and not t.honeypot]
    elif mode == "scam":
        tokens = [t for t in tokens if t.risk_score >= 60]
        tokens.sort(key=lambda x: x.risk_score, reverse=True)
    elif mode == "hot":
        tokens.sort(key=lambda x: x.volume_1h, reverse=True)
    elif mode == "snipe":
        tokens = [t for t in tokens if t.age_hours <= 1]
        tokens.sort(key=lambda x: x.age_hours)

    return tokens[:8]


async def send_scan(message: Message, mode: str) -> None:
    waiting = await message.answer("🔍 Сканирую все сети...")
    try:
        tokens = await do_scan(mode)
    except Exception as e:
        logger.exception("scan error")
        await waiting.edit_text(f"❌ Ошибка: {str(e)[:150]}")
        return

    if not tokens:
        await waiting.edit_text(
            "Токенов не найдено по текущим фильтрам.\n"
            "Попробуй /scan или /all для всех токенов."
        )
        return

    fmt_fn = format_scam_alert if mode == "scam" else format_token
    first = tokens[0]
    await waiting.edit_text(
        fmt_fn(first),
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=token_keyboard(first)
    )
    for t in tokens[1:3]:
        await message.answer(
            fmt_fn(t),
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=token_keyboard(t)
        )


# ═══ HANDLERS ════════════════════════════════════════════════════

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    save_user_id(message.from_user.id)
    await message.answer(
        "🚀 *CRYPTO Screener Bot*\n\n"
        "Сканирую: Ethereum, BSC, Solana, Base, Arbitrum, Polygon\n"
        "Нахожу новые токены, мем-коины, DeFi и скам монеты!\n\n"
        "Выбери действие:",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

@dp.callback_query(F.data == "menu")
async def cb_menu(query: CallbackQuery) -> None:
    await query.answer()
    save_user_id(query.from_user.id)
    await query.message.answer("Главное меню:", reply_markup=main_keyboard())

@dp.callback_query(F.data.startswith("scan:"))
async def cb_scan(query: CallbackQuery) -> None:
    await query.answer("Сканирую...")
    save_user_id(query.from_user.id)
    mode = query.data.split(":", 1)[1]
    await send_scan(query.message, mode)

@dp.callback_query(F.data == "menu_scam_hunter")
async def cb_scam_hunter(query: CallbackQuery) -> None:
    await query.answer("Сканирую...")
    save_user_id(query.from_user.id)
    msg = await query.message.answer("🔍 Ищу скам монеты с большим капиталом...")
    try:
        result = await analyzer.find_scam_whales()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())

@dp.callback_query(F.data == "help:check")
async def cb_help_check(query: CallbackQuery) -> None:
    await query.answer()
    save_user_id(query.from_user.id)
    await query.message.answer(
        "Использование: `/check ethereum 0x...`\n"
        "Поддерживаемые сети: ethereum, bsc, solana, base, arbitrum, polygon",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("check:"))
async def cb_check(query: CallbackQuery) -> None:
    await query.answer("Проверяю...")
    save_user_id(query.from_user.id)
    _, chain, contract = query.data.split(":", 2)
    msg = await query.message.answer("🧪 Проверяю контракт...")
    pairs = await screener._pairs(contract)
    valid = [p for p in pairs if str((p or {}).get("chainId", "")).lower() in SUPPORTED_CHAINS]
    if not valid:
        await msg.edit_text("❌ Контракт не найден.")
        return
    best = max(valid, key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")))
    tok = await screener._build(best)
    if tok:
        await msg.edit_text(format_token(tok), parse_mode="Markdown", disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("refresh:"))
async def cb_refresh(query: CallbackQuery) -> None:
    await query.answer("Обновляю...")
    save_user_id(query.from_user.id)
    _, chain, contract = query.data.split(":", 2)
    pairs = await screener._pairs(contract)
    valid = [p for p in pairs if str((p or {}).get("chainId", "")).lower() in SUPPORTED_CHAINS]
    if not valid:
        await query.message.answer("❌ Токен не найден.")
        return
    best = max(valid, key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")))
    tok = await screener._build(best)
    if tok:
        await query.message.edit_text(
            format_token(tok),
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=token_keyboard(tok)
        )

@dp.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    save_user_id(message.from_user.id)
    await send_scan(message, "all")

@dp.message(Command("new"))
async def cmd_new(message: Message) -> None:
    save_user_id(message.from_user.id)
    await send_scan(message, "new")

@dp.message(Command("meme"))
async def cmd_meme(message: Message) -> None:
    save_user_id(message.from_user.id)
    await send_scan(message, "meme")

@dp.message(Command("defi"))
async def cmd_defi(message: Message) -> None:
    save_user_id(message.from_user.id)
    await send_scan(message, "defi")

@dp.message(Command("hot"))
async def cmd_hot(message: Message) -> None:
    save_user_id(message.from_user.id)
    await send_scan(message, "hot")

@dp.message(Command("safe"))
async def cmd_safe(message: Message) -> None:
    save_user_id(message.from_user.id)
    await send_scan(message, "safe")

@dp.message(Command("scam"))
async def scam_command(message: Message) -> None:
    save_user_id(message.from_user.id)
    msg = await message.answer("🔍 Сканирую скам монеты...")
    try:
        result = await analyzer.find_scam_whales()
        await msg.edit_text(result, parse_mode="Markdown", reply_markup=back_keyboard())
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}", reply_markup=back_keyboard())


@dp.message(Command("analyze"))
async def cmd_analyze(message: Message) -> None:
    save_user_id(message.from_user.id)
    parts = (message.text or "").split(maxsplit=1)
    sym = parts[1].strip().upper() if len(parts) > 1 else "BTC"
    wait = await message.answer(f"📊 Анализ {sym}...")
    try:
        text = await analyzer.full_analysis(sym)
        await wait.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        await wait.edit_text(f"❌ Ошибка: {str(e)[:200]}")

@dp.message(Command("snipe"))
async def cmd_snipe(message: Message) -> None:
    save_user_id(message.from_user.id)
    await send_scan(message, "snipe")

@dp.message(Command("check"))
async def cmd_check(message: Message) -> None:
    save_user_id(message.from_user.id)
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: `/check ethereum 0x...`", parse_mode="Markdown")
        return
    chain, contract = parts[1].lower(), parts[2].strip()
    if chain not in SUPPORTED_CHAINS:
        await message.answer("Сети: ethereum, bsc, solana, base, arbitrum, polygon")
        return
    msg = await message.answer("🧪 Проверяю контракт...")
    pairs = await screener._pairs(contract)
    valid = [p for p in pairs if str((p or {}).get("chainId", "")).lower() in SUPPORTED_CHAINS]
    if not valid:
        await msg.edit_text("❌ Контракт не найден в DexScreener.")
        return
    best = max(valid, key=lambda p: _to_float((p.get("liquidity") or {}).get("usd")))
    tok = await screener._build(best)
    if tok:
        await msg.edit_text(format_token(tok), parse_mode="Markdown", disable_web_page_preview=True)


# ═══ MAIN ════════════════════════════════════════════════════════

async def main() -> None:
    ensure_users_file()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Добавь в Railway Variables или .env файл")
    await screener.start()
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(scanner_loop(bot))
    logger.info("Bot started!")
    try:
        await dp.start_polling(bot)
    finally:
        if screener.session:
            await screener.session.close()
        await analyzer.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        logger.error(str(e))
        raise SystemExit(1)
