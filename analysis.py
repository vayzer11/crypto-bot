from __future__ import annotations

import asyncio
import json
import os
import random
import time
from typing import Any

import aiohttp

try:
    from _symbol_map_generated import SYMBOL_MAP_COINGECKO as _SYMBOL_BASE
except Exception:
    _SYMBOL_BASE = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}

# Дополнение к карте: мемы, AI, DeFi, L2, экосистемы (CoinGecko id)
SYMBOL_MAP_EXTRA: dict[str, str] = {
    "LADYS": "milady-meme-coin",
    "WOJAK": "wojak-2",
    "CHAD": "chad-coin",
    "BILLY": "billy-2",
    "SLERF": "slerf",
    "ZERO": "zerolend",
    "GIGA": "giga-2",
    "PONKE": "ponke-sol",
    "MYRO": "myro-2",
    "SILLY": "silly-dragon",
    "RETARDIO": "retardio",
    "MICHI": "michi-2",
    "BOOK": "book-of-crypto",
    "SIGMA": "sigma-3",
    "MANEKI": "maneki",
    "PORK": "pork-2",
    "CHILLGUY": "chill-guy",
    "FWOG": "fwog",
    "GORK": "gork",
    "GOAT": "goatseus-maximus",
    "ACT": "act-i-the-ai-prophecy",
    "BANANA": "banana-gun",
    "CTXC": "cortex",
    "OLAS": "autonolas",
    "COVALENT": "covalent",
    "RDNT": "radiant-capital",
    "VELA": "vela-token",
    "LISTA": "lista-dao",
    "USUAL": "usual",
    "MKUSD": "prisma-mkusd",
    "MATIC": "polygon-ecosystem-token",
    "BOBA": "boba-network",
    "METIS": "metis-token",
    "SCROLL": "scroll",
    "TAIKO": "taiko",
    "MODE": "mode",
    "KROMA": "kroma",
    "STEP": "step-finance",
    "COPE": "cope",
    "MEDIA": "media-network",
    "HONEY": "hivemapper",
    "TULIP": "tulip-protocol",
    "PORT": "port-finance",
    "LARIX": "larix",
    "PIXEL": "pixels",
    "PORTAL": "portal-2",
    "MAVIA": "heroes-of-mavia",
    "SAGA": "saga-2",
    "NAKA": "nakamoto-games",
    "HERO": "hero-blaze-three-kingdoms",
    "PVU": "plant-vs-undead",
    "DREAMS": "dreams-quest",
    "DOGS": "dogs-2",
    "HMSTR": "hamster-kombat",
    "CATI": "catizen",
    "MAJOR": "major",
    "BLUM": "blum-2",
    "GRAM": "gram-2",
    "DUREV": "durov",
    "RESISTANCE": "resistance-dog",
    "AEVO": "aevo",
    "WEN": "wen-4",
    "JITO": "jito-governance-token",
    "DRIFT": "drift-protocol",
    "MARGINFI": "marginfi",
    "DIA": "dia-data",
    "NEST": "nest",
    "FLUX": "zelcash",
    "POWR": "power-ledger",
    "POWER": "power-2",
    "LUSD": "liquity-usd",
    "AGIX": "singularitynet",
    "OCEAN": "ocean-protocol",
    "SUSHI": "sushi",
    "1INCH": "1inch",
    "GNS": "gains-network",
    "YFI": "yearn-finance",
    "COMP": "compound-governance-token",
    "MKR": "maker",
    "UNI": "uniswap",
    "AAVE": "aave",
    "CRV": "curve-dao-token",
    "SNX": "havven",
    "IMX": "immutable-x",
    "ARB": "arbitrum",
    "OP": "optimism",
    "STRK": "starknet",
    "MANTA": "manta-network",
    "ZKSYNC": "zksync",
    "BLAST": "blast",
    "LINEA": "linea",
    "BEAM": "beam-2",
    "RON": "ronin",
    "MAGIC": "magic",
    "YGG": "yield-guild-games",
    "NOT": "notcoin",
    "REZ": "renzo",
    "OMNI": "omni-network",
    "SAFE": "safe",
    "ETHFI": "ether-fi",
    "EIGEN": "eigenlayer",
    "ONDO": "ondo-finance",
    "ZRO": "layerzero",
    "COW": "cow-protocol",
    "FLUID": "instadapp",
    "FRAX": "frax",
    "CRVUSD": "crvusd",
    "SKY": "sky",
    "API3": "api3",
    "BAND": "band-protocol",
    "TELLOR": "tellor",
    "UMA": "uma",
    "ANKR": "ankr",
    "STORJ": "storj",
    "SIA": "siacoin",
    "FILECOIN": "filecoin",
    "AR": "arweave",
    "THETA": "theta-token",
    "TFUEL": "theta-fuel",
    "LINK": "chainlink",
    "RENDER": "render-token",
    "FET": "fetch-ai",
    "WLD": "worldcoin-wld",
    "AIOZ": "aioz-network",
    "VIRTUAL": "virtual-protocol",
    "ARKM": "arkham",
    "GRT": "the-graph",
    "NMR": "numeraire",
    "PAAL": "paal-ai",
    "PRIME": "hastra-prime",
    "CGPT": "chaingpt",
    "TRIAS": "trias-token",
    "ALT": "altlayer",
    "TAO": "bittensor",
    "JUP": "jupiter-exchange-solana",
    "PYTH": "pyth-network",
    "JTO": "jito-governance-token",
    "ORCA": "orca",
    "RAY": "raydium",
    "MNGO": "mango-markets",
    "SAMO": "samoyedcoin",
    "ATLAS": "star-atlas",
    "POLIS": "star-atlas-dao",
    "SHDW": "genesysgo-shadow",
    "SLND": "solend",
    "ILV": "illuvium",
    "AXS": "axie-infinity",
    "SAND": "the-sandbox",
    "MANA": "decentraland",
    "GALA": "gala",
    "KMNO": "kamino",
    "METEORA": "meteora",
    "KAMINO": "kamino",
    "ZETA": "zetachain",
    "CETUS": "cetus-protocol",
}

SYMBOL_MAP: dict[str, str] = {**_SYMBOL_BASE, **SYMBOL_MAP_EXTRA}

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calc_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(x, 0) for x in deltas]
    losses = [abs(min(x, 0)) for x in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for idx in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calc_ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    multiplier = 2 / (period + 1)
    values = [sum(prices[:period]) / period]
    for p in prices[period:]:
        values.append((p - values[-1]) * multiplier + values[-1])
    return values


def calc_macd(prices: list[float]) -> tuple[float | None, float | None, float | None]:
    e12 = calc_ema(prices, 12)
    e26 = calc_ema(prices, 26)
    if not e12 or not e26:
        return None, None, None
    size = min(len(e12), len(e26))
    line = [a - b for a, b in zip(e12[-size:], e26[-size:])]
    signal = calc_ema(line, 9)
    if not signal:
        return line[-1], None, None
    hist = line[-1] - signal[-1]
    return round(line[-1], 6), round(signal[-1], 6), round(hist, 6)


def calc_bollinger(
    prices: list[float], period: int = 20, std_mult: float = 2.0
) -> tuple[float | None, float | None, float | None]:
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = variance**0.5
    return round(mean - std_mult * std, 6), round(mean, 6), round(mean + std_mult * std, 6)


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v/1e9:.2f}B"
    if v >= 1_000_000:
        return f"${v/1e6:.2f}M"
    if v >= 1_000:
        return f"${v/1e3:.2f}K"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.8f}"


def _fmt_price(v: float) -> str:
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.8f}"


def _rsi_label(rsi: float) -> str:
    if rsi < 30:
        return "перепродан"
    if rsi > 70:
        return "перекуплен"
    return "нейтрально"


def _vol_mcap_assessment(ratio_pct: float) -> str:
    if ratio_pct < 2:
        return "низкая активность"
    if ratio_pct < 8:
        return "нормально"
    if ratio_pct < 20:
        return "повышенный интерес"
    return "аномально высокий"


class CryptoAnalyzer:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}
        self._session: aiohttp.ClientSession | None = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None, ttl: int = 120) -> Any:
        key = f"{url}:{json.dumps(params or {}, sort_keys=True)}"
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1]
        session = await self._session_get()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(2 + attempt * 2)
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                    self._cache[key] = (time.time(), data)
                    return data
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.8 + attempt)
        raise RuntimeError(f"API error: {last_error}")

    async def groq_chat(self, system: str, user: str, max_tokens: int = 256) -> str:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return "API ключ Groq не задан. Добавьте GROQ_API_KEY в окружение."
        session = await self._session_get()
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.6,
        }
        try:
            async with session.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status >= 400:
                    return "Не удалось получить ответ Groq."
                data = await resp.json(content_type=None)
            choices = data.get("choices") or []
            if not choices:
                return "Пустой ответ модели."
            return str(choices[0].get("message", {}).get("content", "")).strip()
        except Exception:
            return "Ошибка сети при обращении к Groq."

    def scam_risk(self, security: dict[str, Any]) -> dict[str, Any]:
        risk = 0
        flags: list[str] = []
        if security.get("is_honeypot"):
            risk += 50
            flags.append("honeypot")
        if security.get("is_mintable"):
            risk += 15
            flags.append("mint")
        if security.get("is_blacklisted"):
            risk += 15
            flags.append("blacklist")
        if security.get("buy_tax") not in (None, 0):
            risk += 10
            flags.append("buy-tax")
        if security.get("sell_tax") not in (None, 0):
            risk += 10
            flags.append("sell-tax")
        if not security.get("lp_locked"):
            risk += 10
            flags.append("lp-unlocked")
        if not security.get("is_open_source"):
            risk += 10
            flags.append("unverified")
        return {"risk_score": min(100, risk), "flags": flags}

    async def indicator_snapshot(self, symbol: str) -> dict[str, Any]:
        coin_id = SYMBOL_MAP.get(symbol.upper(), symbol.lower())
        data = await self._get_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": 90},
            ttl=180,
        )
        prices = [safe_float(x[1]) for x in data.get("prices", [])]
        rsi = calc_rsi(prices)
        macd, macd_signal, macd_hist = calc_macd(prices)
        bb_low, bb_mid, bb_high = calc_bollinger(prices)
        ema20 = calc_ema(prices, 20)
        ema50 = calc_ema(prices, 50)
        return {
            "symbol": symbol.upper(),
            "coin_id": coin_id,
            "rsi": rsi,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "bb_low": bb_low,
            "bb_mid": bb_mid,
            "bb_high": bb_high,
            "ema20": ema20[-1] if ema20 else None,
            "ema50": ema50[-1] if ema50 else None,
            "prices_count": len(prices),
            "last_price": prices[-1] if prices else 0.0,
        }

    def _composite_signal(self, rsi: float, macd: float | None, hist: float | None, ema20: float | None, ema50: float | None, price: float) -> tuple[int, str, str, list[str]]:
        reasons: list[str] = []
        score = 50
        if rsi < 35:
            score += 15
            reasons.append("RSI в зоне перепроданности — возможен отскок")
        elif rsi > 70:
            score -= 15
            reasons.append("RSI перекуплен — риск коррекции")
        if macd is not None and hist is not None:
            if hist > 0 and macd > 0:
                score += 10
                reasons.append("MACD бычий, гистограмма положительна")
            elif hist < 0 and macd < 0:
                score -= 10
                reasons.append("MACD медвежий импульс")
        trend = "NEUTRAL"
        if ema20 and ema50:
            if price > ema20 > ema50:
                trend = "BULL"
                score += 8
                reasons.append("Цена выше EMA20/50 — восходящий тренд")
            elif price < ema20 < ema50:
                trend = "BEAR"
                score -= 8
                reasons.append("Цена ниже EMA20/50 — нисходящий тренд")
        score = max(0, min(100, score))
        if score >= 65:
            emoji, label = "🟢", "ПОКУПКА"
        elif score <= 40:
            emoji, label = "🔴", "ПРОДАЖА / осторожно"
        else:
            emoji, label = "🟡", "НЕЙТРАЛЬНО / ждать"
        if len(reasons) < 3:
            reasons.append("Следите за объёмом и новостями по активу")
        if len(reasons) < 3:
            reasons.append("Используйте стоп-лосс и не рискуйте всем депозитом")
        return score, emoji, label, reasons[:5]

    async def full_analysis(self, symbol: str) -> str:
        sym = symbol.strip().upper()
        coin_id = SYMBOL_MAP.get(sym, symbol.lower())
        try:
            detail = await self._get_json(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "false",
                    "developer_data": "false",
                },
                ttl=120,
            )
        except Exception as e:
            return f"❌ Не удалось загрузить {sym}: {str(e)[:120]}"

        md = detail.get("market_data") or {}
        name = detail.get("name", sym)
        sym_real = (detail.get("symbol") or sym).upper()
        rank = md.get("market_cap_rank")
        rank_s = f"#{rank}" if rank else "#—"
        price = safe_float(md.get("current_price", {}).get("usd"))
        ch1h = safe_float((md.get("price_change_percentage_1h_in_currency") or {}).get("usd"))
        ch24 = safe_float(md.get("price_change_percentage_24h"))
        ch7 = safe_float((md.get("price_change_percentage_7d_in_currency") or {}).get("usd"))
        ch30 = safe_float((md.get("price_change_percentage_30d_in_currency") or {}).get("usd"))
        mcap = safe_float(md.get("market_cap", {}).get("usd"))
        vol24 = safe_float(md.get("total_volume", {}).get("usd"))
        ath = safe_float(md.get("ath", {}).get("usd"))
        ath_ch = safe_float(md.get("ath_change_percentage", {}).get("usd"))

        snap = await self.indicator_snapshot(sym_real)
        rsi = snap["rsi"]
        macd, hist = snap["macd"], snap["macd_hist"]
        bb_low, bb_mid, bb_high = snap["bb_low"], snap["bb_mid"], snap["bb_high"]
        ema20, ema50 = snap["ema20"], snap["ema50"]
        last_p = snap["last_price"] or price

        macd_dir = "вверх" if hist and hist > 0 else "вниз" if hist and hist < 0 else "флэт"
        hist_s = f"{hist}" if hist is not None else "—"
        if bb_low and bb_high and last_p:
            if last_p >= bb_high:
                bb_pos = "у верхней полосы"
            elif last_p <= bb_low:
                bb_pos = "у нижней полосы"
            else:
                bb_pos = "внутри канала"
        else:
            bb_pos = "н/д"

        vol_ratio = (vol24 / mcap * 100) if mcap > 0 else 0.0
        assess = _vol_mcap_assessment(vol_ratio)

        fg_val: int | None = None
        fg_label = "—"
        try:
            fg = await self._get_json("https://api.alternative.me/fng/", ttl=300)
            fg_val = int((fg.get("data") or [{}])[0].get("value", 0))
            fg_label = str((fg.get("data") or [{}])[0].get("value_classification", ""))
        except Exception:
            pass

        tvl = 0.0
        try:
            glob = await self._get_json(
                "https://api.coingecko.com/api/v3/global/decentralized_finance_defi", ttl=300
            )
            tvl = safe_float((glob.get("data") or {}).get("total_value_locked", {}).get("usd"))
        except Exception:
            pass

        score, sig_emoji, sig_label, reasons = self._composite_signal(
            rsi, macd, hist, ema20, ema50, last_p
        )

        if ema20 and ema50 and last_p > ema20 > ema50:
            trend_ru = "BULL"
        elif ema20 and ema50 and last_p < ema20 < ema50:
            trend_ru = "BEAR"
        else:
            trend_ru = "БОКОВИК"

        ema20s = _fmt_price(ema20) if ema20 else "—"
        ema50s = _fmt_price(ema50) if ema50 else "—"
        low_r = last_p * 0.97
        high_r = last_p * 1.02
        entry_lo = min(low_r, high_r)
        entry_hi = max(low_r, high_r)
        stop = last_p * 0.93
        tp1 = last_p * 1.05
        tp2 = last_p * 1.12
        tp3 = last_p * 1.22

        groq = await self.groq_chat(
            "Ты криптоаналитик. Отвечай только по-русски, 3 коротких предложения, без Markdown.",
            f"Кратко оцени {name} ({sym_real}): цена {price}, RSI {rsi}, тренд по EMA, суточное изменение {ch24}%. "
            f"Риски и кому может подойти актив.",
            max_tokens=220,
        )

        fg_line = f"😨 Fear & Greed: {fg_val} — {fg_label}" if fg_val is not None else "😨 Fear & Greed: н/д"

        return (
            f"📊 *{name} ({sym_real})* {rank_s}\n"
            f"💰 Цена: {_fmt_price(price)}\n"
            f"📈 1ч: {ch1h:.2f}% | 24ч: {ch24:.2f}% | 7д: {ch7:.2f}% | 30д: {ch30:.2f}%\n"
            f"🏦 Капа: {_fmt_usd(mcap)} | Объём 24ч: {_fmt_usd(vol24)}\n"
            f"📉 От ATH: {ath_ch:.2f}% | ATH: {_fmt_price(ath)}\n\n"
            f"━━━ ТЕХНИЧЕСКИЙ АНАЛИЗ ━━━\n"
            f"RSI (14): {rsi} — {_rsi_label(rsi).upper()}\n"
            f"MACD: {macd_dir.upper()} (гист: {hist_s})\n"
            f"Bollinger: {bb_pos.upper()}\n"
            f"EMA 20: {ema20s} | EMA 50: {ema50s}\n"
            f"Тренд: {trend_ru}\n\n"
            f"━━━ ОБЪЁМ ━━━\n"
            f"Vol/MCap: {vol_ratio:.1f}% ({assess.upper()})\n\n"
            f"━━━ ОНЧЕЙН ━━━\n"
            f"{fg_line}\n"
            f"🏊 TVL: {_fmt_usd(tvl)}\n\n"
            f"━━━ СИГНАЛ (Score: {score}/100) ━━━\n"
            f"{sig_emoji} {sig_label}\n\n"
            f"Причины:\n"
            + "\n".join(f"- {r}" for r in reasons[:3])
            + "\n\n"
            f"━━━ ТОЧКИ ━━━\n"
            f"🎯 Вход: {_fmt_price(entry_lo)} – {_fmt_price(entry_hi)}\n"
            f"🛑 Стоп: {_fmt_price(stop)} (-7%)\n"
            f"✅ TP1: {_fmt_price(tp1)} (+5%)\n"
            f"✅ TP2: {_fmt_price(tp2)} (+12%)\n"
            f"✅ TP3: {_fmt_price(tp3)} (+22%)\n\n"
            f"━━━ AI АНАЛИЗ (Groq) ━━━\n"
            f"🤖 {groq}"
        )

    async def find_new_potential(self) -> str:
        async def trending() -> list[dict[str, Any]]:
            try:
                d = await self._get_json("https://api.coingecko.com/api/v3/search/trending", ttl=60)
                return [x.get("item") or {} for x in (d.get("coins") or [])]
            except Exception:
                return []

        async def movers() -> list[dict[str, Any]]:
            try:
                return await self._get_json(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "percent_change_24h_desc",
                        "per_page": 50,
                        "page": 1,
                        "price_change_percentage": "24h",
                    },
                    ttl=60,
                ) or []
            except Exception:
                return []

        async def vol_page() -> list[dict[str, Any]]:
            try:
                return await self._get_json(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "volume_desc",
                        "per_page": 50,
                        "page": 2,
                    },
                    ttl=60,
                ) or []
            except Exception:
                return []

        t1, t2, t3 = await asyncio.gather(trending(), movers(), vol_page())
        pool: list[tuple[str, dict[str, Any]]] = []

        for it in t1:
            cid = it.get("id")
            if cid:
                pool.append(("trending", it))
        for row in t2:
            pool.append(("gainers", row))
        for row in t3:
            pool.append(("volume", row))

        random.shuffle(pool)
        seen: set[str] = set()
        picked: list[tuple[str, Any]] = []
        for tag, obj in pool:
            if tag == "trending":
                cid = obj.get("id")
                sym = (obj.get("symbol") or "").upper()
            else:
                cid = obj.get("id")
                sym = (obj.get("symbol") or "").upper()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            picked.append((tag, obj))
            if len(picked) >= 10:
                break

        lines = ["🔥 *Новые идеи (CoinGecko)*\n"]
        for tag, obj in picked[:10]:
            if tag == "trending":
                name = obj.get("name", "")
                sym = (obj.get("symbol") or "").upper()
                rank = obj.get("market_cap_rank")
                why = "В тренде поиска CoinGecko — повышенное внимание розницы"
                ch24 = safe_float((obj.get("data") or {}).get("price_change_percentage_24h", {}).get("usd"))
            else:
                name = obj.get("name", "")
                sym = (obj.get("symbol") or "").upper()
                rank = obj.get("market_cap_rank")
                ch24 = safe_float(obj.get("price_change_percentage_24h"))
                mcap = safe_float(obj.get("market_cap"))
                if tag == "gainers":
                    why = f"Сильный рост 24ч (+{ch24:.1f}%) — моментум среди лидеров"
                else:
                    why = f"Высокий объём при капе {_fmt_usd(mcap)} — ликвидность и интерес к паре"

            lines.append(
                f"• *{name}* ({sym}) #{rank or '—'}\n"
                f"  Почему интересно: {why}\n"
                f"  24ч: {ch24:+.2f}%\n"
            )

        return "\n".join(lines)

    def _scam_coin_score(self, c: dict[str, Any]) -> tuple[int, list[str], str]:
        """Возвращает (risk 0-100, признаки, вывод)."""
        mcap = safe_float(c.get("market_cap"))
        vol = safe_float(c.get("total_volume"))
        ch24 = safe_float(c.get("price_change_percentage_24h"))
        ch7 = safe_float(
            c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d")
        )
        rank = c.get("market_cap_rank")
        rank_i = int(rank) if rank is not None else 9999
        vol_mcap = (vol / mcap) if mcap > 0 else 0.0
        reasons: list[str] = []
        patterns: list[str] = []
        score = 30

        if mcap >= 10_000_000 and mcap <= 500_000_000 and ch24 <= -10 and vol_mcap >= 0.15:
            patterns.append("whale_dump")
            score += 28
            reasons.append(f"Капа {_fmt_usd(mcap)}, падение 24ч {ch24:.1f}% при Vol/MCap {vol_mcap*100:.0f}% — возможен выход крупных игроков")

        if 1_000_000 <= mcap <= 100_000_000 and ch7 >= 50 and ch24 < 0 and vol_mcap >= 0.20:
            patterns.append("pump_dump")
            score += 32
            reasons.append(f"Памп +{ch7:.0f}% за 7д, теперь дамп {ch24:.1f}% — типичный разворот")
            reasons.append(f"Объём {vol_mcap*100:.0f}% от капы — повышенная активность на вершине")

        if rank_i > 200 and mcap > 50_000_000 and vol_mcap >= 0.30:
            patterns.append("new_highcap")
            score += 24
            reasons.append(f"Низкий ранг #{rank_i} при капе {_fmt_usd(mcap)} и Vol/MCap {vol_mcap*100:.0f}%")

        if vol_mcap >= 0.5 and mcap >= 10_000_000:
            score += 10
            reasons.append(f"Vol/MCap {vol_mcap*100:.0f}% — аномально для крупной капы")

        score = max(0, min(100, score))
        if "pump_dump" in patterns:
            verdict = "ВЕРОЯТНЫЙ ПАМП-И-ДАМП"
        elif "whale_dump" in patterns:
            verdict = "ПРИЗНАКИ ДАМПА КИТОВ"
        elif "new_highcap" in patterns:
            verdict = "ПОДОЗРИТЕЛЬНО КРУПНАЯ «НОВИНКА»"
        else:
            verdict = "СМЕШАННЫЕ ПРИЗНАКИ"

        if not reasons:
            reasons.append("Недостаточно явных триггеров в текущей выборке")

        return score, reasons[:4], verdict

    async def find_scam_whales_results(self) -> list[dict[str, Any]]:
        try:
            rows = await self._get_json(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": 1,
                    "price_change_percentage": "24h,7d",
                },
                ttl=180,
            )
        except Exception:
            return []

        out: list[dict[str, Any]] = []
        for c in rows or []:
            risk, reasons, verdict = self._scam_coin_score(c)
            if risk < 55:
                continue
            sym = (c.get("symbol") or "").upper()
            out.append(
                {
                    "risk": risk,
                    "symbol": sym,
                    "name": c.get("name", sym),
                    "id": c.get("id", ""),
                    "price": safe_float(c.get("current_price")),
                    "mcap": safe_float(c.get("market_cap")),
                    "vol": safe_float(c.get("total_volume")),
                    "ch24": safe_float(c.get("price_change_percentage_24h")),
                    "ch7": safe_float(
                        c.get("price_change_percentage_7d_in_currency")
                        or c.get("price_change_percentage_7d")
                    ),
                    "reasons": reasons,
                    "verdict": verdict,
                }
            )

        out.sort(key=lambda x: x["risk"], reverse=True)
        return out[:8]

    def format_scam_whales_text(self, coins: list[dict[str, Any]]) -> str:
        if not coins:
            return "✅ *СКАМ-ДЕТЕКТОР*\n\nСейчас нет монет с выраженными паттернами в топ-250 по капитализации."

        blocks: list[str] = ["🚨 *СКАМ-ДЕТЕКТОР: Монеты с признаками манипуляций*\n"]
        for c in coins:
            vm = (c["vol"] / c["mcap"] * 100) if c["mcap"] > 0 else 0
            warn = " ⚠️ АНОМАЛЬНО" if vm > 30 else ""
            blocks.append(
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🔴 *{c['symbol']}* — Риск: {c['risk']}/100\n"
                f"💰 Цена: {_fmt_price(c['price'])}\n"
                f"🏦 Капа: {_fmt_usd(c['mcap'])} | Объём: {_fmt_usd(c['vol'])}\n"
                f"📊 Vol/MCap: {vm:.0f}%{warn}\n"
                f"📈 7д: {c['ch7']:+.1f}% | 24ч: {c['ch24']:+.1f}%\n\n"
                f"🚨 Признаки:\n"
                + "\n".join(f"- {r}" for r in c["reasons"])
                + f"\n\n💡 Вывод: *{c['verdict']}*\n"
                f"⚡ /analyze {c['symbol']} — подробный анализ"
            )
        blocks.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(blocks)

    async def find_scam_whales(self) -> str:
        coins = await self.find_scam_whales_results()
        return self.format_scam_whales_text(coins)
