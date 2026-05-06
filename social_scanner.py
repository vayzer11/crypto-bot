from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import datetime
from typing import Any

import aiohttp

from analysis import CryptoAnalyzer, SYMBOL_MAP, safe_float

REDDIT_URLS = [
    "https://www.reddit.com/r/CryptoMoonShots/new.json?limit=50",
    "https://www.reddit.com/r/SatoshiStreetBets/hot.json?limit=50",
    "https://www.reddit.com/r/CryptoCurrency/rising.json?limit=50",
    "https://www.reddit.com/r/altcoin/new.json?limit=50",
    "https://www.reddit.com/r/defi/hot.json?limit=50",
    "https://www.reddit.com/r/memecoins/new.json?limit=50",
]
DEXSCREENER_TRENDING = [
    "https://api.dexscreener.com/latest/dex/search?q=trending",
    "https://api.dexscreener.com/latest/dex/search?q=new",
    "https://api.dexscreener.com/latest/dex/search?q=gem",
]
LUNARCRUSH_URL = "https://lunarcrush.com/api4/public/coins/list/v2"
TICKER_RE = re.compile(r"(?:\$)?\b([A-Z]{2,10})\b")


class SocialScanner:
    def __init__(self) -> None:
        self.analyzer = CryptoAnalyzer()
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"User-Agent": "crypto-social-scanner/1.0"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        session = await self._get_session()
        for i in range(3):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(2 + i)
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except Exception:
                if i == 2:
                    return {}
                await asyncio.sleep(1 + i)
        return {}

    async def fetch_reddit_mentions(self) -> list[dict[str, Any]]:
        mentions: dict[str, dict[str, Any]] = {}
        for url in REDDIT_URLS:
            data = await self._get_json(url)
            await asyncio.sleep(2)
            children = (((data or {}).get("data") or {}).get("children") or [])
            for row in children:
                post = row.get("data") or {}
                text = f"{post.get('title', '')} {post.get('selftext', '')}".upper()
                subreddit = post.get("subreddit", "")
                for m in TICKER_RE.findall(text):
                    if m in {"THE", "THIS", "WITH", "FROM", "YOUR", "WILL", "MOON", "PUMP", "HOLD", "LONG"}:
                        continue
                    if len(m) > 6 and m not in SYMBOL_MAP:
                        continue
                    entry = mentions.setdefault(
                        m,
                        {"symbol": m, "reddit_mentions": 0, "expert_mention": False, "expert_source": ""},
                    )
                    entry["reddit_mentions"] += 1
                    if post.get("score", 0) >= 100:
                        entry["expert_mention"] = True
                        entry["expert_source"] = f"r/{subreddit}"
        return [x for x in mentions.values() if x["reddit_mentions"] >= 2]

    async def fetch_coingecko_trending(self) -> list[dict[str, Any]]:
        data = await self._get_json("https://api.coingecko.com/api/v3/search/trending")
        out: list[dict[str, Any]] = []
        for idx, row in enumerate((data.get("coins") or []), start=1):
            item = row.get("item") or {}
            out.append(
                {
                    "id": item.get("id"),
                    "symbol": str(item.get("symbol", "")).upper(),
                    "name": item.get("name", ""),
                    "is_trending_coingecko": True,
                    "coingecko_trend_rank": idx,
                    "trend_bonus": max(5, 35 - idx * 4),
                }
            )
        return out

    async def fetch_low_cap_gainers(self) -> list[dict[str, Any]]:
        data = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "percent_change_24h_desc",
                "per_page": 100,
                "page": 1,
                "price_change_percentage": "24h,7d",
            },
        )
        await asyncio.sleep(2)
        out = []
        for row in data or []:
            mcap = safe_float(row.get("market_cap"))
            ch24 = safe_float(row.get("price_change_percentage_24h"))
            if mcap < 50_000_000 and ch24 > 20:
                out.append(row)
        return out

    async def fetch_dex_trending(self) -> list[dict[str, Any]]:
        tokens: dict[str, dict[str, Any]] = {}
        for url in DEXSCREENER_TRENDING:
            data = await self._get_json(url)
            await asyncio.sleep(2)
            for pair in data.get("pairs") or []:
                age_h = max((datetime.now().timestamp() * 1000 - float(pair.get("pairCreatedAt") or 0)) / 3_600_000, 0.0)
                liq = safe_float((pair.get("liquidity") or {}).get("usd"))
                vol = safe_float((pair.get("volume") or {}).get("h24"))
                if age_h >= 48 or liq < 20_000:
                    continue
                base = pair.get("baseToken") or {}
                sym = str(base.get("symbol", "")).upper()
                if not sym:
                    continue
                tokens[sym] = {
                    "symbol": sym,
                    "name": base.get("name", sym),
                    "is_trending_dex": True,
                    "dex_liquidity": liq,
                    "dex_volume": vol,
                }
        return list(tokens.values())

    async def fetch_lunarcrush(self) -> list[dict[str, Any]]:
        data = await self._get_json(LUNARCRUSH_URL)
        rows = data.get("data") or []
        out: list[dict[str, Any]] = []
        for row in rows[:120]:
            sym = str(row.get("symbol", "")).upper()
            if not sym:
                continue
            out.append(
                {
                    "symbol": sym,
                    "social_volume": safe_float(row.get("social_volume")),
                    "social_score_raw": safe_float(row.get("social_score")),
                    "galaxy_score": safe_float(row.get("galaxy_score")),
                }
            )
        return out

    def calculate_social_score(self, coin: dict[str, Any]) -> dict[str, Any]:
        score = 0
        signals = []
        reddit_mentions = coin.get("reddit_mentions", 0)
        if reddit_mentions >= 10:
            score += 35
            signals.append(f"🔥 {reddit_mentions} упоминаний на Reddit")
        elif reddit_mentions >= 5:
            score += 20
            signals.append(f"📱 {reddit_mentions} упоминаний на Reddit")
        elif reddit_mentions >= 2:
            score += 10
            signals.append(f"💬 {reddit_mentions} упоминания на Reddit")
        if coin.get("is_trending_coingecko"):
            score += 30
            signals.append("🚀 В TRENDING на CoinGecko")
        if coin.get("is_trending_dex"):
            score += 25
            signals.append("📊 Trending на DexScreener")
        mcap = coin.get("market_cap", 0)
        if mcap and mcap < 1_000_000:
            score += 25
            signals.append("💎 Микрокапа < $1M — огромный потенциал")
        elif mcap < 10_000_000:
            score += 20
            signals.append("💎 Малая капа < $10M")
        elif mcap < 50_000_000:
            score += 12
            signals.append("📈 Капа < $50M")
        change = coin.get("change_24h", 0)
        if change > 50:
            score += 20
            signals.append(f"🔥 Рост +{change:.0f}% за 24ч")
        elif change > 20:
            score += 12
            signals.append(f"📈 Рост +{change:.0f}% за 24ч")
        elif change > 5:
            score += 5
            signals.append(f"↗️ Движение +{change:.0f}% за 24ч")
        vol_ratio = coin.get("vol_mcap_ratio", 0)
        if vol_ratio > 0.5:
            score += 20
            signals.append("🐋 Аномальный объём — кто-то скупает!")
        elif vol_ratio > 0.2:
            score += 10
            signals.append("📊 Высокий объём")
        if coin.get("expert_mention"):
            score += 25
            signals.append(f"👨‍💼 Упомянута экспертом: {coin.get('expert_source', '')}")
        if mcap and mcap < 1_000_000 and score >= 60:
            potential = "🚀 x100-x1000 потенциал"
        elif mcap and mcap < 10_000_000 and score >= 50:
            potential = "🚀 x20-x100 потенциал"
        elif mcap and mcap < 50_000_000 and score >= 40:
            potential = "📈 x10-x20 потенциал"
        else:
            potential = "📈 x2-x10 потенциал"
        return {"social_score": min(100, score), "signals": signals[:5], "potential": potential}

    async def _groq_social_comment(self, coin: dict[str, Any]) -> str:
        return await self.analyzer.groq_chat(
            "Ты криптоаналитик. Ответ только на русском. Ровно 2 предложения, без markdown.",
            f"Монета {coin.get('name', coin.get('symbol'))} ({coin.get('symbol')}). "
            f"Соцскор {coin.get('social_score')}, Reddit {coin.get('reddit_mentions', 0)}, "
            f"изменение 24ч {coin.get('change_24h', 0):.2f}%, капа {coin.get('market_cap', 0)}.",
            max_tokens=120,
        )

    async def _groq_social_comment_short(self, coin: dict[str, Any]) -> str:
        return await self.analyzer.groq_chat(
            "Ты криптоаналитик. Ответ только на русском. Ровно 3 коротких предложения.",
            f"Монета {coin.get('name', coin.get('symbol'))} ({coin.get('symbol')}). "
            f"Социальные сигналы: {', '.join(coin.get('signals', []))}. Потенциал: {coin.get('potential')}.",
            max_tokens=160,
        )

    async def find_social_gems_data(self) -> list[dict[str, Any]]:
        reddit, trending, gainers, dex_rows, lunar = await asyncio.gather(
            self.fetch_reddit_mentions(),
            self.fetch_coingecko_trending(),
            self.fetch_low_cap_gainers(),
            self.fetch_dex_trending(),
            self.fetch_lunarcrush(),
        )
        index: dict[str, dict[str, Any]] = {}
        for row in reddit:
            index.setdefault(row["symbol"], {}).update(row)
        for row in trending:
            sym = row["symbol"]
            index.setdefault(sym, {}).update(row)
        for row in dex_rows:
            index.setdefault(row["symbol"], {}).update(row)
        for row in lunar:
            index.setdefault(row["symbol"], {}).update(row)
        for row in gainers:
            sym = str(row.get("symbol", "")).upper()
            if not sym:
                continue
            mcap = safe_float(row.get("market_cap"))
            vol = safe_float(row.get("total_volume"))
            index.setdefault(sym, {}).update(
                {
                    "id": row.get("id"),
                    "symbol": sym,
                    "name": row.get("name", sym),
                    "price": safe_float(row.get("current_price")),
                    "market_cap": mcap,
                    "volume_24h": vol,
                    "change_24h": safe_float(row.get("price_change_percentage_24h")),
                    "change_7d": safe_float(
                        row.get("price_change_percentage_7d_in_currency") or row.get("price_change_percentage_7d")
                    ),
                    "vol_mcap_ratio": (vol / mcap) if mcap > 0 else 0.0,
                }
            )
        enriched: list[dict[str, Any]] = []
        for sym, coin in index.items():
            coin.setdefault("symbol", sym)
            coin.setdefault("name", sym)
            coin.setdefault("market_cap", 0.0)
            coin.setdefault("change_24h", 0.0)
            coin.setdefault("vol_mcap_ratio", 0.0)
            score_pack = self.calculate_social_score(coin)
            coin.update(score_pack)
            if coin["social_score"] >= 45:
                enriched.append(coin)
        enriched.sort(key=lambda x: x["social_score"], reverse=True)
        return enriched[:7]

    async def find_social_gems(self) -> str:
        gems = await self.find_social_gems_data()
        if not gems:
            return "🔍 СОЦИАЛЬНЫЙ СКАНЕР\n\nНе найдено сильных ранних сигналов."
        lines = [
            "🔍 СОЦИАЛЬНЫЙ СКАНЕР — Ранние гемы",
            f"🕐 Обновлено: {datetime.now().strftime('%H:%M:%S')}",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for coin in gems[:7]:
            why = await self._groq_social_comment(coin)
            await asyncio.sleep(2)
            lines.extend(
                [
                    f"🥇 {coin['symbol']} ({coin.get('name', coin['symbol'])}) — Score: {coin['social_score']}/100",
                    f"💰 Цена: ${safe_float(coin.get('price')):.8f} | Капа: ${safe_float(coin.get('market_cap')):,.0f}",
                    f"📈 24ч: {safe_float(coin.get('change_24h')):+.2f}% | 7д: {safe_float(coin.get('change_7d')):+.2f}%",
                    f"🎯 Потенциал: {coin['potential']}",
                    "📡 Социальные сигналы:",
                    "",
                    *coin["signals"],
                    "",
                    "💡 Почему интересна:",
                    why,
                    f"⚡ /analyze {coin['symbol']} — полный анализ",
                    "━━━━━━━━━━━━━━━━━━━━",
                ]
            )
        lines.append("⚠️ Социальные сигналы ≠ гарантия роста. DYOR.")
        return "\n".join(lines)
