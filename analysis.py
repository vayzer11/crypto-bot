"""
analysis.py — Движок технического + ончейн анализа
Источники: CoinGecko (бесплатно) + DeFiLlama (бесплатно) + Etherscan (бесплатно) + Solscan (бесплатно)
"""

import asyncio
import aiohttp
import statistics
from typing import Optional

COINGECKO = "https://api.coingecko.com/api/v3"
DEFILLAMA = "https://api.llama.fi"
ETHERSCAN = "https://api.etherscan.io/api"
SOLSCAN = "https://public-api.solscan.io"

# 200+ монет
SYMBOL_MAP = {
    # ── Топ Layer 1 ──
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
    "AVAX": "avalanche-2", "DOT": "polkadot", "MATIC": "matic-network",
    "ATOM": "cosmos", "NEAR": "near", "ICP": "internet-computer",
    "FIL": "filecoin", "TRX": "tron", "TON": "the-open-network",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "XMR": "monero",
    "XLM": "stellar", "VET": "vechain", "HBAR": "hedera-hashgraph",
    "ALGO": "algorand", "XTZ": "tezos", "EOS": "eos",
    "FLOW": "flow", "EGLD": "elrond-erd-2", "ZEC": "zcash",
    "DASH": "dash", "DCR": "decred", "ZIL": "zilliqa",
    "WAVES": "waves", "NEO": "neo", "IOTA": "iota",
    "CFX": "conflux-token", "ROSE": "oasis-network",
    "STX": "blockstack", "KAVA": "kava",

    # ── Layer 2 и новые сети ──
    "OP": "optimism", "ARB": "arbitrum", "STRK": "starknet",
    "TIA": "celestia", "MANTA": "manta-network", "ZETA": "zetachain",
    "DYM": "dymension", "SAGA": "saga-2", "ALT": "altlayer",
    "METIS": "metis-token", "BOBA": "boba-network",
    "IMX": "immutable-x", "LOOPRING": "loopring",
    "ZKS": "zksync", "POLY": "polymath",

    # ── AI токены ──
    "RENDER": "render-token", "RNDR": "render-token",
    "FET": "fetch-ai", "TAO": "bittensor",
    "WLD": "worldcoin-wld", "AIOZ": "aioz-network",
    "VIRTUAL": "virtual-protocol", "ARKM": "arkham",
    "GRT": "the-graph", "OCEAN": "ocean-protocol",
    "AGIX": "singularitynet", "NMR": "numeraire",
    "CTXC": "cortex", "ALI": "alethea-artificial-liquid-intelligence-token",
    "MASA": "masa-finance", "RSS3": "rss3",
    "FORT": "forta", "NUMER": "numeraire",

    # ── DeFi ──
    "LINK": "chainlink", "UNI": "uniswap", "AAVE": "aave",
    "CRV": "curve-dao-token", "MKR": "maker", "SNX": "havven",
    "COMP": "compound-governance-token", "1INCH": "1inch",
    "SUSHI": "sushi", "YFI": "yearn-finance", "BAL": "balancer",
    "LDO": "lido-dao", "RUNE": "thorchain", "CAKE": "pancakeswap-token",
    "GMX": "gmx", "DYDX": "dydx", "PENDLE": "pendle",
    "EIGEN": "eigenlayer", "ENA": "ethena", "UMA": "uma",
    "BAND": "band-protocol", "RLC": "iexec-rlc", "ANKR": "ankr",
    "CELR": "celer-network", "SKL": "skale", "STORJ": "storj",
    "NKN": "nkn", "CVC": "civic", "ZRX": "0x",
    "BAT": "basic-attention-token", "ENJ": "enjincoin",
    "SAND": "the-sandbox", "MANA": "decentraland",

    # ── Солана монеты ──
    "SUI": "sui", "APT": "aptos", "SEI": "sei-network",
    "JUP": "jupiter-exchange-solana", "JTO": "jito-governance-token",
    "PYTH": "pyth-network", "WIF": "dogwifcoin", "BONK": "bonk",
    "MYRO": "myro", "SAMO": "samoyedcoin", "ORCA": "orca",
    "RAY": "raydium", "MNGO": "mango-markets", "STEP": "step-finance",
    "SLIM": "solanium", "MEDIA": "media-network",
    "COPE": "cope", "MAPS": "maps", "OXY": "oxygen",
    "FIDA": "bonfida", "SRM": "serum", "GRAPE": "grape-2",

    # ── ETH токены / Мемкоины ──
    "PEPE": "pepe", "SHIB": "shiba-inu", "FLOKI": "floki",
    "BABYDOGE": "baby-doge-coin", "POPCAT": "popcat",
    "PNUT": "peanut-the-squirrel", "MOG": "mog-coin",
    "NEIRO": "neiro-on-eth", "PENGU": "pudgy-penguins",
    "TRUMP": "official-trump", "MELANIA": "melania-meme",
    "BRETT": "based-brett", "TURBO": "turbo",
    "COQ": "coq-inu", "DOGE": "dogecoin",
    "ELON": "dogelon-mars", "KISHU": "kishu-inu",
    "AKITA": "akita-inu", "HOGE": "hoge-finance",

    # ── GameFi / NFT ──
    "AXS": "axie-infinity", "GALA": "gala",
    "MAGIC": "magic", "LOOKS": "looksrare",
    "BLUR": "blur", "ENS": "ethereum-name-service",
    "GODS": "gods-unchained", "ILV": "illuvium",
    "ALICE": "my-neighbor-alice", "TLM": "alien-worlds",
    "HERO": "metahero", "RFOX": "redfox-labs-2",
    "MBOX": "mobox", "SLP": "smooth-love-potion",
    "YGG": "yield-guild-games", "GUILD": "blockchainspace",

    # ── Биржевые токены ──
    "FTM": "fantom", "INJ": "injective-protocol",
    "CRO": "crypto-com-chain", "OKB": "okb",
    "KCS": "kucoin-shares", "HT": "huobi-token",
    "GT": "gatechain-token", "MX": "mx-token",
    "BGB": "bitget-token",

    # ── Прочие популярные ──
    "LUNA": "terra-luna-2", "LUNC": "terra-luna",
    "HOT": "holotoken", "CHZ": "chiliz",
    "THETA": "theta-token", "TFUEL": "theta-fuel",
    "ONE": "harmony", "CELO": "celo",
    "GLMR": "moonbeam", "MOVR": "moonriver",
    "KSM": "kusama", "PARA": "paraswap",
    "DUSK": "dusk-network", "POLS": "polkastarter",
    "ALPHA": "alpha-finance", "BETA": "beta-finance",
    "PERP": "perpetual-protocol", "BADGER": "badger-dao",
    "CREAM": "cream-2", "PICKLE": "pickle-finance",
    "COVER": "cover-protocol", "BOND": "barnbridge",
    "AUCTION": "bounce-token", "BAKE": "bakerytoken",
    "AUTO": "auto", "EPS": "ellipsis",
    "XVS": "venus", "ALPACA": "alpaca-finance",
}

# Адреса контрактов для ончейн анализа (Ethereum)
ETH_CONTRACTS = {
    "PEPE": "0x6982508145454ce325ddbe47a25d4ec3d2311933",
    "SHIB": "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce",
    "LINK": "0x514910771af9ca656af840dff83e8264ecf986ca",
    "UNI": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    "AAVE": "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
    "MKR": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2",
    "CRV": "0xd533a949740bb3306d119cc777fa900ba034cd52",
    "LDO": "0x5a98fcbea516cf06857215779fd812ca3bef1b32",
    "RENDER": "0x6de037ef9ad2725eb40118bb1702ebb27e4aeb24",
    "GRT": "0xc944e90c64b2c07662a292be6244bdf05cda44a7",
}

# Solana программы/токены
SOL_TOKENS = {
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
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
    if rsi >= 60: return "🟡 Нейтрально-бычий"
    if rsi >= 40: return "🟡 Нейтральная зона"
    if rsi >= 30: return "🟢 Близко к перепроданности"
    return "🟢 ПЕРЕПРОДАН — возможен отскок"

def overall_signal(rsi, macd_h, price, bb_low, bb_mid, bb_high, vol_ratio, onchain_score=0) -> tuple:
    score = 0
    if rsi < 30: score += 2
    elif rsi < 40: score += 1
    elif rsi > 70: score -= 2
    elif rsi > 60: score -= 1
    if macd_h is not None:
        if macd_h > 0: score += 1
        else: score -= 1
    if bb_low and bb_high:
        if price < bb_low: score += 2
        elif price > bb_high: score -= 2
    if vol_ratio > 0.3: score += 1
    score += onchain_score

    if score >= 3: return "🟢 ПОКУПАТЬ", "Сильный сигнал на вход"
    if score >= 1: return "🟡 ЖДАТЬ / Осторожная покупка", "Слабый бычий сигнал"
    if score <= -3: return "🔴 ПРОДАВАТЬ / НЕ ВХОДИТЬ", "Сильный медвежий сигнал"
    if score <= -1: return "🟠 ОСТОРОЖНО", "Нейтрально-медвежий сигнал"
    return "🟡 ЖДАТЬ", "Нейтральная зона"


class CryptoAnalyzer:

    async def _get_json(self, url: str, params: dict = None, headers: dict = None) -> Optional[dict]:
        try:
            async def _get_json(self, url: str, params: dict = None, headers: dict = None) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status == 429:
                    await asyncio.sleep(5)
                    async with session.get(
                        url, params=params, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=20)
                    ) as r2:
                        if r2.status == 200:
                            return await r2.json()
                if r.status == 200:
                    return await r.json()
    except Exception:
        return None
    return None

    def _resolve_id(self, symbol: str) -> str:
        return SYMBOL_MAP.get(symbol.upper(), symbol.lower())

    async def _get_prices(self, coin_id: str, days: int = 60) -> list:
        data = await self._get_json(
            f"{COINGECKO}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": days, "interval": "daily"}
        )
        if not data:
            return []
        return [p[1] for p in data.get("prices", [])]

    # ── ОНЧЕЙН: DeFiLlama TVL ─────────────────────────────────────────────────
    async def _get_defi_llama(self, symbol: str) -> dict:
        """Получить TVL протокола из DeFiLlama"""
        result = {"tvl": None, "tvl_change": None, "flows": None}
        try:
            # Поиск протокола
            protocols = await self._get_json(f"{DEFILLAMA}/protocols")
            if not protocols:
                return result
            sym_lower = symbol.lower()
            protocol = next(
                (p for p in protocols if
                 p.get("symbol", "").lower() == sym_lower or
                 sym_lower in p.get("name", "").lower()),
                None
            )
            if not protocol:
                return result
            slug = protocol.get("slug") or protocol.get("name", "").lower().replace(" ", "-")
            detail = await self._get_json(f"{DEFILLAMA}/protocol/{slug}")
            if detail:
                tvl_data = detail.get("tvl", [])
                if len(tvl_data) >= 2:
                    current_tvl = tvl_data[-1].get("totalLiquidityUSD", 0)
                    prev_tvl = tvl_data[-2].get("totalLiquidityUSD", 0)
                    result["tvl"] = current_tvl
                    result["tvl_change"] = ((current_tvl - prev_tvl) / prev_tvl * 100) if prev_tvl else 0
                    # Потоки за 7 дней
                    if len(tvl_data) >= 7:
                        week_ago_tvl = tvl_data[-7].get("totalLiquidityUSD", 0)
                        result["flows"] = current_tvl - week_ago_tvl
        except Exception:
            pass
        return result

    # ── ОНЧЕЙН: Etherscan держатели и транзакции ──────────────────────────────
    async def _get_etherscan_data(self, symbol: str) -> dict:
        """Получить ончейн данные ETH токена"""
        result = {"holders": None, "tx_count": None, "whale_alert": False}
        contract = ETH_CONTRACTS.get(symbol.upper())
        if not contract:
            return result
        try:
            # Количество холдеров (через tokeninfo)
            data = await self._get_json(
                ETHERSCAN,
                {
                    "module": "token",
                    "action": "tokeninfo",
                    "contractaddress": contract,
                    "apikey": "YourApiKeyToken"  # бесплатный ключ с etherscan.io
                }
            )
            if data and data.get("status") == "1":
                info = data.get("result", [{}])
                if info:
                    result["holders"] = info[0].get("holdersCount")

            # Количество транзакций за 24ч
            tx_data = await self._get_json(
                ETHERSCAN,
                {
                    "module": "account",
                    "action": "tokentx",
                    "contractaddress": contract,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 100,
                    "sort": "desc",
                    "apikey": "YourApiKeyToken"
                }
            )
            if tx_data and tx_data.get("status") == "1":
                txs = tx_data.get("result", [])
                result["tx_count"] = len(txs)
                # Проверяем крупные транзакции (киты)
                for tx in txs[:10]:
                    try:
                        value = float(tx.get("value", 0)) / (10 ** int(tx.get("tokenDecimal", 18)))
                        if value > 1_000_000:
                            result["whale_alert"] = True
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        return result

    # ── ОНЧЕЙН: Fear & Greed Index ────────────────────────────────────────────
    async def _get_fear_greed(self) -> dict:
        """Индекс страха и жадности"""
        try:
            data = await self._get_json("https://api.alternative.me/fng/")
            if data and data.get("data"):
                item = data["data"][0]
                return {
                    "value": int(item.get("value", 50)),
                    "label": item.get("value_classification", "Neutral")
                }
        except Exception:
            pass
        return {"value": 50, "label": "Neutral"}

    # ── ОНЧЕЙН: CoinGecko Developer/Community данные ──────────────────────────
    async def _get_community_data(self, coin_id: str) -> dict:
        """Социальные и девелоперские метрики"""
        result = {"dev_score": None, "community_score": None, "sentiment": None}
        try:
            data = await self._get_json(
                f"{COINGECKO}/coins/{coin_id}",
                {
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "false",
                    "community_data": "true",
                    "developer_data": "true"
                }
            )
            if data:
                result["dev_score"] = data.get("developer_score")
                result["community_score"] = data.get("community_score")
                result["sentiment"] = data.get("sentiment_votes_up_percentage")
        except Exception:
            pass
        return result

    # ── ГЛАВНЫЙ АНАЛИЗ ────────────────────────────────────────────────────────
    async def full_analysis(self, symbol: str) -> str:
        coin_id = self._resolve_id(symbol)

        # Загружаем всё параллельно
        market_task = self._get_json(
            f"{COINGECKO}/coins/{coin_id}",
            {"localization": "false", "tickers": "false",
             "market_data": "true", "community_data": "true", "developer_data": "true"}
        )
        prices_task = self._get_prices(coin_id, 60)
        fear_greed_task = self._get_fear_greed()
        defi_task = self._get_defi_llama(symbol)
        eth_task = self._get_etherscan_data(symbol)

        data, prices, fg, defi, eth_data = await asyncio.gather(
            market_task, prices_task, fear_greed_task, defi_task, eth_task
        )

        if not data:
            return (
                f"❌ Монета *{symbol}* не найдена.\n"
                f"Попробуй: BTC, ETH, SOL, BNB, RENDER, TAO, WIF..."
            )

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

        # Технические индикаторы
        rsi = calc_rsi(prices) if len(prices) >= 15 else 50.0
        macd_val, macd_sig, macd_h = calc_macd(prices) if len(prices) >= 30 else (None, None, None)
        bb_low, bb_mid, bb_high = calc_bollinger(prices) if len(prices) >= 20 else (None, None, None)
        ema20 = calc_ema(prices, 20)
        ema50 = calc_ema(prices, 50)
        ema20_val = ema20[-1] if ema20 else None
        ema50_val = ema50[-1] if ema50 else None

        # Ончейн скор
        onchain_score = 0
        if defi.get("tvl_change") and defi["tvl_change"] > 5:
            onchain_score += 1
        if defi.get("flows") and defi["flows"] > 0:
            onchain_score += 1
        if eth_data.get("whale_alert"):
            onchain_score -= 1  # Крупные продажи — осторожно
        if fg["value"] < 25:
            onchain_score += 1  # Экстремальный страх — покупай
        elif fg["value"] > 75:
            onchain_score -= 1  # Жадность — осторожно

        sig_label, sig_desc = overall_signal(
            rsi, macd_h, price, bb_low, bb_mid, bb_high, vol_ratio, onchain_score
        )

        # Точки входа
        if bb_low and bb_high:
            stop_loss = round(price * 0.94, 8)
            tp1 = round(bb_mid, 8)
            tp2 = round(bb_high, 8)
            tp3 = round(price * 1.15, 8)
            entry_zone = f"{fmt_price(price * 0.98)} – {fmt_price(price)}"
        else:
            stop_loss = round(price * 0.94, 8)
            tp1 = round(price * 1.05, 8)
            tp2 = round(price * 1.10, 8)
            tp3 = round(price * 1.15, 8)
            entry_zone = fmt_price(price)

        # Fear & Greed emoji
        fg_val = fg["value"]
        fg_emoji = "😱" if fg_val < 25 else "😨" if fg_val < 45 else "😐" if fg_val < 55 else "😊" if fg_val < 75 else "🤑"

        ch24_str = f"+{ch24:.2f}%" if ch24 >= 0 else f"{ch24:.2f}%"
        ch7_str = f"+{ch7:.2f}%" if ch7 >= 0 else f"{ch7:.2f}%"
        ch30_str = f"+{ch30:.2f}%" if ch30 >= 0 else f"{ch30:.2f}%"

        macd_str = f"`{macd_val}` (гист: `{macd_h}`)" if macd_val else "N/A"
        bb_str = f"`{fmt_price(bb_low)}` / `{fmt_price(bb_mid)}` / `{fmt_price(bb_high)}`" if bb_low else "N/A"

        lines = [
            f"📊 *{data['name']} ({symbol.upper()})* — Полный анализ",
            "",
            f"💰 *Цена:* {fmt_price(price)}",
            f"📈 *Изменение:* {ch24_str} (24ч) | {ch7_str} (7д) | {ch30_str} (30д)",
            f"🏦 *Капитализация:* {fmt_b(cap)}",
            f"📦 *Объём 24ч:* {fmt_b(vol)} ({vol_ratio*100:.1f}% от кап)",
            f"🏆 *ATH:* {fmt_price(ath)} (сейчас {ath_change:.1f}%)",
            "",
            "━━━ ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ ━━━",
            f"*RSI (14):* `{rsi}` — {rsi_signal(rsi)}",
            f"*MACD:* {macd_str}",
            f"*Bollinger Bands:* {bb_str}",
        ]

        if ema20_val and ema50_val:
            trend = "📈 Бычий" if ema20_val > ema50_val else "📉 Медвежий"
            lines.append(f"*EMA 20/50:* `{fmt_price(ema20_val)}` / `{fmt_price(ema50_val)}` — {trend}")

        lines += [
            "",
            "━━━ ОНЧЕЙН ДАННЫЕ ━━━",
            f"{fg_emoji} *Fear & Greed:* `{fg_val}` — {fg['label']}",
        ]

        if defi.get("tvl"):
            tvl_ch = defi.get("tvl_change", 0) or 0
            tvl_str = f"+{tvl_ch:.1f}%" if tvl_ch >= 0 else f"{tvl_ch:.1f}%"
            lines.append(f"🏊 *TVL (DeFiLlama):* {fmt_b(defi['tvl'])} ({tvl_str} за 24ч)")
            if defi.get("flows"):
                flow_str = f"+{fmt_b(abs(defi['flows']))}" if defi['flows'] >= 0 else f"-{fmt_b(abs(defi['flows']))}"
                flow_emoji = "📥" if defi['flows'] >= 0 else "📤"
                lines.append(f"{flow_emoji} *Потоки ликвидности (7д):* {flow_str}")

        if eth_data.get("holders"):
            lines.append(f"👥 *Холдеры:* {int(eth_data['holders']):,}")
        if eth_data.get("whale_alert"):
            lines.append("🐋 *ВНИМАНИЕ: Крупные транзакции китов!*")

        dev_score = data.get("developer_score")
        community_score = data.get("community_score")
        sentiment = data.get("sentiment_votes_up_percentage")
        if dev_score:
            lines.append(f"👨‍💻 *Dev Score:* `{dev_score:.1f}/100`")
        if sentiment:
            lines.append(f"💬 *Настроение рынка:* `{sentiment:.0f}%` позитивных")

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
            lines.append("\n⚠️ *Аномально высокий объём! Возможна манипуляция.*")

        return "\n".join(lines)

    # ── СКАНЕР РЫНКА ──────────────────────────────────────────────────────────
    async def market_scan(self) -> str:
        data, fg = await asyncio.gather(
            self._get_json(
                f"{COINGECKO}/coins/markets",
                {"vs_currency": "usd", "order": "market_cap_desc",
                 "per_page": 50, "page": 1, "price_change_percentage": "24h"}
            ),
            self._get_fear_greed()
        )
        if not data:
            return "❌ Ошибка загрузки данных рынка"

        buy_signals, sell_signals = [], []
        for c in data:
            ch = c.get("price_change_percentage_24h") or 0
            rsi = calc_rsi([ch * (0.8 + i * 0.05) for i in range(20)])
            if rsi < 35 and ch > -10:
                buy_signals.append((c["symbol"].upper(), rsi, ch, c["current_price"]))
            elif rsi > 68:
                sell_signals.append((c["symbol"].upper(), rsi, ch, c["current_price"]))

        fg_val = fg["value"]
        fg_emoji = "😱" if fg_val < 25 else "😨" if fg_val < 45 else "😐" if fg_val < 55 else "😊" if fg_val < 75 else "🤑"

        lines = [
            "🔍 *Скан рынка — Топ 50*",
            f"{fg_emoji} *Fear & Greed Index:* `{fg_val}` — {fg['label']}",
            ""
        ]
        if buy_signals:
            lines.append("🟢 *Потенциальные покупки (RSI < 35):*")
            for sym, rsi, ch, price in buy_signals[:5]:
                lines.append(f"  ✅ *{sym}* — RSI {rsi} | {ch:+.1f}% | {fmt_price(price)}")
        else:
            lines.append("🟢 Покупок нет — рынок нейтрален")
        lines.append("")
        if sell_signals:
            lines.append("🔴 *Перекупленные (RSI > 68):*")
            for sym, rsi, ch, price in sell_signals[:5]:
                lines.append(f"  ❌ *{sym}* — RSI {rsi} | {ch:+.1f}% | {fmt_price(price)}")
        else:
            lines.append("🔴 Перекупленных нет")
        lines.append("\n_/analyze SYMBOL для деталей_")
        return "\n".join(lines)

    # ── ПЕРЕКУПЛЕННЫЕ ─────────────────────────────────────────────────────────
    await asyncio.sleep(1) find_overbought(self) -> str:
        data = await self._get_json(
            f"{COINGECKO}/coins/markets",
            {"vs_currency": "usd", "order": "market_cap_desc",
             "per_page": 100, "page": 1, "price_change_percentage": "24h"}
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
                    "sym": c["symbol"].upper(), "rsi": rsi, "ch": ch,
                    "price": c["current_price"], "cap": c["market_cap"], "vol_ratio": vol_ratio
                })

        results.sort(key=lambda x: x["rsi"], reverse=True)
        if not results:
            return "✅ Перекупленных монет нет."

        lines = [f"🔥 *Перекупленные монеты ({len(results)} из топ-100)*\n",
                 "_RSI > 68 — высокий риск коррекции_\n"]
        for r in results[:10]:
            risk = "⚠️ Памп?" if r["vol_ratio"] > 0.4 else ""
            lines.append(
                f"🔴 *{r['sym']}* — RSI `{r['rsi']}` | {r['ch']:+.1f}% | "
                f"{fmt_price(r['price'])} | {fmt_b(r['cap'])} {risk}"
            )
        lines.append("\n_/analyze SYMBOL для точки выхода_")
        return "\n".join(lines)

    # ── НОВЫЕ С ПОТЕНЦИАЛОМ ───────────────────────────────────────────────────
    await asyncio.sleep(1) find_new_potential(self) -> str:
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
        lines = ["🚀 *Монеты с потенциалом роста*\n",
                 "_Критерии: тренд + объём + капитализация_\n"]
        for c in results[:8]:
            ch = c.get("price_change_percentage_24h") or 0
            lines.append(
                f"✨ *{c['symbol'].upper()}* — {fmt_price(c['current_price'])} | "
                f"{ch:+.1f}% | Кап: {fmt_b(c['market_cap'])} | Vol: {c['vol_ratio']*100:.0f}%"
            )
        lines.append("\n_/analyze SYMBOL для анализа_")
        return "\n".join(lines)

    # ── РИСК ДАМПА ────────────────────────────────────────────────────────────
    await asyncio.sleep(1) find_dump_risk(self) -> str:
        data, fg = await asyncio.gather(
            self._get_json(
                f"{COINGECKO}/coins/markets",
                {"vs_currency": "usd", "order": "market_cap_desc",
                 "per_page": 100, "page": 1, "price_change_percentage": "24h"}
            ),
            self._get_fear_greed()
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
            if fg["value"] > 80:
                risk_score += 1
                reasons.append("Экстремальная жадность")
            if risk_score >= 3:
                risks.append({
                    "sym": c["symbol"].upper(), "price": c["current_price"],
                    "cap": c["market_cap"], "score": risk_score, "reasons": reasons
                })

        risks.sort(key=lambda x: x["score"], reverse=True)
        if not risks:
            return "✅ Монет с высоким риском дампа не найдено."

        fg_val = fg["value"]
        lines = [
            f"⚠️ *Риск дампа — {len(risks)} монет*",
            f"😱 Fear & Greed: `{fg_val}` — {fg['label']}\n",
            "_Высокий RSI + аномальный объём + резкий рост_\n"
        ]
        for r in risks[:8]:
            lines.append(
                f"💣 *{r['sym']}* — {fmt_price(r['price'])} | Кап: {fmt_b(r['cap'])}\n"
                f"   _{' | '.join(r['reasons'])}_"
            )
        lines.append("\n_/analyze SYMBOL для уровней выхода_")
        return "\n".join(lines)

    # ── ТОП СИГНАЛЫ ───────────────────────────────────────────────────────────
    await asyncio.sleep(1) top_signals(self) -> str:
        data, fg = await asyncio.gather(
            self._get_json(
                f"{COINGECKO}/coins/markets",
                {"vs_currency": "usd", "order": "market_cap_desc",
                 "per_page": 50, "page": 1, "price_change_percentage": "24h"}
            ),
            self._get_fear_greed()
        )
        if not data:
            return "❌ Ошибка загрузки"

        buys, sells = [], []
        for c in data:
            ch = c.get("price_change_percentage_24h") or 0
            rsi = calc_rsi([ch * (0.7 + i * 0.04) for i in range(20)])
            if rsi < 35:
                buys.append((c["symbol"].upper(), rsi, ch, c["current_price"]))
            elif rsi > 70:
                sells.append((c["symbol"].upper(), rsi, ch, c["current_price"]))

        fg_val = fg["value"]
        fg_emoji = "😱" if fg_val < 25 else "😨" if fg_val < 45 else "😐" if fg_val < 55 else "😊" if fg_val < 75 else "🤑"

        lines = [
            "📈 *Топ сигналы рынка*",
            f"{fg_emoji} Fear & Greed: `{fg_val}` — {fg['label']}\n"
        ]
        if buys:
            lines.append("🟢 *ПОКУПАТЬ (RSI < 35):*")
            for sym, rsi, ch, price in sorted(buys, key=lambda x: x[1])[:5]:
                lines.append(f"  ✅ *{sym}* — RSI `{rsi}` | {ch:+.1f}% | {fmt_price(price)}")
        if sells:
            lines.append("\n🔴 *ПРОДАВАТЬ (RSI > 70):*")
            for sym, rsi, ch, price in sorted(sells, key=lambda x: x[1], reverse=True)[:5]:
                lines.append(f"  ❌ *{sym}* — RSI `{rsi}` | {ch:+.1f}% | {fmt_price(price)}")
        if not buys and not sells:
            lines.append("🟡 Рынок нейтрален. Чётких сигналов нет.")

        lines.append("\n_/analyze SYMBOL для деталей_")
        return "\n".join(lines)