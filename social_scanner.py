from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime
from typing import Any

import aiohttp

from analysis import safe_float

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


class SocialScanner:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._last_gems: set[str] = set()

    async def _s(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        session = await self._s()
        p = dict(params or {})
        p["_ts"] = int(datetime.now().timestamp() // 20)
        for i in range(3):
            try:
                async with session.get(url, params=p, headers={"User-Agent": "crypto-social-scanner/1.0"}) as r:
                    if r.status == 429:
                        await asyncio.sleep(2 + i)
                        continue
                    if r.status >= 400:
                        return {}
                    return await r.json(content_type=None)
            except Exception:
                if i == 2:
                    return {}
                await asyncio.sleep(1 + i)
        return {}

    def calculate_social_score(self, coin: dict) -> dict:
        score = 0
        signals = []
        rm = coin.get("reddit_mentions", 0)
        if rm >= 10:
            score += 35; signals.append(f"🔥 {rm} упоминаний на Reddit")
        elif rm >= 5:
            score += 20; signals.append(f"📱 {rm} упоминаний на Reddit")
        elif rm >= 2:
            score += 10; signals.append(f"💬 {rm} упоминания на Reddit")
        if coin.get("is_trending_coingecko"):
            score += 30; signals.append("🚀 В TRENDING на CoinGecko")
        if coin.get("is_trending_dex"):
            score += 25; signals.append("📊 Trending на DexScreener")
        mcap = coin.get("market_cap", 0)
        if mcap and mcap < 1_000_000:
            score += 25; signals.append("💎 Микрокапа < $1M — огромный потенциал")
        elif mcap < 10_000_000:
            score += 20; signals.append("💎 Малая капа < $10M")
        elif mcap < 50_000_000:
            score += 12; signals.append("📈 Капа < $50M")
        ch = coin.get("change_24h", 0)
        if ch > 50:
            score += 20; signals.append(f"🔥 Рост +{ch:.0f}% за 24ч")
        elif ch > 20:
            score += 12; signals.append(f"📈 Рост +{ch:.0f}% за 24ч")
        elif ch > 5:
            score += 5; signals.append(f"↗️ Движение +{ch:.0f}% за 24ч")
        vm = coin.get("vol_mcap_ratio", 0)
        if vm > 0.5:
            score += 20; signals.append("🐋 Аномальный объём — кто-то скупает!")
        elif vm > 0.2:
            score += 10; signals.append("📊 Высокий объём")
        if coin.get("expert_mention"):
            score += 25; signals.append(f"👨‍💼 Упомянута экспертом: {coin.get('expert_source', '')}")
        potential = "📈 x2-x10 потенциал"
        if mcap and mcap < 1_000_000 and score >= 60:
            potential = "🚀 x100-x1000 потенциал"
        elif mcap and mcap < 10_000_000 and score >= 50:
            potential = "🚀 x20-x100 потенциал"
        elif mcap and mcap < 50_000_000 and score >= 40:
            potential = "📈 x10-x20 потенциал"
        return {"social_score": min(100, score), "signals": signals[:5], "potential": potential}

    async def find_social_gems_data(self) -> list[dict[str, Any]]:
        reddit_raw, trend_raw, gainers_raw, dex_raw = await asyncio.gather(
            self._fetch_reddit(), self._fetch_trending(), self._fetch_gainers(), self._fetch_dex()
        )
        coins: dict[str, dict[str, Any]] = {}
        for row in reddit_raw + trend_raw + gainers_raw + dex_raw:
            sym = row.get("symbol")
            if not sym:
                continue
            coins.setdefault(sym, {}).update(row)
        rows = list(coins.values())
        random.shuffle(rows)
        rows = [r for r in rows if r.get("symbol") not in self._last_gems]
        if len(rows) < 7:
            rows = list(coins.values())
            random.shuffle(rows)
        out = []
        for row in rows:
            pack = self.calculate_social_score(row)
            row.update(pack)
            if row["social_score"] >= 40:
                out.append(row)
        out.sort(key=lambda x: x["social_score"], reverse=True)
        top = out[:7]
        self._last_gems = {x.get("symbol", "") for x in top}
        return top

    async def find_social_gems(self) -> str:
        gems = await self.find_social_gems_data()
        if not gems:
            return "🔍 СОЦИАЛЬНЫЙ СКАНЕР\nНе найдено сильных сигналов."
        lines = [
            "🔍 СОЦИАЛЬНЫЙ СКАНЕР — Ранние гемы",
            f"🕐 Обновлено: {datetime.now().strftime('%H:%M:%S')}",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for c in gems:
            lines.extend(
                [
                    f"🥇 {c.get('symbol')} ({c.get('name', c.get('symbol'))}) — Score: {c.get('social_score')}/100",
                    f"💰 Цена: ${safe_float(c.get('price')):.8f} | Капа: ${safe_float(c.get('market_cap')):,.0f}",
                    f"📈 24ч: {safe_float(c.get('change_24h')):+.2f}% | 7д: {safe_float(c.get('change_7d')):+.2f}%",
                    f"🎯 Потенциал: {c.get('potential')}",
                    "📡 Социальные сигналы:",
                    *c.get("signals", []),
                    f"⚡ /analyze {c.get('symbol')} — полный анализ",
                    "━━━━━━━━━━━━━━━━━━━━",
                ]
            )
        lines.append("⚠️ Социальные сигналы ≠ гарантия роста. DYOR.")
        return "\n".join(lines)

    async def _fetch_reddit(self) -> list[dict[str, Any]]:
        tick = re.compile(r"(?:\$)?\b([A-Z]{2,10})\b")
        agg: dict[str, dict[str, Any]] = {}
        for url in REDDIT_URLS:
            data = await self._get(url)
            await asyncio.sleep(2)
            for ch in (((data.get("data") or {}).get("children")) or []):
                d = ch.get("data") or {}
                text = f"{d.get('title','')} {d.get('selftext','')}".upper()
                for t in tick.findall(text):
                    if t in {"THE", "AND", "PUMP", "MOON", "THIS", "THAT"}:
                        continue
                    row = agg.setdefault(t, {"symbol": t, "reddit_mentions": 0, "expert_mention": False, "expert_source": ""})
                    row["reddit_mentions"] += 1
                    if int(d.get("score", 0)) > 150:
                        row["expert_mention"] = True
                        row["expert_source"] = f"r/{d.get('subreddit','')}"
        return [x for x in agg.values() if x["reddit_mentions"] >= 2]

    async def _fetch_trending(self) -> list[dict[str, Any]]:
        data = await self._get("https://api.coingecko.com/api/v3/search/trending")
        rows = []
        for i, c in enumerate(data.get("coins") or []):
            item = c.get("item") or {}
            rows.append({"symbol": str(item.get("symbol", "")).upper(), "name": item.get("name"), "is_trending_coingecko": True, "trend_pos": i + 1})
        return rows

    async def _fetch_gainers(self) -> list[dict[str, Any]]:
        pages = await asyncio.gather(
            self._get("https://api.coingecko.com/api/v3/coins/markets", {"vs_currency": "usd", "order": "percent_change_24h_desc", "per_page": 100, "page": 1, "price_change_percentage": "24h,7d"}),
            self._get("https://api.coingecko.com/api/v3/coins/markets", {"vs_currency": "usd", "order": "percent_change_24h_desc", "per_page": 100, "page": 2, "price_change_percentage": "24h,7d"}),
        )
        rows = []
        for page in pages:
            for c in page or []:
                mcap = safe_float(c.get("market_cap"))
                ch24 = safe_float(c.get("price_change_percentage_24h"))
                if mcap < 50_000_000 and ch24 > 20:
                    vol = safe_float(c.get("total_volume"))
                    rows.append(
                        {
                            "symbol": str(c.get("symbol", "")).upper(),
                            "name": c.get("name"),
                            "price": safe_float(c.get("current_price")),
                            "market_cap": mcap,
                            "change_24h": ch24,
                            "change_7d": safe_float(c.get("price_change_percentage_7d_in_currency")),
                            "vol_mcap_ratio": (vol / mcap) if mcap > 0 else 0,
                        }
                    )
        return rows

    async def _fetch_dex(self) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for url in DEXSCREENER_TRENDING:
            d = await self._get(url)
            await asyncio.sleep(2)
            for p in d.get("pairs") or []:
                created = safe_float(p.get("pairCreatedAt"))
                age_h = max((datetime.now().timestamp() * 1000 - created) / 3_600_000, 0)
                liq = safe_float((p.get("liquidity") or {}).get("usd"))
                if age_h > 48 or liq < 20_000:
                    continue
                base = p.get("baseToken") or {}
                sym = str(base.get("symbol", "")).upper()
                if not sym:
                    continue
                rows[sym] = {"symbol": sym, "name": base.get("name"), "is_trending_dex": True}
        return list(rows.values())
