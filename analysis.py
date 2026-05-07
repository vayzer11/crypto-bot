from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

import aiohttp

try:
    from _symbol_map_generated import SYMBOL_MAP_COINGECKO as SYMBOL_MAP
except Exception:
    SYMBOL_MAP = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"


class SmartCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: int = 300) -> Any | None:
        row = self._store.get(key)
        if not row:
            return None
        ts, val = row
        if time.time() - ts < ttl:
            return val
        return None

    def set(self, key: str, val: Any) -> None:
        self._store[key] = (time.time(), val)

    def clear_old(self) -> None:
        now = time.time()
        self._store = {k: v for k, v in self._store.items() if now - v[0] < 600}


def safe_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def get_signal(rsi: float, macd_hist: float | None, change_24h: float, change_7d: float, vol_ratio: float) -> dict[str, Any]:
    score = 50
    reasons: list[str] = []
    if rsi < 25:
        score += 35; reasons.append("RSI экстремально перепродан")
    elif rsi < 35:
        score += 25; reasons.append("RSI перепродан — возможен отскок")
    elif rsi < 45:
        score += 15; reasons.append("RSI в бычьей зоне")
    elif rsi < 55:
        score += 5; reasons.append("RSI нейтральный")
    elif rsi < 65:
        score -= 5
    elif rsi < 75:
        score -= 20; reasons.append("RSI перекуплен — осторожно")
    else:
        score -= 35; reasons.append("RSI экстремально перекуплен")
    if macd_hist is not None and macd_hist > 0:
        score += 12; reasons.append("MACD бычий")
    elif macd_hist is not None and macd_hist < 0:
        score -= 12; reasons.append("MACD медвежий")
    if change_24h > 10:
        score += 15; reasons.append(f"Сильный рост +{change_24h:.1f}%")
    elif change_24h > 3:
        score += 8; reasons.append(f"Рост +{change_24h:.1f}%")
    elif change_24h < -15:
        score -= 20; reasons.append(f"Сильное падение {change_24h:.1f}%")
    elif change_24h < -5:
        score -= 10
    if change_7d > 20:
        score += 10; reasons.append(f"Тренд +{change_7d:.1f}% за 7д")
    elif change_7d < -20:
        score -= 10
    if vol_ratio > 0.15:
        score += 12; reasons.append("Аномальный объём — интерес рынка")
    elif vol_ratio > 0.05:
        score += 5
    score = max(0, min(100, score))
    if score >= 65:
        signal, sig_type = "🟢 ПОКУПАТЬ", "buy"
    elif score >= 55:
        signal, sig_type = "🟡 ОСТОРОЖНАЯ ПОКУПКА", "watch"
    elif score <= 30:
        signal, sig_type = "🔴 ПРОДАВАТЬ", "sell"
    elif score <= 40:
        signal, sig_type = "🟠 ОСТОРОЖНАЯ ПРОДАЖА", "caution"
    else:
        signal, sig_type = "⚪ ЖДАТЬ", "wait"
    return {"signal": signal, "type": sig_type, "score": score, "reasons": reasons[:4]}


class CryptoAnalyzer:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self.cache = SmartCache()
        self._last_gems: set[str] = set()

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _fmt_price(self, price: float) -> str:
        if price >= 10000:
            return f"${price:,.0f}"
        if price >= 100:
            return f"${price:,.2f}"
        if price >= 1:
            return f"${price:.4f}"
        if price >= 0.01:
            return f"${price:.6f}"
        return f"${price:.10f}".rstrip("0")

    def _fmt_usd(self, val: float) -> str:
        if val >= 1e12:
            return f"${val/1e12:.2f}T"
        if val >= 1e9:
            return f"${val/1e9:.2f}B"
        if val >= 1e6:
            return f"${val/1e6:.2f}M"
        if val >= 1e3:
            return f"${val/1e3:.1f}K"
        return f"${val:.0f}"

    async def _get_json(self, url: str, params: dict[str, Any] | None = None, ttl: int = 60) -> Any:
        key = f"{url}:{params or {}}"
        cached = self.cache.get(key, ttl=ttl)
        if cached is not None:
            return cached
        session = await self._session_get()
        for i in range(3):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(2 + i)
                        continue
                    if resp.status >= 400:
                        return {}
                    data = await resp.json(content_type=None)
                    self.cache.set(key, data)
                    return data
            except Exception:
                if i == 2:
                    return {}
                await asyncio.sleep(1 + i)
        return {}

    async def _resolve_id(self, symbol: str) -> str:
        s = symbol.upper()
        if s in SYMBOL_MAP:
            return SYMBOL_MAP[s]
        data = await self._get_json("https://api.coingecko.com/api/v3/search", {"query": symbol}, ttl=120)
        for coin in (data.get("coins") or [])[:10]:
            if str(coin.get("symbol", "")).upper() == s:
                return str(coin.get("id", symbol.lower()))
        coins = data.get("coins") or []
        if coins:
            return str(coins[0].get("id", symbol.lower()))
        return symbol.lower()

    def _calc_approx_rsi(self, c: dict[str, Any]) -> float:
        ch24 = safe_float(c.get("price_change_percentage_24h"))
        ch7 = safe_float(c.get("price_change_percentage_7d_in_currency") or c.get("price_change_percentage_7d"))
        rsi = 50 + ch7 * 0.4 + ch24 * 0.3
        return max(5.0, min(95.0, rsi))

    async def fetch_all_coins(self) -> list[dict[str, Any]]:
        cached = self.cache.get("all_coins", ttl=300)
        if cached is not None:
            return cached
        all_coins: list[dict[str, Any]] = []
        for page in range(1, 26):
            data = await self._get_json(
                "https://api.coingecko.com/api/v3/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 100,
                    "page": page,
                    "price_change_percentage": "1h,24h,7d,30d",
                    "sparkline": "false",
                },
                ttl=120,
            )
            if not data:
                break
            all_coins.extend(data)
            await asyncio.sleep(1.5)
        trending = await self._get_json("https://api.coingecko.com/api/v3/search/trending", ttl=120)
        for item in (trending.get("coins") or []):
            cid = (item.get("item") or {}).get("id")
            if not cid:
                continue
            det = await self._get_json(
                f"https://api.coingecko.com/api/v3/coins/{cid}",
                {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"},
                ttl=120,
            )
            if det:
                md = det.get("market_data") or {}
                all_coins.append(
                    {
                        "id": det.get("id"),
                        "symbol": det.get("symbol"),
                        "name": det.get("name"),
                        "current_price": safe_float((md.get("current_price") or {}).get("usd")),
                        "market_cap": safe_float((md.get("market_cap") or {}).get("usd")),
                        "total_volume": safe_float((md.get("total_volume") or {}).get("usd")),
                        "price_change_percentage_24h": safe_float(md.get("price_change_percentage_24h")),
                        "price_change_percentage_7d_in_currency": safe_float((md.get("price_change_percentage_7d_in_currency") or {}).get("usd")),
                        "ath": safe_float((md.get("ath") or {}).get("usd")),
                        "market_cap_rank": md.get("market_cap_rank"),
                    }
                )
            await asyncio.sleep(1)
        gainers = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            {"vs_currency": "usd", "order": "percent_change_24h_desc", "per_page": 100, "page": 1, "price_change_percentage": "24h,7d"},
            ttl=90,
        )
        all_coins.extend(gainers or [])
        await asyncio.sleep(1)
        for page in [1, 2, 3]:
            vol = await self._get_json(
                "https://api.coingecko.com/api/v3/coins/markets",
                {"vs_currency": "usd", "order": "volume_desc", "per_page": 100, "page": page, "price_change_percentage": "24h,7d"},
                ttl=90,
            )
            if not vol:
                break
            all_coins.extend(vol)
            await asyncio.sleep(1)
        seen: set[str] = set()
        uniq: list[dict[str, Any]] = []
        for c in all_coins:
            cid = str(c.get("id") or c.get("symbol") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            uniq.append(c)
        self.cache.set("all_coins", uniq)
        return uniq

    async def find_new_potential(self) -> str:
        coins = await self.fetch_all_coins()
        minute = int(time.time() / 60) % 6
        label = "🔍 Идеи"
        if minute == 0:
            filtered = [c for c in coins if self._calc_approx_rsi(c) < 40 and safe_float(c.get("market_cap")) < 500_000_000]
            label = "💎 Перепроданные монеты с потенциалом"
        elif minute == 1:
            filtered = [c for c in coins if safe_float(c.get("total_volume")) / max(safe_float(c.get("market_cap")), 1) > 0.1 and safe_float(c.get("price_change_percentage_24h")) > 0]
            label = "🔥 Аномальный объём — кто-то скупает"
        elif minute == 2:
            filtered = [c for c in coins if safe_float(c.get("price_change_percentage_24h")) > 8 and safe_float(c.get("price_change_percentage_7d_in_currency")) > 10]
            label = "🚀 Сильный моментум — тренд продолжается"
        elif minute == 3:
            filtered = [c for c in coins if 1_000_000 < safe_float(c.get("market_cap")) < 50_000_000 and safe_float(c.get("total_volume")) > 500_000]
            label = "💎 Малая капа с высоким объёмом"
        elif minute == 4:
            filtered = [c for c in coins if safe_float(c.get("price_change_percentage_7d_in_currency")) < -15 and safe_float(c.get("price_change_percentage_24h")) > 2]
            label = "📈 Восстановление после падения"
        else:
            filtered = [c for c in coins if safe_float(c.get("price_change_percentage_24h")) > 5 and safe_float(c.get("market_cap_rank"), 999) > 50]
            label = "⚡ Малоизвестные монеты в движении"
        if len(filtered) < 8:
            filtered = sorted(coins, key=lambda x: abs(safe_float(x.get("price_change_percentage_24h"))), reverse=True)
        random.shuffle(filtered)
        selected = filtered[:10]
        lines = [f"🔍 *{label}*"]
        for c in selected:
            sym = str(c.get("symbol", "")).upper()
            price = safe_float(c.get("current_price"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            mcap = safe_float(c.get("market_cap"))
            vol = safe_float(c.get("total_volume"))
            vol_ratio = vol / max(mcap, 1) * 100
            rsi_approx = self._calc_approx_rsi(c)
            sig = get_signal(rsi_approx, None, ch24, ch7, vol_ratio / 100)
            entry, sl, tp = price, price * 0.93, price * 1.15
            lines.append(
                f"\n{'🟢' if sig['type'] in ['buy','watch'] else '🔴' if sig['type'] in ['sell','caution'] else '⚪'} *{sym}* — {self._fmt_price(price)}\n"
                f"📊 Капа: {self._fmt_usd(mcap)} | Vol: {self._fmt_usd(vol)}\n"
                f"📈 24ч: {ch24:+.1f}% | 7д: {ch7:+.1f}%\n"
                f"🎯 Вход: {self._fmt_price(entry)} | Стоп: {self._fmt_price(sl)} | Цель: {self._fmt_price(tp)}\n"
                f"⚡ /analyze {sym}"
            )
        return "\n".join(lines)

    async def find_overbought(self) -> str:
        coins = await self.fetch_all_coins()
        over = []
        for c in coins:
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            rsi = max(5, min(95, 50 + ch7 * 0.5 + ch24 * 0.4))
            if rsi > 62:
                over.append({**c, "rsi_approx": rsi})
        over.sort(key=lambda x: x["rsi_approx"], reverse=True)
        top = over[:12] if over else sorted(coins, key=lambda x: safe_float(x.get("price_change_percentage_24h")), reverse=True)[:12]
        lines = ["🔥 *Перекупленные монеты (RSI > 62)*"]
        for c in top:
            sym = str(c.get("symbol", "")).upper()
            price = safe_float(c.get("current_price"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            rsi = safe_float(c.get("rsi_approx"), 65)
            correction = price * (1 - (rsi - 50) / 200)
            lines.append(
                f"\n🔴 *{sym}* | RSI: {rsi:.0f}\n"
                f"💰 {self._fmt_price(price)} | 📈 {ch24:+.1f}%\n"
                f"🏦 Капа: {self._fmt_usd(safe_float(c.get('market_cap')))}\n"
                f"📉 Ожидаемая коррекция: {self._fmt_price(correction)}\n"
                f"⚡ /analyze {sym}"
            )
        return "\n".join(lines)

    async def find_gems(self) -> list[dict[str, Any]]:
        coins = await self.fetch_all_coins()
        gems = []
        for c in coins:
            mcap = safe_float(c.get("market_cap"))
            vol = safe_float(c.get("total_volume"))
            if mcap <= 0:
                continue
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            ath = safe_float(c.get("ath"))
            price = safe_float(c.get("current_price"))
            rank = int(safe_float(c.get("market_cap_rank"), 9999))
            vol_ratio = vol / mcap
            ath_drop = ((price - ath) / ath * 100) if ath > 0 else 0
            score = 0
            if mcap < 1_000_000:
                score += 40
            elif mcap < 10_000_000:
                score += 30
            elif mcap < 50_000_000:
                score += 20
            elif mcap < 200_000_000:
                score += 10
            if vol_ratio > 0.5:
                score += 30
            elif vol_ratio > 0.2:
                score += 20
            elif vol_ratio > 0.1:
                score += 10
            if ath_drop < -90:
                score += 25
            elif ath_drop < -80:
                score += 15
            elif ath_drop < -70:
                score += 8
            if ch24 > 30:
                score += 20
            elif ch24 > 10:
                score += 12
            elif ch24 > 3:
                score += 5
            elif ch24 < -20:
                score -= 15
            if ch7 > 50:
                score += 15
            elif ch7 > 20:
                score += 8
            elif ch7 < -30:
                score -= 10
            if rank > 200 and vol > 1_000_000:
                score += 10
            if score >= 30:
                gems.append({**c, "gem_score": min(100, score), "vol_ratio": vol_ratio, "ath_drop": ath_drop})
        gems.sort(key=lambda x: x["gem_score"], reverse=True)
        top = gems[:50]
        random.shuffle(top)
        selected = [g for g in top if g.get("id") not in self._last_gems][:8]
        if len(selected) < 8:
            selected = top[:8]
        if len(selected) < 8:
            selected = sorted(coins, key=lambda x: safe_float(x.get("total_volume")) / max(safe_float(x.get("market_cap")), 1), reverse=True)[:8]
        self._last_gems = {str(x.get("id")) for x in selected if x.get("id")}
        return selected[:8]

    async def find_gems_text(self) -> str:
        selected = await self.find_gems()
        if len(selected) < 8:
            coins = await self.fetch_all_coins()
            extra = sorted(
                coins,
                key=lambda x: safe_float(x.get("total_volume")) / max(safe_float(x.get("market_cap")), 1),
                reverse=True,
            )[:8]
            selected = extra[:8]
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
        lines = ["💎 *GEM FINDER — Монеты с потенциалом x10-x1000*"]
        for i, c in enumerate(selected[:8]):
            sym = str(c.get("symbol", "")).upper()
            price = safe_float(c.get("current_price"))
            mcap = safe_float(c.get("market_cap"))
            vol = safe_float(c.get("total_volume"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            vol_ratio = vol / max(mcap, 1) * 100
            ath = safe_float(c.get("ath"))
            ath_drop = ((price - ath) / ath * 100) if ath > 0 else 0
            score = int(safe_float(c.get("gem_score"), 45))
            potential = "🚀 x100-x1000" if mcap < 1_000_000 else "🚀 x20-x100" if mcap < 10_000_000 else "📈 x10-x20" if mcap < 50_000_000 else "📈 x5-x10"
            lines.append(
                f"\n{medals[i]} *{sym}* — Score: {score}/100\n"
                f"💰 {self._fmt_price(price)} | Капа: {self._fmt_usd(mcap)}\n"
                f"📊 Объём: {self._fmt_usd(vol)} ({vol_ratio:.1f}% от капы)\n"
                f"📈 24ч: {ch24:+.1f}% | 7д: {ch7:+.1f}% | От ATH: {ath_drop:.0f}%\n"
                f"🎯 Потенциал: {potential}\n"
                f"⚡ /analyze {sym}"
            )
        return "\n".join(lines)

    async def top_signals(self) -> str:
        coins = await self.fetch_all_coins()
        scored = []
        for c in coins:
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            vol = safe_float(c.get("total_volume"))
            mcap = max(safe_float(c.get("market_cap")), 1)
            vol_ratio = vol / mcap
            rsi = max(5, min(95, 50 + ch7 * 0.4 + ch24 * 0.3))
            sig = get_signal(rsi, None, ch24, ch7, vol_ratio)
            scored.append({**c, "sig": sig, "rsi": rsi})
        buys = [c for c in scored if c["sig"]["type"] in ["buy", "watch"]]
        sells = [c for c in scored if c["sig"]["type"] in ["sell", "caution"]]
        buys.sort(key=lambda x: x["sig"]["score"], reverse=True)
        sells.sort(key=lambda x: x["sig"]["score"])
        if len(buys) < 3:
            buys = sorted(scored, key=lambda x: safe_float(x.get("price_change_percentage_24h")), reverse=True)[:5]
        if len(sells) < 3:
            sells = sorted(scored, key=lambda x: safe_float(x.get("price_change_percentage_24h")))[:5]
        lines = ["📈 *ТОП СИГНАЛЫ РЫНКА*", "🟢 *Покупка:*"]
        for c in buys[:5]:
            sym = str(c.get("symbol", "")).upper()
            price = safe_float(c.get("current_price"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            rsi = safe_float(c.get("rsi"), 50)
            score = safe_float((c.get("sig") or {}).get("score"), 50)
            lines.append(
                f"• *{sym}* | Score {score:.0f} | RSI {rsi:.0f}\n"
                f"  💰 {self._fmt_price(price)} ({ch24:+.1f}%)\n"
                f"  🛑 Стоп: {self._fmt_price(price*0.93)} | 🎯 Цель: {self._fmt_price(price*1.12)}"
            )
        lines.append("\n🔴 *Продажа/Осторожно:*")
        for c in sells[:5]:
            sym = str(c.get("symbol", "")).upper()
            price = safe_float(c.get("current_price"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            rsi = safe_float(c.get("rsi"), 50)
            lines.append(f"• *{sym}* | RSI {rsi:.0f} | {ch24:+.1f}%\n  💰 {self._fmt_price(price)} — возможна коррекция")
        return "\n".join(lines)

    async def full_analysis(self, symbol: str) -> str:
        cid = await self._resolve_id(symbol)
        cache_key = f"fa:{cid}"
        cached = self.cache.get(cache_key, ttl=180)
        if cached:
            return cached
        detail = await self._get_json(
            f"https://api.coingecko.com/api/v3/coins/{cid}",
            {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"},
            ttl=120,
        )
        if not detail:
            return "❌ Не удалось получить данные монеты."
        md = detail.get("market_data") or {}
        price = safe_float((md.get("current_price") or {}).get("usd"))
        ch24 = safe_float(md.get("price_change_percentage_24h"))
        ch7 = safe_float((md.get("price_change_percentage_7d_in_currency") or {}).get("usd"))
        mcap = safe_float((md.get("market_cap") or {}).get("usd"))
        vol = safe_float((md.get("total_volume") or {}).get("usd"))
        ath = safe_float((md.get("ath") or {}).get("usd"))
        ath_pct = safe_float((md.get("ath_change_percentage") or {}).get("usd"))
        rank = int(safe_float(md.get("market_cap_rank"), 9999))
        rsi = max(5, min(95, 50 + ch7 * 0.4 + ch24 * 0.3))
        macd_hist = (ch24 * 0.01) + (ch7 * 0.005)
        vol_ratio = vol / max(mcap, 1)
        sig = get_signal(rsi, macd_hist, ch24, ch7, vol_ratio)
        all_coins = await self.fetch_all_coins()
        market_size = max(len(all_coins), 1)
        percentile = max(1, min(100, int((1 - (rank / market_size)) * 100)))
        similar = sorted(
            [c for c in all_coins if c.get("id") != detail.get("id") and c.get("market_cap")],
            key=lambda x: abs(safe_float(x.get("market_cap")) - mcap),
        )[:3]
        similar_txt = ", ".join(str(c.get("symbol", "")).upper() for c in similar) or "ETH, SOL, AVAX"
        avg_vol_ctx = sum(safe_float(c.get("total_volume")) for c in all_coins[:200]) / max(len(all_coins[:200]), 1)
        vol_ctx = "выше среднего" if vol > avg_vol_ctx else "ниже среднего"
        text = (
            f"📊 *{str(detail.get('name','')).upper()} ({str(detail.get('symbol','')).upper()})* #{rank}\n"
            f"💰 Цена: {self._fmt_price(price)}\n"
            f"📈 24ч: {ch24:+.2f}% | 7д: {ch7:+.2f}%\n"
            f"🏦 Капа: {self._fmt_usd(mcap)} | Объём: {self._fmt_usd(vol)}\n"
            f"📉 От ATH: {ath_pct:.1f}% (ATH {self._fmt_price(ath)})\n\n"
            f"━━━ СИГНАЛ ━━━\n{sig['signal']} | Score: {sig['score']}/100\n"
            + "\n".join(f"• {r}" for r in sig["reasons"]) +
            f"\n\n📌 Позиция в рынке: топ {percentile}%\n"
            f"📊 Объём vs рынок: {vol_ctx}\n"
            f"👀 Похожие монеты: {similar_txt}\n"
            f"🎯 Вход: {self._fmt_price(price)} | Стоп: {self._fmt_price(price*0.93)} | Цель: {self._fmt_price(price*1.15)}\n"
            f"⚡ /analyze {str(detail.get('symbol','')).upper()}"
        )
        self.cache.set(cache_key, text)
        return text

    async def find_scam_whales_results(self) -> list[dict[str, Any]]:
        coins = await self.fetch_all_coins()
        out = []
        for c in coins:
            mcap = safe_float(c.get("market_cap"))
            vol = safe_float(c.get("total_volume"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            rank = int(safe_float(c.get("market_cap_rank"), 9999))
            if mcap <= 0:
                continue
            vm = vol / mcap
            risk = 20
            flags = []
            if mcap > 10_000_000 and ch24 < -10 and vm > 0.15:
                risk += 35; flags.append("WHALE DUMP")
            if ch7 > 50 and ch24 < -5 and vm > 0.2:
                risk += 35; flags.append("PUMP AND DUMP")
            if rank > 200 and mcap > 50_000_000 and vm > 0.3:
                risk += 25; flags.append("NEW SCAM")
            out.append(
                {
                    "symbol": str(c.get("symbol", "")).upper(),
                    "risk": min(100, risk),
                    "price": safe_float(c.get("current_price")),
                    "mcap": mcap,
                    "vol": vol,
                    "ch24": ch24,
                    "ch7": ch7,
                    "reasons": flags or ["Смешанные риски"],
                    "verdict": "ВЕРОЯТНЫЙ ПАМП-И-ДАМП" if risk >= 70 else "ПОДОЗРИТЕЛЬНО",
                }
            )
        out.sort(key=lambda x: x["risk"], reverse=True)
        return out[:8]

    async def find_scam_whales(self) -> str:
        coins = await self.find_scam_whales_results()
        lines = ["🚨 *СКАМ-ДЕТЕКТОР: Монеты с признаками манипуляций*"]
        for c in coins[:8]:
            vm = c["vol"] / max(c["mcap"], 1) * 100
            lines.append(
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🔴 *{c['symbol']}* — Риск: {c['risk']}/100\n"
                f"💰 Цена: {self._fmt_price(c['price'])}\n"
                f"🏦 Капа: {self._fmt_usd(c['mcap'])} | Объём: {self._fmt_usd(c['vol'])}\n"
                f"📊 Vol/MCap: {vm:.1f}% ⚠️\n"
                f"📈 7д: {c['ch7']:+.1f}% | 24ч: {c['ch24']:+.1f}%\n"
                f"🚨 Признаки:\n- " + "\n- ".join(c["reasons"]) +
                f"\n💡 Вывод: {c['verdict']}\n⚡ /analyze {c['symbol']}\n━━━━━━━━━━━━━━━━━━━━"
            )
        return "\n".join(lines)
