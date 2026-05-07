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
        self.cache = SmartCache()
        self._last_gems: set[str] = set()

    async def close(self) -> None:
        # Backward-compatible hook for shutdown paths.
        self.cache.clear_old()

    async def groq_chat(self, system: str, user: str, max_tokens: int = 180) -> str:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return "Groq недоступен: отсутствует GROQ_API_KEY."
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.6,
            "max_tokens": max_tokens,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                ) as resp:
                    if resp.status >= 400:
                        return "Groq временно недоступен."
                    data = await resp.json(content_type=None)
            return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")).strip() or "Пустой ответ Groq."
        except Exception:
            return "Ошибка сети при запросе к Groq."

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
        for i in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
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

    def _coin_category(self, name: str, symbol: str) -> str:
        txt = f"{name} {symbol}".lower()
        if any(k in txt for k in ["pepe", "doge", "shib", "bonk", "wif", "floki", "meme", "cat", "frog"]):
            return "MEME"
        if any(k in txt for k in ["aave", "uni", "defi", "curve", "maker", "pendle", "comp"]):
            return "DEFI"
        if any(k in txt for k in ["tao", "render", "fet", "wld", "ai", "ocean", "grt", "aioz", "virtual"]):
            return "AI"
        if any(k in txt for k in ["arb", "op", "matic", "zk", "layer", "l2", "optimism", "arbitrum"]):
            return "LAYER2"
        if any(k in txt for k in ["game", "gala", "axs", "mana", "sand", "pixel", "ron"]):
            return "GAMING"
        return "GENERAL"

    async def _coin_detail(self, coin_id: str, ttl: int = 300) -> dict[str, Any]:
        data = await self._get_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "true",
                "developer_data": "true",
                "sparkline": "false",
            },
            ttl=ttl,
        )
        await asyncio.sleep(1.1)
        return data or {}

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
        coins = sorted(await self.fetch_all_coins(), key=lambda x: safe_float(x.get("market_cap")), reverse=True)[:60]
        out: list[dict[str, Any]] = []
        for c in coins:
            cid = str(c.get("id") or "")
            if not cid:
                continue
            mcap = safe_float(c.get("market_cap"))
            vol = safe_float(c.get("total_volume"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            price = safe_float(c.get("current_price"))
            if mcap <= 0 or price <= 0:
                continue
            vm = vol / max(mcap, 1)
            rsi = self._calc_approx_rsi(c)
            risk = 10
            reasons: list[str] = []
            detail = await self._coin_detail(cid, ttl=420)
            md = detail.get("market_data") or {}
            community_score = safe_float(detail.get("community_score"))
            dev_score = safe_float(detail.get("developer_score"))
            ath = safe_float((md.get("ath") or {}).get("usd"), safe_float(c.get("ath")))
            ath_diff = abs(((ath - price) / max(ath, 1)) * 100) if ath > 0 else 100
            genesis = str(detail.get("genesis_date") or "")
            age_days = 9999
            if genesis and len(genesis) >= 10:
                try:
                    y, m, d = genesis.split("-")
                    now = time.time()
                    age_days = int((now - time.mktime((int(y), int(m), int(d), 0, 0, 0, 0, 0, 0))) / 86400)
                except Exception:
                    age_days = 9999
            if age_days < 30:
                risk += 18
                reasons.append(f"Новый листинг: {age_days} дней")
            if vm > 0.5:
                risk += 22
                reasons.append(f"Vol/MCap {vm*100:.1f}% (аномально высокий)")
            elif vm > 0.3:
                risk += 12
                reasons.append(f"Vol/MCap {vm*100:.1f}% (повышенный)")
            if ch7 > 50 and ch24 < -5:
                risk += 20
                reasons.append("Классический pump-dump: резкий рост 7д и слив 24ч")
            if community_score < 20:
                risk += 8
                reasons.append(f"Низкий social score: {community_score:.1f}")
            if dev_score < 20 and ch7 > 15:
                risk += 12
                reasons.append(f"Слабая разработка ({dev_score:.1f}) при сильном росте")
            if ath_diff <= 5 and rsi > 75:
                risk += 16
                reasons.append(f"Около ATH ({ath_diff:.1f}% до пика) и RSI {rsi:.0f}")
            risk = int(max(0, min(100, risk)))
            recommendation = "SAFE"
            if risk >= 75:
                recommendation = "AVOID"
            elif risk >= 55:
                recommendation = "CAUTION"
            elif risk >= 35:
                recommendation = "INVESTIGATE"
            out.append(
                {
                    "symbol": str(c.get("symbol", "")).upper(),
                    "risk": risk,
                    "price": price,
                    "mcap": mcap,
                    "vol": vol,
                    "ch24": ch24,
                    "ch7": ch7,
                    "reasons": reasons or ["Сигналы риска не выражены"],
                    "verdict": recommendation,
                    "age_days": age_days if age_days < 9999 else None,
                    "community_score": community_score,
                    "developer_score": dev_score,
                    "rsi": rsi,
                }
            )
        out.sort(key=lambda x: x["risk"], reverse=True)
        return out[:10]

    async def find_scam_whales(self) -> str:
        coins = await self.find_scam_whales_results()
        lines = ["🚨 *СКАМ-ДЕТЕКТОР: Монеты с признаками манипуляций*"]
        for c in coins[:8]:
            vm = c["vol"] / max(c["mcap"], 1) * 100
            rec_emoji = {"AVOID": "⛔", "CAUTION": "⚠️", "INVESTIGATE": "🕵️", "SAFE": "✅"}.get(c["verdict"], "⚠️")
            lines.append(
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🔴 *{c['symbol']}* — Риск: {c['risk']}/100\n"
                f"💰 Цена: {self._fmt_price(c['price'])}\n"
                f"🏦 Капа: {self._fmt_usd(c['mcap'])} | Объём: {self._fmt_usd(c['vol'])}\n"
                f"📊 Vol/MCap: {vm:.1f}% ⚠️\n"
                f"📈 7д: {c['ch7']:+.1f}% | 24ч: {c['ch24']:+.1f}% | RSI: {c['rsi']:.0f}\n"
                f"👥 Social: {c['community_score']:.1f} | Dev: {c['developer_score']:.1f}\n"
                f"🚨 Признаки:\n- " + "\n- ".join(c["reasons"]) +
                f"\n💡 Рекомендация: {rec_emoji} *{c['verdict']}*\n⚡ /analyze {c['symbol']}\n━━━━━━━━━━━━━━━━━━━━"
            )
        return "\n".join(lines)

    async def find_new_coins(self) -> str:
        trending = await self._get_json("https://api.coingecko.com/api/v3/search/trending", ttl=120)
        await asyncio.sleep(1.2)
        new_list = await self._get_json("https://api.coingecko.com/api/v3/coins/list/new", ttl=300)
        await asyncio.sleep(1.2)
        market = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            {"vs_currency": "usd", "order": "volume_desc", "per_page": 200, "page": 1, "price_change_percentage": "24h,7d"},
            ttl=120,
        )
        trend_ids = {(x.get("item") or {}).get("id") for x in (trending.get("coins") or [])}
        new_ids = {str(x.get("id")) for x in (new_list or []) if x.get("id")}
        rows: list[dict[str, Any]] = []
        for c in market or []:
            cid = str(c.get("id") or "")
            if not cid:
                continue
            detail_needed = cid in trend_ids or cid in new_ids
            age_days = None
            community = 0.0
            if detail_needed:
                detail = await self._coin_detail(cid, ttl=300)
                community = safe_float(detail.get("community_score"))
                gd = str(detail.get("genesis_date") or "")
                if gd and len(gd) >= 10:
                    try:
                        y, m, d = gd.split("-")
                        age_days = int((time.time() - time.mktime((int(y), int(m), int(d), 0, 0, 0, 0, 0, 0))) / 86400)
                    except Exception:
                        age_days = None
            mcap = safe_float(c.get("market_cap"))
            if mcap < 1_000_000:
                continue
            if age_days is not None and age_days > 90:
                continue
            vol = safe_float(c.get("total_volume"))
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            vol_ratio = vol / max(mcap, 1)
            if vol_ratio < 0.04 and ch24 < 3:
                continue
            cat = self._coin_category(str(c.get("name", "")), str(c.get("symbol", "")))
            risk = 30
            if age_days is not None and age_days < 30:
                risk += 20
            if vol_ratio > 0.5:
                risk += 18
            if ch24 > 20:
                risk += 10
            if community < 15 and community > 0:
                risk += 10
            why = []
            if cid in trend_ids:
                why.append("в trending CoinGecko")
            if cid in new_ids:
                why.append("новый листинг")
            if vol_ratio > 0.1:
                why.append("растущий объём")
            if ch24 > 10:
                why.append(f"импульс +{ch24:.1f}% за 24ч")
            rows.append(
                {
                    "symbol": str(c.get("symbol", "")).upper(),
                    "name": str(c.get("name", "")),
                    "mcap": mcap,
                    "age_days": age_days,
                    "category": cat,
                    "risk": int(min(100, risk)),
                    "why": ", ".join(why) or "рыночный интерес",
                }
            )
        rows.sort(key=lambda x: (x["risk"], x["mcap"]), reverse=True)
        lines = ["🆕 *NEW COINS FINDER — новые монеты с потенциалом*"]
        for c in rows[:10]:
            age_txt = f"{c['age_days']} дн." if c["age_days"] is not None else "n/a"
            lines.append(
                f"\n*{c['symbol']}* ({c['name']})\n"
                f"🏦 Капа: {self._fmt_usd(c['mcap'])} | ⏳ Возраст: {age_txt}\n"
                f"🏷️ Категория: {c['category']} | ⚠️ Риск: {c['risk']}/100\n"
                f"💡 Почему интересно: {c['why']}\n"
                f"⚡ /analyze {c['symbol']}"
            )
        return "\n".join(lines) if len(lines) > 1 else "🆕 Новых монет с подходящими метриками пока нет."

    async def meme_tracker(self) -> str:
        watch = {"PEPE", "WIF", "BONK", "DOGE", "SHIB", "FLOKI", "BRETT", "TURBO", "MOG", "POPCAT", "NEIRO", "PENGU"}
        rows = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            {"vs_currency": "usd", "category": "meme-token", "order": "volume_desc", "per_page": 100, "page": 1, "price_change_percentage": "24h,7d"},
            ttl=120,
        )
        await asyncio.sleep(1.1)
        if not isinstance(rows, list):
            rows = []
        sel = [c for c in rows if str(c.get("symbol", "")).upper() in watch][:12]
        lines = ["🐸 *MEME COINS TRACKER*"]
        for c in sel:
            sym = str(c.get("symbol", "")).upper()
            ch24 = safe_float(c.get("price_change_percentage_24h"))
            ch7 = safe_float(c.get("price_change_percentage_7d_in_currency"))
            mcap = safe_float(c.get("market_cap"))
            vol = safe_float(c.get("total_volume"))
            rsi = self._calc_approx_rsi(c)
            vol_ratio = vol / max(mcap, 1)
            whale = "🐋" if vol_ratio > 0.35 else "—"
            pump = "🚨 PUMP ALERT" if ch24 > 20 and vol_ratio > 0.3 else "—"
            lines.append(
                f"\n*{sym}* | {self._fmt_price(safe_float(c.get('current_price')))}\n"
                f"📈 24ч: {ch24:+.1f}% | 7д: {ch7:+.1f}% | RSI: {rsi:.0f}\n"
                f"📊 Vol anomaly: {vol_ratio*100:.1f}% | Whale: {whale}\n"
                f"{pump}\n"
                f"⚡ /analyze {sym}"
            )
        return "\n".join(lines) if len(lines) > 1 else "🐸 Мем-токены временно недоступны."

    async def defi_tracker(self) -> str:
        rows = await self._get_json("https://api.llama.fi/protocols", ttl=300)
        if not isinstance(rows, list):
            return "❌ DeFiLlama временно недоступен."
        rows = sorted(rows, key=lambda x: safe_float(x.get("tvl")), reverse=True)
        lines = ["🏦 *DEFI TRACKER — топ протоколы по TVL*"]
        for r in rows[:10]:
            ch1 = safe_float((r.get("change_1d") if r.get("change_1d") is not None else r.get("change_1h")))
            ch7 = safe_float(r.get("change_7d"))
            signal = "🟢 TVL растет" if ch1 > 0 and ch7 > 0 else "🟡 смешанная динамика" if ch7 > -5 else "🔴 TVL падает"
            token = r.get("symbol") or "N/A"
            lines.append(
                f"\n*{r.get('name','?')}* ({token})\n"
                f"💰 TVL: {self._fmt_usd(safe_float(r.get('tvl')))}\n"
                f"📈 TVL 24ч: {ch1:+.2f}% | 7д: {ch7:+.2f}%\n"
                f"📌 Сигнал: {signal}"
            )
        return "\n".join(lines)

    async def ai_tracker(self) -> str:
        ai_watch = {"TAO", "RENDER", "FET", "WLD", "AIOZ", "VIRTUAL", "GRT", "OCEAN", "RNDR"}
        cat = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            {"vs_currency": "usd", "category": "artificial-intelligence", "order": "market_cap_desc", "per_page": 100, "page": 1, "price_change_percentage": "24h,7d"},
            ttl=180,
        )
        await asyncio.sleep(1.1)
        btc_row = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            {"vs_currency": "usd", "ids": "bitcoin", "price_change_percentage": "7d"},
            ttl=120,
        )
        btc_7d = safe_float(((btc_row or [{}])[0] if isinstance(btc_row, list) else {}).get("price_change_percentage_7d_in_currency"))
        rows = [c for c in (cat or []) if str(c.get("symbol", "")).upper() in ai_watch]
        sector_7d = sum(safe_float(c.get("price_change_percentage_7d_in_currency")) for c in rows) / max(len(rows), 1)
        delta = sector_7d - btc_7d
        lines = [
            "🤖 *AI TOKENS TRACKER*",
            f"Сектор AI за 7д: {sector_7d:+.2f}% | BTC: {btc_7d:+.2f}% | Alpha: {delta:+.2f}%",
        ]
        for c in rows[:10]:
            lines.append(
                f"\n*{str(c.get('symbol','')).upper()}* — {self._fmt_price(safe_float(c.get('current_price')))}\n"
                f"📈 24ч: {safe_float(c.get('price_change_percentage_24h')):+.2f}% | 7д: {safe_float(c.get('price_change_percentage_7d_in_currency')):+.2f}%\n"
                f"🏦 Капа: {self._fmt_usd(safe_float(c.get('market_cap')))}"
            )
        return "\n".join(lines)
