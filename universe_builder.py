"""
每日 09:00 建立宇宙清單
從 TWSE + OTC 抓前一日成交資料
篩選條件：成交金額 > 1 億、純股票（排除 ETF/權證）
取成交金額前 300 檔，存成 universe.json
"""
import requests
import json
import os
import re
from datetime import date

UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "universe.json")
MIN_AMOUNT = 1e8  # 成交金額 > 1 億
MAX_STOCKS = 300

BLACKLIST = {"6434", "2363", "1503", "1514", "3217", "3592"}


def _is_stock(code: str, name: str) -> bool:
    if not re.match(r'^\d{4}$', code):
        return False
    if re.match(r'^0\d{4}', code):
        return False
    if any(x in name for x in ["ETF", "債", "權", "特", "存託", "期"]):
        return False
    if code in BLACKLIST:
        return False
    return True


def _safe_float(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").replace("--", "0"))
    except Exception:
        return 0.0


def fetch_twse() -> list:
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
        result = []
        for d in r.json():
            code = d.get("Code", "").strip()
            name = d.get("Name", "").strip()
            if not _is_stock(code, name):
                continue
            amount = _safe_float(d.get("TradeValue", 0))
            if amount < MIN_AMOUNT:
                continue
            result.append({"code": code, "name": name, "amount": amount, "market": "TSE"})
        return result
    except Exception as e:
        print(f"  ⚠️ TWSE 失敗：{e}")
        return []


def fetch_otc() -> list:
    try:
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
        data = r.json()
        if not data:
            return []
        latest = max(d.get("Date", "") for d in data)
        result = []
        for d in data:
            if d.get("Date") != latest:
                continue
            code = d.get("SecuritiesCompanyCode", "").strip()
            name = d.get("CompanyName", "").strip()
            if not _is_stock(code, name):
                continue
            amount = _safe_float(d.get("TransactionAmount", 0))
            if amount < MIN_AMOUNT:
                continue
            result.append({"code": code, "name": name, "amount": amount, "market": "OTC"})
        return result
    except Exception as e:
        print(f"  ⚠️ OTC 失敗：{e}")
        return []


def build_universe() -> int:
    print("📋 更新宇宙清單...")
    tse = fetch_twse()
    otc = fetch_otc()
    combined = tse + otc

    # 去重（同代號保留一筆）
    seen = {}
    for s in combined:
        if s["code"] not in seen:
            seen[s["code"]] = s

    # 按成交金額排序，取前 300
    ranked = sorted(seen.values(), key=lambda x: x["amount"], reverse=True)[:MAX_STOCKS]

    universe = {
        "updated": date.today().isoformat(),
        "count": len(ranked),
        "stocks": [{"code": s["code"], "name": s["name"], "market": s["market"]} for s in ranked]
    }

    with open(UNIVERSE_PATH, "w", encoding="utf-8") as f:
        json.dump(universe, f, ensure_ascii=False, indent=2)

    print(f"  ✅ 宇宙清單更新完成：{len(ranked)} 檔（TSE {len(tse)} + OTC {len(otc)} 去重）")
    return len(ranked)


if __name__ == "__main__":
    build_universe()
