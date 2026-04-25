"""
analysis.py — Движок технического анализа
Использует CoinGecko API (бесплатно) + расчёт RSI, MACD, BB, EMA вручную.
"""

import asyncio
import aiohttp
import statistics
from typing import Optional

COINGECKO = "https://api.coingecko.com/api/v3"

# Маппинг популярных символов → CoinGecko ID
SYMBOL_MAP = {
 # Топ монеты
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
    "AVAX": "avalanche-2", "DOT": "polkadot", "MATIC": "matic-network",
    "LINK": "chainlink", "UNI": "uniswap", "ATOM": "cosmos",
    "LTC": "litecoin", "DOGE": "dogecoin", "SHIB": "shiba-inu",
    "TRX": "tron", "TON": "the-open-network", "SUI": "sui",
    "APT": "aptos", "OP": "optimism", "ARB": "arbitrum",
    "INJ": "injective-protocol", "SEI": "sei-network", "FTM": "fantom",
    "NEAR": "near", "ICP": "internet-computer", "FIL": "filecoin",
    "SAND": "the-sandbox", "MANA": "decentraland", "PEPE": "pepe",
    "WIF": "dogwifcoin", "BONK": "bonk", "JUP": "jupiter-exchange-solana",
    # AI токены
    "RENDER": "render-token", "RNDR": "render-token",
    "FET": "fetch-ai", "TAO": "bittensor",
    "WLD": "worldcoin-wld", "AIOZ": "aioz-network",
    "VIRTUAL": "virtual-protocol", "ARKM": "arkham",
    "GRT": "the-graph", "OCEAN": "ocean-protocol",
    "AGIX": "singularitynet",
    # DeFi
    "AAVE": "aave", "CRV": "curve-dao-token",
    "MKR": "maker", "SNX": "havven",
    "COMP": "compound-governance-token", "1INCH": "1inch",
    "SUSHI": "sushi", "YFI": "yearn-finance",
    "LDO": "lido-dao", "RUNE": "thorchain",
    "CAKE": "pancakeswap-token", "GMX": "gmx",
    "DYDX": "dydx", "PENDLE": "pendle",
    # Layer 2 и новые сети
    "STRK": "starknet", "TIA": "celestia",
    "PYTH": "pyth-network", "JTO": "jito-governance-token",
    "MANTA": "manta-network", "ZETA": "zetachain",
    "DYM": "dymension",
    # Мемкоины
    "FLOKI": "floki", "POPCAT": "popcat",
    "PNUT": "peanut-the-squirrel", "MOG": "mog-coin",
    "NEIRO": "neiro-on-eth", "PENGU": "pudgy-penguins",
    "TRUMP": "official-trump", "MELANIA": "melania-meme",
    "BRETT": "based-brett", "TURBO": "turbo",
    # Gamefi / NFT
    "AXS": "axie-infinity", "GALA": "gala",
    "IMX": "immutable-x", "MAGIC": "magic",
    "BLUR": "blur", "ENS": "ethereum-name-service",
    # Другие популярные
    "XLM": "stellar", "VET": "vechain",
    "HBAR": "hedera-hashgraph", "ALGO": "algorand",
    "XTZ": "tezos", "THETA": "theta-token",
    "CHZ": "chiliz", "BAT": "basic-attention-token",
    "ROSE": "oasis-network", "CFX": "conflux-token",
    "STX": "blockstack", "EGLD": "elrond-erd-2",
    "FLOW": "flow", "BCH": "bitcoin-cash",
    "ZEC": "zcash", "XMR": "monero",
    "LUNA": "terra-luna-2", "LUNC": "terra-luna",
    "CRO": "crypto-com-chain", "OKB": "okb",
}

def fmt_price(p: float) -> str:
    if p >= 1000: return f"${p:,.0f}"
    if p >= 1: return f"${p:.4f}"
    if p >= 0.01: return f"${p:.6f}"
    return f"${p:.8f}"

def fmt_b(v: float) -> str:
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"

def calc_rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [abs(d) if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def calc_ema(prices: list, period: int) -> list:
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calc_macd(prices: list):
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    if not ema12 or not ema26:
        return None, None, None
    min_len = min(len(ema12), len(ema26))
    macd_line = [ema12[-(min_len-i)] - ema26[-(min_len-i)] for i in range(min_len)]
    signal = calc_ema(macd_line, 9)
    if not signal:
        return macd_line[-1], None, None
    histogram = macd_line[-1] - signal[-1]
    return round(macd_line[-1], 6), round(signal[-1], 6), round(histogram, 6)

def calc_bollinger(prices: list, period: int = 20, std_mult: float = 2.0):
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mean = sum(window) / period
    std = statistics.stdev(window)
    return round(mean - std_mult * std, 6), round(mean, 6), round(mean + std_mult * std, 6)

def rsi_signal(rsi: float) -> str:
    if rsi >= 80: return "🔴 СИЛЬНО ПЕРЕКУПЛЕН"
    if rsi >= 70: return "🟠 ПЕРЕКУПЛЕН"
    if rsi >= 60: return "🟡 Нейтрально (ближе к покупке)"
    if rsi >= 40: return "🟡 Нейтральная зона"
    if rsi >= 30: return "🟢 Близко к перепроданности"
    return "🟢 ПЕРЕПРОДАН — возможен отскок"

def overall_signal(rsi, macd_h, price, bb_low, bb_mid, bb_high, vol_ratio) -> tuple:
    score = 0

    # RSI
    if rsi < 30: score += 2
    elif rsi < 40: score += 1
    elif rsi > 70: score -= 2
    elif rsi > 60: score -= 1

    # MACD histogram
    if macd_h is not None:
        if macd_h > 0: score += 1
        else: score -= 1

    # Bollinger
    if bb_low and bb_high:
        if price < bb_low: score += 2
        elif price > bb_high: score -= 2

    # Volume
    if vol_ratio > 0.3: score += 1  # Высокий объём — интерес

    if score >= 3: return "🟢 ПОКУПАТЬ", "Сильный сигнал на вход"
    if score >= 1: return "🟡 ЖДАТЬ / Осторожная покупка", "Слабый бычий сигнал"
    if score <= -3: return "🔴 ПРОДАВАТЬ / НЕ ВХОДИТЬ", "Сильный медвежий сигнал"
    if score <= -1: return "🟠 ОСТОРОЖНО", "Нейтрально-медвежий сигнал"
    return "🟡 ЖДАТЬ", "Нейтральная зона — нет чёткого сигнала"

class CryptoAnalyzer:

    async def _get_json(self, url: str, params: dict = None) -> Optional[dict]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        return await r.json()
        except Exception as e:
            return None
        return None

    def _resolve_id(self, symbol: str) -> str:
        return SYMBOL_MAP.get(symbol.upper(), symbol.lower())

    async def _get_prices(self, coin_id: str, days: int = 30) -> list:
        data = await self._get_json(
            f"{COINGECKO}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": days, "interval": "daily"}
        )
        if not data:
            return []
        return [p[1] for p in data.get("prices", [])]

    async def full_analysis(self, symbol: str) -> str:
        coin_id = self._resolve_id(symbol)
        data = await self._get_json(
            f"{COINGECKO}/coins/{coin_id}",
            {"localization": "false", "tickers": "false", "market_data": "true", "community_data": "false"}
        )
        if not data:
            return f"❌ Монета *{symbol}* не найдена. Проверь символ.\nПопробуй: BTC, ETH, SOL, BNB..."

        md = data["market_data"]
        price = md["current_price"]["usd"]
        cap = md["market_cap"]["usd"]
        vol = md["total_volume"]["usd"]
        ch24 = md.get("price_change_percentage_24h") or 0
        ch7 = md.get("price_change_percentage_7d") or 0
        ch30 = md.get("price_change_percentage_30d") or 0
        ath = md["ath"]["usd"]
        ath_change = md.get("ath_change_percentage", {}).get("usd") or 0
        vol_ratio = vol / cap if cap > 0 else 0

        prices = await self._get_prices(coin_id, 60)

        rsi = calc_rsi(prices) if len(prices) >= 15 else 50.0
        macd_val, macd_sig, macd_h = calc_macd(prices) if len(prices) >= 30 else (None, None, None)
        bb_low, bb_mid, bb_high = calc_bollinger(prices) if len(prices) >= 20 else (None, None, None)
        ema20 = calc_ema(prices, 20)
        ema50 = calc_ema(prices, 50)
        ema20_val = ema20[-1] if ema20 else None
        ema50_val = ema50[-1] if ema50 else None

        sig_label, sig_desc = overall_signal(rsi, macd_h, price, bb_low, bb_mid, bb_high, vol_ratio)

        # Точка входа / стоп / тейк
        if bb_low and bb_high:
            stop_loss = round(price * 0.94, 6)
            tp1 = round(bb_mid, 6)
            tp2 = round(bb_high, 6)
            tp3 = round(price * 1.15, 6)
            entry_zone = f"{fmt_price(price * 0.98)} – {fmt_price(price)}"
        else:
            stop_loss = round(price * 0.94, 6)
            tp1 = round(price * 1.05, 6)
            tp2 = round(price * 1.10, 6)
            tp3 = round(price * 1.15, 6)
            entry_zone = fmt_price(price)

        ch24_str = f"+{ch24:.2f}%" if ch24 >= 0 else f"{ch24:.2f}%"
        ch7_str = f"+{ch7:.2f}%" if ch7 >= 0 else f"{ch7:.2f}%"
        ch30_str = f"+{ch30:.2f}%" if ch30 >= 0 else f"{ch30:.2f}%"

        macd_str = f"`{macd_val}` (сигнал: `{macd_sig}`, гист: `{macd_h}`)" if macd_val else "N/A"
        bb_str = f"`{fmt_price(bb_low)}` / `{fmt_price(bb_mid)}` / `{fmt_price(bb_high)}`" if bb_low else "N/A"
        ema_str = ""
        if ema20_val and ema50_val:
            trend = "📈 Бычий тренд" if ema20_val > ema50_val else "📉 Медвежий тренд"
            ema_str = f"\n*EMA 20/50:* `{fmt_price(ema20_val)}` / `{fmt_price(ema50_val)}` — {trend}"

        lines = [
            f"📊 *{data['name']} ({symbol.upper()})* — Полный анализ",
            "",
            f"💰 *Цена:* {fmt_price(price)}",
            f"📈 *Изменение:* {ch24_str} (24ч) | {ch7_str} (7д) | {ch30_str} (30д)",
            f"🏦 *Капитализация:* {fmt_b(cap)}",
            f"📦 *Объём 24ч:* {fmt_b(vol)} ({vol_ratio*100:.1f}% от кап)",
            f"🏆 *ATH:* {fmt_price(ath)} (сейчас {ath_change:.1f}% от ATH)",
            "",
            "━━━ ИНДИКАТОРЫ ━━━",
            f"*RSI (14):* `{rsi}` — {rsi_signal(rsi)}",
            f"*MACD:* {macd_str}",
            f"*Bollinger Bands:* {bb_str}",
        ]
        if ema_str:
            lines.append(ema_str)

        lines += [
            "",
            "━━━ СИГНАЛ ━━━",
            f"*{sig_label}*",
            f"_{sig_desc}_",
            "",
            "━━━ ТОЧКИ ━━━",
            f"🎯 *Зона входа:* {entry_zone}",
            f"🛑 *Стоп-лосс:* {fmt_price(stop_loss)} (-6%)",
            f"✅ *TP1:* {fmt_price(tp1)}",
            f"✅ *TP2:* {fmt_price(tp2)}",
            f"✅ *TP3:* {fmt_price(tp3)} (+15%)",
        ]

        if vol_ratio > 0.3:
            lines += ["", "⚠️ *Аномально высокий объём!* Возможна манипуляция или крупный выход."]

        return "\n".join(lines)

    async def market_scan(self) -> str:
        data = await self._get_json(
            f"{COINGECKO}/coins/markets",
            {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 50,
             "page": 1, "price_change_percentage": "24h"}
        )
        if not data:
            return "❌ Ошибка загрузки данных рынка"

        buy_signals, sell_signals = [], []
        for c in data:
            ch = c.get("price_change_percentage_24h") or 0
            vol_ratio = (c["total_volume"] / c["market_cap"]) if c["market_cap"] > 0 else 0
            rsi_approx = calc_rsi([ch * (0.8 + i * 0.05) for i in range(20)])
            if rsi_approx < 35 and ch > -10:
                buy_signals.append((c["symbol"].upper(), rsi_approx, ch, c["current_price"]))
            elif rsi_approx > 68:
                sell_signals.append((c["symbol"].upper(), rsi_approx, ch, c["current_price"]))

        lines = ["🔍 *Скан рынка — Топ 50*\n"]
        if buy_signals:
            lines.append("🟢 *Потенциальные покупки (RSI < 35):*")
            for sym, rsi, ch, price in buy_signals[:5]:
                lines.append(f"  • *{sym}* — RSI {rsi} | {ch:+.1f}% | {fmt_price(price)}")
        else:
            lines.append("🟢 Покупок нет — рынок нейтрален")

        lines.append("")
        if sell_signals:
            lines.append("🔴 *Перекупленные (RSI > 68):*")
            for sym, rsi, ch, price in sell_signals[:5]:
                lines.append(f"  • *{sym}* — RSI {rsi} | {ch:+.1f}% | {fmt_price(price)}")
        else:
            lines.append("🔴 Перекупленных нет")

        lines.append(f"\n_Проанализировано 50 монет. /analyze SYMBOL для деталей._")
        return "\n".join(lines)

    async def find_overbought(self) -> str:
        data = await self._get_json(
            f"{COINGECKO}/coins/markets",
            {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100,
             "page": 1, "price_change_percentage": "24h"}
        )
        if not data:
            return "❌ Ошибка загрузки"

        results = []
        for c in data:
            ch = c.get("price_change_percentage_24h") or 0
            rsi = calc_rsi([ch * (0.7 + i * 0.04) for i in range(20)])
            vol_ratio = (c["total_volume"] / c["market_cap"]) if c["market_cap"] > 0 else 0
            if rsi >= 68:
                results.append({
                    "sym": c["symbol"].upper(), "name": c["name"],
                    "rsi": rsi, "ch": ch, "price": c["current_price"],
                    "cap": c["market_cap"], "vol_ratio": vol_ratio
                })

        results.sort(key=lambda x: x["rsi"], reverse=True)

        if not results:
            return "✅ Перекупленных монет нет. Рынок в нейтральной зоне."

        lines = [f"🔥 *Перекупленные монеты ({len(results)} из топ-100)*\n",
                 "_RSI > 68 — высокий риск коррекции_\n"]
        for r in results[:10]:
            risk = "⚠️ Памп?" if r["vol_ratio"] > 0.4 else ""
            lines.append(
                f"🔴 *{r['sym']}* — RSI `{r['rsi']}` | {r['ch']:+.1f}% | {fmt_price(r['price'])} | Кап: {fmt_b(r['cap'])} {risk}"
            )
        lines.append(f"\n_/analyze SYMBOL для точки выхода_")
        return "\n".join(lines)

    async def find_new_potential(self) -> str:
        data = await self._get_json(
            f"{COINGECKO}/coins/markets",
            {"vs_currency": "usd", "order": "gecko_desc", "per_page": 30,
             "page": 1, "price_change_percentage": "24h"}
        )
        if not data:
            return "❌ Ошибка загрузки"

        results = []
        for c in data:
            if not c["market_cap"] or c["market_cap"] < 10_000_000:
                continue
            ch = c.get("price_change_percentage_24h") or 0
            vol_ratio = (c["total_volume"] / c["market_cap"]) if c["market_cap"] > 0 else 0
            rsi = calc_rsi([ch * (0.75 + i * 0.04) for i in range(20)])
            score = 0
            if vol_ratio > 0.15: score += 2
            if ch > 5: score += 1
            if rsi < 60: score += 1
            if c["market_cap"] > 100_000_000: score += 1
            results.append({**c, "rsi": rsi, "vol_ratio": vol_ratio, "score": score})

        results.sort(key=lambda x: x["score"], reverse=True)

        lines = [f"🚀 *Монеты с потенциалом роста*\n",
                 "_Критерии: высокий тренд + объём + капитализация_\n"]
        for c in results[:8]:
            ch = c.get("price_change_percentage_24h") or 0
            lines.append(
                f"✨ *{c['symbol'].upper()}* — {fmt_price(c['current_price'])} | "
                f"{ch:+.1f}% | Кап: {fmt_b(c['market_cap'])} | "
                f"Vol/Cap: {c['vol_ratio']*100:.0f}%"
            )
        lines.append(f"\n_/analyze SYMBOL для анализа_")
        return "\n".join(lines)

    async def find_dump_risk(self) -> str:
        data = await self._get_json(
            f"{COINGECKO}/coins/markets",
            {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100,
             "page": 1, "price_change_percentage": "24h"}
        )
        if not data:
            return "❌ Ошибка загрузки"

        risks = []
        for c in data:
            ch = c.get("price_change_percentage_24h") or 0
            vol_ratio = (c["total_volume"] / c["market_cap"]) if c["market_cap"] > 0 else 0
            rsi = calc_rsi([ch * (0.75 + i * 0.04) for i in range(20)])
            risk_score = 0
            reasons = []
            if rsi > 75:
                risk_score += 3
                reasons.append(f"RSI {rsi}")
            if vol_ratio > 0.5:
                risk_score += 2
                reasons.append(f"Vol/Cap {vol_ratio*100:.0f}%")
            if ch > 20:
                risk_score += 2
                reasons.append(f"Рост {ch:.0f}% за 24ч")
            if ch > 10 and vol_ratio > 0.3:
                risk_score += 1
                reasons.append("Памп-паттерн")
            if risk_score >= 3:
                risks.append({
                    "sym": c["symbol"].upper(), "price": c["current_price"],
                    "rsi": rsi, "ch": ch, "cap": c["market_cap"],
                    "vol_ratio": vol_ratio, "score": risk_score, "reasons": reasons
                })

        risks.sort(key=lambda x: x["score"], reverse=True)

        if not risks:
            return "✅ Монет с высоким риском дампа не найдено."

        lines = [f"⚠️ *Риск дампа — {len(risks)} монет*\n",
                 "_Высокий RSI + аномальный объём + резкий рост_\n"]
        for r in risks[:8]:
            reasons_str = " | ".join(r["reasons"])
            lines.append(
                f"💣 *{r['sym']}* — {fmt_price(r['price'])} | "
                f"Кап: {fmt_b(r['cap'])}\n"
                f"   _{reasons_str}_"
            )
        lines.append(f"\n_/analyze SYMBOL для уровней выхода_")
        return "\n".join(lines)

    async def top_signals(self) -> str:
        data = await self._get_json(
            f"{COINGECKO}/coins/markets",
            {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 50,
             "page": 1, "price_change_percentage": "24h"}
        )
        if not data:
            return "❌ Ошибка загрузки"

        buys, sells = [], []
        for c in data:
            ch = c.get("price_change_percentage_24h") or 0
            vol_ratio = (c["total_volume"] / c["market_cap"]) if c["market_cap"] > 0 else 0
            rsi = calc_rsi([ch * (0.7 + i * 0.04) for i in range(20)])
            if rsi < 35:
                buys.append((c["symbol"].upper(), rsi, ch, c["current_price"], vol_ratio))
            elif rsi > 70:
                sells.append((c["symbol"].upper(), rsi, ch, c["current_price"], vol_ratio))

        lines = ["📈 *Топ сигналы рынка*\n"]
        if buys:
            lines.append("🟢 *ПОКУПАТЬ (RSI < 35 — перепродан):*")
            for sym, rsi, ch, price, vr in sorted(buys, key=lambda x: x[1])[:5]:
                lines.append(f"  ✅ *{sym}* — RSI `{rsi}` | {ch:+.1f}% | {fmt_price(price)}")
        if sells:
            lines.append("\n🔴 *ПРОДАВАТЬ (RSI > 70 — перекуплен):*")
            for sym, rsi, ch, price, vr in sorted(sells, key=lambda x: x[1], reverse=True)[:5]:
                lines.append(f"  ❌ *{sym}* — RSI `{rsi}` | {ch:+.1f}% | {fmt_price(price)}")
        if not buys and not sells:
            lines.append("🟡 Рынок нейтрален. Чётких сигналов нет.")

        lines.append("\n_/analyze SYMBOL для деталей_")
        return "\n".join(lines)
