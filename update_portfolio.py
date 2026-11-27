import base64
import json
import os
from datetime import datetime, timezone, timedelta
import requests
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
KST = timezone(timedelta(hours=9))


def to_float(val, default=0.0):
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return default


def get_usd_krw():
    url = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW"
    res = requests.get(url, headers=HEADERS, timeout=10).json()
    return to_float(res.get("exchangeInfo", {}).get("calcPrice"), 1400.0)


def get_gold_krw():
    url = "https://api.stock.naver.com/marketindex/metals/M04020000"
    res = requests.get(url, headers=HEADERS, timeout=10).json()
    return to_float(res.get("closePrice"), 0.0)


def get_kr_price(code):
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    res = requests.get(url, headers=HEADERS, timeout=10).json()
    return to_float(res.get("closePrice"), 0.0)


def get_us_price(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
    res = requests.get(url, headers=HEADERS, timeout=10).json()
    return to_float(res["chart"]["result"][0]["meta"]["regularMarketPrice"], 0.0)


def get_crypto_prices(markets):
    if not markets:
        return {}
    url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets)}"
    res = requests.get(url, headers=HEADERS, timeout=10).json()
    return {item["market"]: to_float(item.get("trade_price"), 0.0) for item in res}


def encrypt_payload(payload_dict, password):
    salt = os.urandom(16)
    iv = os.urandom(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
    key = kdf.derive(password.encode("utf-8"))
    aesgcm = AESGCM(key)
    plaintext = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, None)
    return {
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "data": base64.b64encode(ciphertext).decode(),
        "updated_at": payload_dict["updated_at"],
    }


def main():
    portfolio = json.loads(os.environ["PORTFOLIO_JSON"])
    password = os.environ["DASHBOARD_PASSWORD"]
    gh_token = os.environ["MY_GITHUB_TOKEN"]
    gh_repo_full = os.environ.get("GITHUB_REPOSITORY", "")

    usd_krw = get_usd_krw()
    gold_price = get_gold_krw()
    crypto_prices = get_crypto_prices([c["market"] for c in portfolio.get("crypto", [])])

    sections = {"kr": [], "us": [], "crypto": [], "gold": []}
    total_cost_krw = 0.0
    total_eval_krw = 0.0

    # 1. 국내주식·ETF
    for item in portfolio.get("kr", []):
        cur = get_kr_price(item["code"])
        cost = item["qty"] * item["avg"]
        val = item["qty"] * cur
        pnl = val - cost
        ret = (pnl / cost * 100) if cost else 0.0
        total_cost_krw += cost
        total_eval_krw += val
        sections["kr"].append({
            "name": item["name"], "symbol": item["code"], "qty": item["qty"],
            "avg": item["avg"], "cur": cur, "cost_krw": cost, "eval_krw": val,
            "pnl_krw": pnl, "ret": ret, "currency": "KRW"
        })

    # 2. 미국주식 (실시간 환율 적용)
    for item in portfolio.get("us", []):
        cur_usd = get_us_price(item["ticker"])
        cost_usd = item["qty"] * item["avg"]
        val_usd = item["qty"] * cur_usd
        pnl_usd = val_usd - cost_usd
        ret = (pnl_usd / cost_usd * 100) if cost_usd else 0.0
        cost_krw = cost_usd * usd_krw
        val_krw = val_usd * usd_krw
        total_cost_krw += cost_krw
        total_eval_krw += val_krw
        sections["us"].append({
            "name": item["name"], "symbol": item["ticker"], "qty": item["qty"],
            "avg": item["avg"], "cur": cur_usd, "cost_krw": cost_krw, "eval_krw": val_krw,
            "pnl_krw": val_krw - cost_krw, "pnl_usd": pnl_usd, "ret": ret, "currency": "USD"
        })

    # 3. 코인 (업비트)
    for item in portfolio.get("crypto", []):
        cur = crypto_prices.get(item["market"], 0.0)
        cost = item["qty"] * item["avg"]
        val = item["qty"] * cur
        pnl = val - cost
        ret = (pnl / cost * 100) if cost else 0.0
        total_cost_krw += cost
        total_eval_krw += val
        sections["crypto"].append({
            "name": item["name"], "symbol": item["market"].replace("KRW-", ""), "qty": item["qty"],
            "avg": item["avg"], "cur": cur, "cost_krw": cost, "eval_krw": val,
            "pnl_krw": pnl, "ret": ret, "currency": "KRW"
        })

    # 4. 금 (KRX 1g 기준)
    for item in portfolio.get("gold", []):
        cur = gold_price
        cost = item["qty"] * item["avg"]
        val = item["qty"] * cur
        pnl = val - cost
        ret = (pnl / cost * 100) if cost else 0.0
        total_cost_krw += cost
        total_eval_krw += val
        sections["gold"].append({
            "name": item["name"], "symbol": "KRX금(g)", "qty": item["qty"],
            "avg": item["avg"], "cur": cur, "cost_krw": cost, "eval_krw": val,
            "pnl_krw": pnl, "ret": ret, "currency": "KRW"
        })

    total_pnl_krw = total_eval_krw - total_cost_krw
    total_ret = (total_pnl_krw / total_cost_krw * 100) if total_cost_krw else 0.0

    payload = {
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "usd_krw": usd_krw,
        "summary": {
            "total_cost_krw": total_cost_krw,
            "total_eval_krw": total_eval_krw,
            "total_pnl_krw": total_pnl_krw,
            "total_ret": total_ret,
        },
        "sections": sections,
        "gh": {"repo": gh_repo_full, "token": gh_token},
    }

    encrypted = encrypt_payload(payload, password)
    with open("data.enc.json", "w", encoding="utf-8") as f:
        json.dump(encrypted, f, ensure_ascii=False)
    print("암호화 데이터 생성 완료:", payload["updated_at"])


if __name__ == "__main__":
    main()
