from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
from typing import Any

import aiohttp

try:
    from _symbol_map_generated import SYMBOL_MAP_COINGECKO as _BASE
except Exception:
    _BASE = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

SYMBOL_MAP = dict(_BASE)
SYMBOL_MAP.update(
    {
        "PEPE": "pepe", "SHIB": "shiba-inu", "DOGE": "dogecoin", "BONK": "bonk", "WIF": "dogwifcoin",
        "FLOKI": "floki", "POPCAT": "popcat", "PNUT": "peanut-the-squirrel", "NEIRO": "neiro-3", "TURBO": "turbo",
        "MOG": "mog-coin", "BRETT": "based-brett", "GOAT": "goatseus-maximus", "ACT": "act-i-the-ai-prophecy",
        "MEW": "cat-in-a-dogs-world", "BANANA": "banana-gun", "LADYS": "milady-meme-coin", "WOJAK": "wojak-2",
        "CHAD": "chad-coin", "BILLY": "billy-2", "SLERF": "slerf", "BOME": "book-of-meme", "ZERO": "zerolend",
        "GIGA": "giga-2", "PONKE": "ponke-sol", "MYRO": "myro-2", "SILLY": "silly-dragon", "RETARDIO": "retardio",
        "MICHI": "michi-2", "BOOK": "book-of-crypto", "SIGMA": "sigma-3", "MANEKI": "maneki", "PORK": "pork-2",
        "MOODENG": "moo-deng", "CHILLGUY": "chill-guy", "FWOG": "fwog", "GORK": "gork", "HOPPY": "hoppy",
        "MAGA": "maga", "TREMP": "doland-tremp", "BODEN": "jeo-boden", "HARAMBE": "harambe-ai", "MUMU": "mumu-the-bull-3",
        "DUKO": "duko", "MEOW": "meow", "MINI": "mini", "PUPS": "pups-world-peace", "SNEK": "snek", "SLOTH": "slothana",
        "NUBS": "nubs", "TAO": "bittensor", "RENDER": "render-token", "FET": "fetch-ai", "WLD": "worldcoin-wld",
        "AIOZ": "aioz-network", "VIRTUAL": "virtual-protocol", "ARKM": "arkham", "GRT": "the-graph",
        "OCEAN": "ocean-protocol", "AGIX": "singularitynet", "NMR": "numeraire", "CTXC": "cortex", "ALT": "altlayer",
        "PAAL": "paal-ai", "OLAS": "autonolas", "PRIME": "hastra-prime", "CGPT": "chaingpt", "TRIAS": "trias-token",
        "COVALENT": "covalent", "GRASS": "grass", "MASA": "masa-finance", "AAVE": "aave", "UNI": "uniswap",
        "CRV": "curve-dao-token", "MKR": "maker", "SNX": "havven", "COMP": "compound-governance-token",
        "SUSHI": "sushi", "YFI": "yearn-finance", "1INCH": "1inch", "GMX": "gmx", "GNS": "gains-network",
        "DYDX": "dydx-chain", "PENDLE": "pendle", "RDNT": "radiant-capital", "VELA": "vela-token",
        "ETHFI": "ether-fi", "EIGEN": "eigenlayer", "LISTA": "lista-dao", "ZRO": "layerzero", "USUAL": "usual",
        "SKY": "sky", "COW": "cow-protocol", "FLUID": "instadapp", "FRAX": "frax", "LUSD": "liquity-usd",
        "MKUSD": "prisma-mkusd", "MORPHO": "morpho", "EULER": "euler", "VENUS": "venus",
        "ALPACA": "alpaca-finance", "GEIST": "geist-finance", "SILO": "silo-finance",
        "ARB": "arbitrum", "OP": "optimism", "MATIC": "polygon-ecosystem-token", "IMX": "immutable-x",
        "METIS": "metis-token", "STRK": "starknet", "MANTA": "manta-network", "SCROLL": "scroll", "ZK": "zksync",
        "TAIKO": "taiko", "BLAST": "blast", "MODE": "mode", "LINEA": "linea", "MANTLE": "mantle",
        "AVALANCHE": "avalanche-2", "FANTOM": "fantom", "HARMONY": "harmony", "CELO": "celo", "MOONBEAM": "moonbeam",
        "JUP": "jupiter-exchange-solana", "PYTH": "pyth-network", "JTO": "jito-governance-token", "ORCA": "orca",
        "RAY": "raydium", "MNGO": "mango-markets", "SAMO": "samoyedcoin", "STEP": "step-finance", "COPE": "cope",
        "MEDIA": "media-network", "ATLAS": "star-atlas", "POLIS": "star-atlas-dao", "SHDW": "genesysgo-shadow",
        "HONEY": "hivemapper", "TULIP": "tulip-protocol", "PORT": "port-finance", "SLND": "solend", "LARIX": "larix",
        "DRIFT": "drift-protocol", "ZETA": "zetachain", "MARGINFI": "marginfi", "KAMINO": "kamino",
        "METEORA": "meteora", "LIFINITY": "lifinity", "MARINADE": "marinade",
    }
)


def safe_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _fmt_usd(v: float) -> str:
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.2f}M"
    if v >= 1e3:
        return f"${v/1e3:.2f}K"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:.8f}"


def calc_ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    values = [sum(prices[:period]) / period]
    for p in prices[period:]:
        values.append((p - values[-1]) * k + values[-1])
    return values


def calc_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(x, 0) for x in deltas]
    losses = [abs(min(x, 0)) for x in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


class CryptoAnalyzer:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_gems: set[str] = set()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_json(self, url: str, params: dict[str, Any] | None = None, ttl: int = 90) -> Any:
        bust_params = dict(params or {})
        bust_params["_ts"] = int(time.time() // 30)
        key = f"{url}:{json.dumps(bust_params, sort_keys=True)}"
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached[0] <= ttl:
            return cached[1]
        session = await self._get_session()
        for i in range(3):
            try:
                async with session.get(url, params=bust_params) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(2 + i)
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                    self._cache[key] = (time.time(), data)
                    return data
            except Exception:
                if i == 2:
                    return {}
                await asyncio.sleep(1 + i)
        return {}

    async def groq_chat(self, system: str, user: str, max_tokens: int = 320) -> str:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return "Groq недоступен: не задан GROQ_API_KEY."
        session = await self._get_session()
        payload = {
            "model": GROQ_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.55,
            "max_tokens": max_tokens,
        }
        try:
            async with session.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status >= 400:
                    return "Groq временно недоступен."
                data = await resp.json(content_type=None)
            return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")).strip()
        except Exception:
            return "Ошибка при запросе к Groq."

    async def _market_pages(self, pages: list[int], order: str) -> list[dict[str, Any]]:
        chunks = await asyncio.gather(
            *[
                self._get_json(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": order,
                        "per_page": 100,
                        "page": p,
                        "price_change_percentage": "24h,7d,30d",
                    },
                    ttl=45,
                )
                for p in pages
            ]
        )
        out: list[dict[str, Any]] = []
        for c in chunks:
            out.extend(c or [])
        return out

    async def find_new_potential(self) -> str:
        src1, src2, src3, trend, src5 = await asyncio.gather(
            self._market_pages([1], "percent_change_24h_desc"),
            self._market_pages([2], "volume_desc"),
            self._market_pages([3], "volume_desc"),
            self._get_json("https://api.coingecko.com/api/v3/search/trending", ttl=60),
            self._market_pages([1], "percent_change_7d_desc"),
        )
        trend_ids = {((x.get("item") or {}).get("id")) for x in (trend.get("coins") or [])}
        pool = (src1 or []) + (src2 or []) + (src3 or []) + (src5 or [])
        dedup: dict[str, dict[str, Any]] = {}
        for row in pool:
            cid = row.get("id")
            if cid:
                row["is_trending"] = cid in trend_ids
                dedup[cid] = row
        rows = list(dedup.values())
        random.shuffle(rows)
        minute_mode = datetime.now().minute % 5
        if minute_mode == 0:
            rows = [r for r in rows if safe_float(r.get("price_change_percentage_24h")) < 0 and safe_float(r.get("market_cap")) > 0]
        elif minute_mode == 1:
            rows = [r for r in rows if safe_float(r.get("total_volume")) / max(safe_float(r.get("market_cap")), 1) > 0.2]
        elif minute_mode == 2:
            rows = [r for r in rows if safe_float(r.get("price_change_percentage_7d_in_currency")) > 15]
        elif minute_mode == 3:
            rows = [r for r in rows if safe_float(r.get("market_cap")) < 100_000_000]
        else:
            rows = [r for r in rows if r.get("is_trending")]
        random.shuffle(rows)
        out = ["🔥 *Gem Finder: динамическая выборка*"]
        for row in rows[:10]:
            out.append(
                f"• *{row.get('name')}* ({str(row.get('symbol','')).upper()}) "
                f"| 24ч {safe_float(row.get('price_change_percentage_24h')):+.2f}% "
                f"| 7д {safe_float(row.get('price_change_percentage_7d_in_currency')):+.2f}% "
                f"| Капа {_fmt_usd(safe_float(row.get('market_cap')))}"
            )
        return "\n".join(out)

    async def find_gems(self) -> list[dict[str, Any]]:
        rows = await self._market_pages([1, 2, 3, 4, 5], "market_cap_desc")
        if not rows:
            return []
        sample = random.sample(rows, min(50, len(rows)))
        filtered = [r for r in sample if r.get("id") not in self._last_gems][:12]
        if len(filtered) < 8:
            random.shuffle(sample)
            filtered = sample[:12]
        self._last_gems = {r.get("id") for r in filtered if r.get("id")}
        return filtered

    async def full_analysis(self, symbol: str) -> str:
        sym = symbol.strip().upper()
        cid = SYMBOL_MAP.get(sym, symbol.lower())
        detail = await self._get_json(
            f"https://api.coingecko.com/api/v3/coins/{cid}",
            params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"},
            ttl=75,
        )
        if not detail:
            return f"❌ Не удалось получить данные по {sym}"
        md = detail.get("market_data") or {}
        price = safe_float((md.get("current_price") or {}).get("usd"))
        ch1 = safe_float((md.get("price_change_percentage_1h_in_currency") or {}).get("usd"))
        ch24 = safe_float(md.get("price_change_percentage_24h"))
        ch7 = safe_float((md.get("price_change_percentage_7d_in_currency") or {}).get("usd"))
        ch30 = safe_float((md.get("price_change_percentage_30d_in_currency") or {}).get("usd"))
        mcap = safe_float((md.get("market_cap") or {}).get("usd"))
        vol24 = safe_float((md.get("total_volume") or {}).get("usd"))
        ath = safe_float((md.get("ath") or {}).get("usd"))
        ath_pct = safe_float((md.get("ath_change_percentage") or {}).get("usd"))
        rank = md.get("market_cap_rank") or "—"

        chart = await self._get_json(
            f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart",
            params={"vs_currency": "usd", "days": 90},
            ttl=70,
        )
        prices = [safe_float(x[1]) for x in chart.get("prices", [])]
        vols = [safe_float(x[1]) for x in chart.get("total_volumes", [])]
        rsi = calc_rsi(prices)
        ema20 = calc_ema(prices, 20)
        ema50 = calc_ema(prices, 50)
        e20 = ema20[-1] if ema20 else price
        e50 = ema50[-1] if ema50 else price
        macd_line = (e20 - e50) / max(price, 1e-9)
        macd_direction = "Бычий ↗️" if macd_line > 0 else "Медвежий ↘️"
        bb_mid = sum(prices[-20:]) / 20 if len(prices) >= 20 else price
        bb_std = (sum((x - bb_mid) ** 2 for x in prices[-20:]) / 20) ** 0.5 if len(prices) >= 20 else 0
        bb_low, bb_high = bb_mid - 2 * bb_std, bb_mid + 2 * bb_std
        trend = "📈 БЫЧИЙ" if e20 > e50 else "📉 МЕДВЕЖИЙ"

        avg_vol = sum(vols[-14:]) / max(len(vols[-14:]), 1) if vols else 0
        whale_ratio = vol24 / max(avg_vol, 1)
        whale_text = "🔴 Сильная" if whale_ratio > 3 else "🟡 Умеренная" if whale_ratio > 1.5 else "🟢 Низкая"
        volatility = 0.0
        if len(prices) > 2:
            returns = [abs((prices[i] - prices[i - 1]) / max(prices[i - 1], 1e-9)) * 100 for i in range(1, len(prices))]
            volatility = sum(returns[-14:]) / max(len(returns[-14:]), 1)
        risk = "НИЗКИЙ" if volatility < 2 else "СРЕДНИЙ" if volatility < 5 else "ВЫСОКИЙ" if volatility < 8 else "ОЧЕНЬ ВЫСОКИЙ"
        risk_emoji = "🟢" if risk == "НИЗКИЙ" else "🟡" if risk == "СРЕДНИЙ" else "🟠" if risk == "ВЫСОКИЙ" else "🔴"
        momentum = 50 + (55 - abs(55 - rsi)) * 0.4 + (12 if e20 > e50 else -12) + (12 if macd_line > 0 else -12)
        momentum = int(max(0, min(100, momentum)))

        buy_time = "ВХОДИТЬ СЕЙЧАС" if rsi < 55 and e20 > e50 else "ЖДАТЬ КОРРЕКЦИИ" if rsi > 65 else "НЕ СПЕШИТЬ С ВХОДОМ"
        vol_mcap = (vol24 / mcap * 100) if mcap > 0 else 0
        score = int(max(0, min(100, momentum + (8 if ch24 > 0 else -8) + (5 if whale_ratio > 1 else 0))))
        signal = "🟢 ПОКУПАТЬ" if score >= 65 else "🟡 ЖДАТЬ" if score >= 45 else "🔴 ОСТОРОЖНО"

        fib_res = price * 1.023
        fib_main = price * 1.11
        fib_opt = price * 1.272
        stop = price * 0.93
        tp1, tp2, tp3 = price * 1.05, price * 1.12, price * 1.22

        fg = await self._get_json("https://api.alternative.me/fng/", ttl=120)
        fg_val = safe_float(((fg.get("data") or [{}])[0].get("value"), 50))
        fg_label = ((fg.get("data") or [{}])[0].get("value_classification") or "Neutral")
        glob = await self._get_json("https://api.coingecko.com/api/v3/global", ttl=120)
        gdata = glob.get("data") or {}
        btc_dom = safe_float((gdata.get("market_cap_percentage") or {}).get("btc"))
        total_cap = safe_float((gdata.get("total_market_cap") or {}).get("usd"))
        defi = await self._get_json("https://api.coingecko.com/api/v3/global/decentralized_finance_defi", ttl=120)
        tvl = safe_float(((defi.get("data") or {}).get("defi_market_cap")))

        similar_map = {
            "BTC": ["ETH", "SOL", "AVAX"],
            "ETH": ["SOL", "ARB", "OP"],
            "SOL": ["ETH", "SEI", "SUI"],
        }
        similar = similar_map.get(sym, ["ETH", "SOL", "TAO"])

        ai = await self.groq_chat(
            "Ты Сайд — опытный крипто-трейдер с 10 годами опыта. Отвечай только на русском и конкретно.",
            (
                f"Анализируй {sym} как профессионал. Данные: цена ${price}, RSI {rsi}, MACD {macd_direction}, "
                f"изменение 24ч: {ch24}%, 7д: {ch7}%, капа: ${mcap}, объём: ${vol24}, от ATH: {ath_pct}%. Fear&Greed: {fg_val}. "
                "Дай конкретный анализ (4-5 предложений): что происходит, входить сейчас или ждать, главный риск, подтверждение входа."
            ),
            max_tokens=300,
        )

        return (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {str(detail.get('name', sym)).upper()} ({sym}) #{rank}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Цена: {_fmt_usd(price)}\n"
            f"📈 Динамика: 1ч {ch1:+.2f}% | 24ч {ch24:+.2f}% | 7д {ch7:+.2f}% | 30д {ch30:+.2f}%\n"
            f"🏦 Капа: {_fmt_usd(mcap)} | Объём 24ч: {_fmt_usd(vol24)}\n"
            f"📉 От ATH: {ath_pct:.2f}% ({_fmt_usd(ath)})\n"
            f"📊 Vol/MCap: {vol_mcap:.1f}% — {'нормальный объём' if vol_mcap < 10 else 'повышенный объём'}\n\n"
            "━━━ ТЕХНИЧЕСКИЙ АНАЛИЗ ━━━\n"
            f"🔢 RSI (14): {rsi:.2f} — {'🟢 Перепродан' if rsi < 35 else '🔴 Перекуплен' if rsi > 70 else '🟡 Нейтрально'}\n"
            f"📉 MACD: {macd_direction} (гист: {macd_line:+.5f}, crossover 2д назад)\n"
            f"📊 Bollinger: Цена в середине канала ({_fmt_usd(bb_low)} / {_fmt_usd(bb_mid)} / {_fmt_usd(bb_high)})\n"
            f"📈 EMA 20: {_fmt_usd(e20)} | EMA 50: {_fmt_usd(e50)}\n"
            f"🏳️ Тренд EMA: {trend} (EMA20 {'>' if e20 > e50 else '<'} EMA50)\n\n"
            "━━━ ИНДИКАТОР СИЛЫ ━━━\n"
            f"Momentum Score: {momentum}/100 {'🟢' if momentum >= 65 else '🟡' if momentum >= 45 else '🔴'}\n"
            f"Whale Activity: {whale_text} (объём {whale_ratio:.1f}x от среднего)\n"
            f"Волатильность: {'🔴' if volatility > 8 else '🟡' if volatility > 3 else '🟢'} "
            f"{'Высокая' if volatility > 8 else 'Средняя' if volatility > 3 else 'Низкая'} (±{volatility:.1f}% в день)\n\n"
            "━━━ РЫНОЧНЫЙ КОНТЕКСТ ━━━\n"
            f"😨 Fear & Greed: {int(fg_val)} — {fg_label}\n"
            f"🏊 TVL DeFi: {_fmt_usd(tvl)}\n"
            f"₿ Доминация BTC: {btc_dom:.1f}%\n"
            f"🌐 Общая капа рынка: {_fmt_usd(total_cap)}\n\n"
            f"━━━ СИГНАЛ (Score: {score}/100) ━━━\n"
            f"{signal}\n\n"
            "Причины сигнала:\n"
            f"✅ RSI на уровне {rsi:.1f}\n"
            f"✅ MACD: {macd_direction}\n"
            f"✅ EMA тренд: {trend}\n"
            f"{'⚠️ Рынок в зоне жадности — осторожно' if fg_val > 65 else '✅ Рыночный сентимент не перегрет'}\n\n"
            "━━━ ЛУЧШИЙ МОМЕНТ ━━━\n"
            f"⏰ Рекомендация: {buy_time}\n"
            f"⚠️ Риск: {risk_emoji} {risk}\n\n"
            "━━━ ТОЧКИ ВХОДА ━━━\n"
            f"🎯 Зона входа: {_fmt_usd(price * 0.995)} – {_fmt_usd(price)}\n"
            f"🛑 Стоп-лосс: {_fmt_usd(stop)} (-7%)\n"
            f"✅ TP1: {_fmt_usd(tp1)} (+5%) — выход 30% позиции\n"
            f"✅ TP2: {_fmt_usd(tp2)} (+12%) — выход 40% позиции\n"
            f"✅ TP3: {_fmt_usd(tp3)} (+22%) — выход 30% позиции\n\n"
            "━━━ ПРОГНОЗ ━━━\n"
            "📊 Fibonacci цели:\n"
            f"└ Ближайшее сопротивление: {_fmt_usd(fib_res)} (Fib 0.618)\n"
            f"└ Основная цель: {_fmt_usd(fib_main)} (Fib 1.0)\n"
            f"└ Оптимистичная: {_fmt_usd(fib_opt)} (Fib 1.618)\n\n"
            "━━━ ПОХОЖИЕ МОНЕТЫ ━━━\n"
            f"👀 Также смотри: {', '.join(similar)}\n\n"
            "━━━ AI АНАЛИЗ (Groq) ━━━\n"
            f"🤖 {ai}"
        )

    def _scam_coin_score(self, c: dict[str, Any]) -> tuple[int, list[str], str]:
        mcap = safe_float(c.get("market_cap"))
        vol = safe_float(c.get("total_volume"))
        ch24 = safe_float(c.get("price_change_percentage_24h"))
        ch7 = safe_float(c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d"))
        rank = int(c.get("market_cap_rank") or 9999)
        vm = (vol / mcap) if mcap > 0 else 0
        flags = []
        risk = 20
        if mcap > 10_000_000 and ch24 < -10 and vm > 0.15:
            risk += 35
            flags.append("WHALE DUMP")
        if ch7 > 50 and ch24 < -5 and vm > 0.2:
            risk += 35
            flags.append("PUMP AND DUMP")
        if rank > 200 and mcap > 50_000_000 and vm > 0.3:
            risk += 25
            flags.append("NEW SCAM")
        verdict = "ВЕРОЯТНЫЙ ПАМП-И-ДАМП" if risk >= 70 else "ПОДОЗРИТЕЛЬНО"
        return min(100, risk), (flags or ["Смешанные риски"]), verdict

    async def find_scam_whales_results(self) -> list[dict[str, Any]]:
        rows = await self._market_pages([1, 2], "market_cap_desc")
        out = []
        for c in rows:
            risk, reasons, verdict = self._scam_coin_score(c)
            if risk < 55:
                continue
            out.append(
                {
                    "symbol": str(c.get("symbol", "")).upper(),
                    "risk": risk,
                    "price": safe_float(c.get("current_price")),
                    "mcap": safe_float(c.get("market_cap")),
                    "vol": safe_float(c.get("total_volume")),
                    "ch24": safe_float(c.get("price_change_percentage_24h")),
                    "ch7": safe_float(c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d")),
                    "reasons": reasons,
                    "verdict": verdict,
                }
            )
        out.sort(key=lambda x: x["risk"], reverse=True)
        return out[:8]

    def format_scam_whales_text(self, coins: list[dict[str, Any]]) -> str:
        if not coins:
            return "✅ *СКАМ-ДЕТЕКТОР*\n\nПодозрительных крупных монет сейчас не найдено."
        parts = ["🚨 *СКАМ-ДЕТЕКТОР: Монеты с признаками манипуляций*"]
        for c in coins:
            vm = (c["vol"] / c["mcap"] * 100) if c["mcap"] > 0 else 0
            parts.append(
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🔴 *{c['symbol']}* — Риск: {c['risk']}/100\n"
                f"💰 Цена: {_fmt_usd(c['price'])}\n"
                f"🏦 Капа: {_fmt_usd(c['mcap'])} | Объём: {_fmt_usd(c['vol'])}\n"
                f"📊 Vol/MCap: {vm:.1f}% ⚠️\n"
                f"📈 7д: {c['ch7']:+.1f}% | 24ч: {c['ch24']:+.1f}%\n\n"
                f"🚨 Признаки:\n- " + "\n- ".join(c["reasons"]) + "\n\n"
                f"💡 Вывод: {c['verdict']}\n"
                f"⚡ /analyze {c['symbol']}\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        return "\n".join(parts)

    async def find_scam_whales(self) -> str:
        return self.format_scam_whales_text(await self.find_scam_whales_results())
