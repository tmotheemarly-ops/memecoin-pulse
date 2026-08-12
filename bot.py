import os
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MOMENTUM_THRESHOLD = 65

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"


def send_telegram(text):
    r = requests.post(TELEGRAM_API, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    if not r.ok:
        print("telegram error", r.status_code, r.text)


def fetch_coins():
    params = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": 50,
        "page": 1,
        "category": "meme-token",
        "price_change_percentage": "1h,24h,7d",
    }
    r = requests.get(COINGECKO_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def momentum_score(c):
    chg_1h = c.get("price_change_percentage_1h_in_currency") or 0
    chg_24h = c.get("price_change_percentage_24h_in_currency") or 0
    chg_7d = c.get("price_change_percentage_7d_in_currency") or 0

    mc = c.get("market_cap") or 0
    vol = c.get("total_volume") or 0
    vol_ratio = (vol / mc) if mc else 0

    # Components, each scaled 0-100
    short_term = clamp((chg_1h + 10) / 20 * 100)       # 1h move, sensitive to spikes
    daily = clamp((chg_24h + 30) / 60 * 100)            # 24h move
    weekly = clamp((chg_7d + 60) / 120 * 100)           # 7d trend, catches sustained moves
    liquidity = clamp(vol_ratio * 100)                  # how actively it's trading

    # Weighted blend: recent moves matter most, but weekly trend + liquidity
    # help filter out noise / single-candle spikes with no real volume behind them
    score = (
        short_term * 0.25 +
        daily * 0.35 +
        weekly * 0.15 +
        liquidity * 0.25
    )
    return round(score), {
        "1h": chg_1h, "24h": chg_24h, "7d": chg_7d,
        "vol_ratio": vol_ratio, "rank": c.get("market_cap_rank"),
    }


def main():
    coins = fetch_coins()
    for c in coins:
        score, d = momentum_score(c)
        if score < MOMENTUM_THRESHOLD:
            continue

        price = c.get("current_price") or 0
        price_str = f"${price:.8f}" if price < 0.01 else f"${price:,.4f}"

        msg = (
            f"🚨 *{c['symbol'].upper()}* — momentum {score}/100\n"
            f"Price: {price_str} (rank #{d['rank']})\n"
            f"1h: {d['1h']:+.2f}% · 24h: {d['24h']:+.2f}% · 7d: {d['7d']:+.2f}%\n"
            f"Volume/MCap turnover: {d['vol_ratio']:.2f}\n"
            f"⚠️ Public-data heuristic only. Not a prediction, not insider info, not advice. DYOR."
        )
        send_telegram(msg)
        print("alerted", c["id"], score)


if __name__ == "__main__":
    main()
