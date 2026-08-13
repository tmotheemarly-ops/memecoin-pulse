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
            return None, ["security check rate-limited, try
