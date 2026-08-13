import os
import time
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

MOMENTUM_THRESHOLD = 65
NEW_PAIR_MAX_AGE_MIN = 30
NEW_PAIR_MIN_LIQUIDITY = 5000

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_DETAIL_URL = "https://api.coingecko.com/api/v3/coins/{id}"
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/search"
GOPLUS_EVM_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
GOPLUS_SOLANA_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"

PLATFORM_TO_GOPLUS = {
    "ethereum": "1", "binance-smart-chain": "56", "polygon-pos": "137",
    "arbitrum-one": "42161", "base": "8453", "solana": "solana",
}


def jupiter_link(output_mint):
    return f"https://jup.ag/swap/SOL-{output_mint}"


def send_telegram(text):
    r = requests.post(TELEGRAM_API, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    if not r.ok:
        print("telegram error", r.status_code, r.text)


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def check_contract_security(chain_id, contract_address):
    try:
        if chain_id == "solana":
            r = requests.get(GOPLUS_SOLANA_URL, params={"contract_addresses": contract_address}, timeout=15)
        else:
            url = GOPLUS_EVM_URL.format(chain_id=chain_id)
            r = requests.get(url, params={"contract_addresses": contract_address}, timeout=15)
        if r.status_code == 429:
            print(f"goplus rate limited on {contract_address}")
            return None, ["security check rate-limited, try again later"]
        r.raise_for_status()
        data = r.json().get("result", {}).get(contract_address.lower(), {})
    except Exception as e:
        print("goplus error", e)
        return None, ["security check unavailable"]

    if not data:
        return None, ["no security data found"]

    flags = []
    risk = 0

    if data.get("is_honeypot") == "1":
        flags.append("🔴 HONEYPOT — may block selling")
        risk += 50
    if data.get("is_mintable") == "1":
        flags.append("⚠️ Mintable — supply can be inflated")
        risk += 15
    if data.get("owner_change_balance") == "1":
        flags.append("⚠️ Owner can change balances")
        risk += 20
    if data.get("is_open_source") == "0":
        flags.append("⚠️ Contract not verified/open source")
        risk += 10
    if data.get("can_take_back_ownership") == "1":
        flags.append("⚠️ Ownership can be reclaimed")
        risk += 15

    lp_holders = data.get("lp_holders", [])
    locked_pct = sum(float(h.get("percent", 0)) for h in lp_holders if h.get("is_locked") == 1)
    if lp_holders and locked_pct < 0.5:
        flags.append(f"⚠️ Only {locked_pct*100:.0f}% liquidity locked")
        risk += 20

    holders = data.get("holders", [])
    if holders:
        top10_pct = sum(float(h.get("percent", 0)) for h in holders[:10])
        if top10_pct > 0.7:
            flags.append(f"🔴 Top 10 holders own {top10_pct*100:.0f}% of supply")
            risk += 25
        elif top10_pct > 0.4:
            flags.append(f"⚠️ Top 10 holders own {top10_pct*100:.0f}% of supply")
            risk += 10

    if not flags:
        flags.append("✅ No major red flags detected in automated check")

    return min(risk, 100), flags


def get_contract_info(coin_id):
    """Fetch contract address + chain for a CoinGecko coin id."""
    try:
        r = requests.get(COINGECKO_DETAIL_URL.format(id=coin_id), timeout=15)
        if r.status_code == 429:
            print(f"coingecko rate limited on {coin_id}, skipping")
            return None, None
        r.raise_for_status()
        platforms = r.json().get("platforms", {}) or {}
    except Exception as e:
        print(f"coingecko detail error for {coin_id}: {e}")
        return None, None

    for platform, address in platforms.items():
        if platform in PLATFORM_TO_GOPLUS and address:
            return PLATFORM_TO_GOPLUS[platform], address
    if platforms:
        print(f"{coin_id}: no matching chain among {list(platforms.keys())}")
    else:
        print(f"{coin_id}: no platform/contract data on CoinGecko")
    return None, None


def risk_label_for(score):
    if score is None:
        return "⚪ UNKNOWN"
    if score >= 50:
        return "🔴 HIGH"
    if score >= 20:
        return "🟡 MEDIUM"
    return "🟢 LOWER (still no guarantee)"


def fetch_coins():
    params = {
        "vs_currency": "usd", "order": "volume_desc", "per_page": 50,
        "page": 1, "category": "meme-token", "price_change_percentage": "1h,24h,7d",
    }
    r = requests.get(COINGECKO_MARKETS_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def momentum_score(c):
    chg_1h = c.get("price_change_percentage_1h_in_currency") or 0
    chg_24h = c.get("price_change_percentage_24h_in_currency") or 0
    chg_7d = c.get("price_change_percentage_7d_in_currency") or 0
    mc = c.get("market_cap") or 0
    vol = c.get("total_volume") or 0
    vol_ratio = (vol / mc) if mc else 0

    short_term = clamp((chg_1h + 10) / 20 * 100)
    daily = clamp((chg_24h + 30) / 60 * 100)
    weekly = clamp((chg_7d + 60) / 120 * 100)
    liquidity = clamp(vol_ratio * 100)

    score = short_term * 0.25 + daily * 0.35 + weekly * 0.15 + liquidity * 0.25
    return round(score), {"1h": chg_1h, "24h": chg_24h, "7d": chg_7d, "vol_ratio": vol_ratio, "rank": c.get("market_cap_rank")}


def check_momentum():
    for c in fetch_coins():
        score, d = momentum_score(c)
        if score < MOMENTUM_THRESHOLD:
            continue

        price = c.get("current_price") or 0
        price_str = f"${price:.8f}" if price < 0.01 else f"${price:,.4f}"

        chain_id, contract = get_contract_info(c["id"])
        risk_score, flags = (None, ["contract not found on supported chains"])
        if chain_id and contract:
            risk_score, flags = check_contract_security(chain_id, contract)
        time.sleep(1.5)

        risk_text = "\n".join(f"  {f}" for f in flags)

        msg = (
            f"🚨 *{c['symbol'].upper()}* — momentum {score}/100\n"
            f"Price: {price_str} (rank #{d['rank']})\n"
            f"1h: {d['1h']:+.2f}% · 24h: {d['24h']:+.2f}% · 7d: {d['7d']:+.2f}%\n"
            f"Volume/MCap turnover: {d['vol_ratio']:.2f}\n\n"
            f"*Contract risk: {risk_label_for(risk_score)}*\n{risk_text}\n\n"
            f"⚠️ Heuristic + public contract data only. Not a prediction, not advice. "
            f"A low risk score does not mean safe. DYOR."
        )

        if chain_id == "solana" and contract:
            msg += f"\n\n🔗 [Open swap in Jupiter]({jupiter_link(contract)})"

        send_telegram(msg)
        print("momentum alert", c["id"], score, risk_score)


def check_new_pairs():
    try:
        r = requests.get(DEXSCREENER_URL, params={"q": "meme"}, timeout=20)
        r.raise_for_status()
        pairs = r.json().get("pairs", []) or []
    except Exception as e:
        print("dexscreener error", e)
        return

    now_ms = datetime.now(timezone.utc).timestamp() * 1000

    for p in pairs:
        created_ms = p.get("pairCreatedAt")
        if not created_ms:
            continue
        age_min = (now_ms - created_ms) / 60000
        liquidity_usd = (p.get("liquidity") or {}).get("usd") or 0

        if age_min > NEW_PAIR_MAX_AGE_MIN or liquidity_usd < NEW_PAIR_MIN_LIQUIDITY:
            continue

        base = p.get("baseToken", {})
        chain = p.get("chainId", "")
        contract = base.get("address", "")
        price_change_5m = (p.get("priceChange") or {}).get("m5") or 0
        vol_5m = (p.get("volume") or {}).get("m5") or 0

        chain_id_map = {"ethereum": "1", "bsc": "56", "polygon": "137", "arbitrum": "42161", "base": "8453", "solana": "solana"}
        goplus_chain = chain_id_map.get(chain)

        risk_score, flags = (None, ["chain not supported for security check"])
        if goplus_chain and contract:
            risk_score, flags = check_contract_security(goplus_chain, contract)
            time.sleep(1.5)

        flags_text = "\n".join(f"  {f}" for f in flags)

        msg = (
            f"🆕 *{base.get('symbol','?').upper()}* — new pair, {age_min:.0f} min old\n"
            f"Liquidity: ${liquidity_usd:,.0f} · 5m volume: ${vol_5m:,.0f}\n"
            f"5m price change: {price_change_5m:+.2f}%\n"
            f"Chain: {chain} · DEX: {p.get('dexId','?')}\n\n"
            f"*Contract risk: {risk_label_for(risk_score)}*\n{flags_text}\n\n"
            f"⚠️ This is a public-data risk read, not a recommendation. "
            f"A low risk score does not mean safe. DYOR."
        )

        if chain == "solana" and contract:
            msg += f"\n\n🔗 [Open swap in Jupiter]({jupiter_link(contract)})"

        send_telegram(msg)
        print("new pair alert", base.get("symbol"), age_min, risk_score)


def main():
    check_momentum()
    check_new_pairs()


if __name__ == "__main__":
    main()
