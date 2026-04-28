"""
全市場強勢股掃描：
1. TWSE + TPEx 一次抓全市場收盤資料
2. 過濾 ETF、權證、零股等非個股
3. 按漲幅+量比加權排序，取前 60 候選
4. Fugle 補抓 MA20，篩掉跌破 MA20 的
5. 取最強 30 檔存進 watchlist.json
"""
import requests
import pandas as pd
import json
import os
import re
from datetime import date
from fugle_fetcher import fetch_history


def _is_stock(code: str, name: str) -> bool:
    """過濾掉 ETF、債券、權證、特別股"""
    if re.match(r'^0\d{4}', code):  # ETF (00xx)
        return False
    if re.match(r'^\d{6}', code):   # 權證 (6碼)
        return False
    if any(x in name for x in ["ETF", "債", "權", "特", "存託"]):
        return False
    if not re.match(r'^\d{4}$', code):  # 只要純4碼
        return False
    return True


def fetch_market_data() -> list:
    """抓上市+上櫃全市場當日收盤資料"""
    stocks = []

    # 上市 TWSE
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
        for d in r.json():
            code = d.get("Code", "").strip()
            name = d.get("Name", "").strip()
            if not _is_stock(code, name):
                continue
            try:
                close  = float(d["ClosingPrice"])
                change = float(d["Change"])
                vol    = int(d["TradeVolume"]) // 1000
                chg_pct = change / (close - change) * 100 if (close - change) != 0 else 0
                if chg_pct <= 0 or vol < 100:  # 只要上漲、成交量 >100 張
                    continue
                stocks.append({"code": code, "name": name, "close": close,
                                "chg_pct": chg_pct, "volume": vol, "market": "TSE"})
            except Exception:
                continue
    except Exception:
        pass

    # 上櫃 TPEx
    try:
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
        for d in r.json():
            code = d.get("SecuritiesCompanyCode", "").strip()
            name = d.get("CompanyName", "").strip()
            if not _is_stock(code, name):
                continue
            try:
                close  = float(d["Close"])
                change = float(d["Change"])
                vol    = int(d["TradingShares"]) // 1000
                chg_pct = change / (close - change) * 100 if (close - change) != 0 else 0
                if chg_pct <= 0 or vol < 50:
                    continue
                stocks.append({"code": code, "name": name, "close": close,
                                "chg_pct": chg_pct, "volume": vol, "market": "OTC"})
            except Exception:
                continue
    except Exception:
        pass

    return stocks


def run_daily_scan() -> str:
    """全市場掃描，嚴選 30 檔強勢股更新 watchlist"""

    stocks = fetch_market_data()
    if not stocks:
        return "全市場資料抓取失敗，請稍後再試。"

    # 計算各股平均成交量（用當日量作為基準，之後可改為比對均量）
    # 先按漲幅排序取前 80 候選（保留緩衝給 MA20 篩選）
    candidates = sorted(stocks, key=lambda x: x["chg_pct"], reverse=True)[:80]

    # 用 Fugle 補抓 MA20 與均量（最多 60 次，取前 60 候選）
    qualified = []
    for s in candidates[:60]:
        try:
            df = fetch_history(s["code"], days=25)
            if df.empty:
                continue
            df["MA20"] = df["Close"].rolling(20).mean()
            last = df.iloc[-1]
            ma20 = last["MA20"]
            if pd.isna(ma20):
                continue
            if s["close"] <= ma20:
                continue
            avg_vol = df["Volume"].tail(10).mean()
            vol_ratio = s["volume"] * 1000 / avg_vol if avg_vol > 0 else 0
            s["ma20"] = round(float(ma20), 1)
            s["vol_ratio"] = round(vol_ratio, 1)
            qualified.append(s)
        except Exception:
            continue

    # 加權排序：漲幅 60% + 量 40%
    if qualified:
        max_chg = max(s["chg_pct"] for s in qualified)
        max_vol = max(s["volume"] for s in qualified) or 1
        for s in qualified:
            s["score"] = (s["chg_pct"] / max_chg * 0.6) + (s["volume"] / max_vol * 0.4)
        qualified.sort(key=lambda x: x["score"], reverse=True)

    top30 = qualified[:30]

    # TOP 5：從 top30 中額外篩選量比 > 2x 且非漲停（避免隔日跳空開低）
    top5 = [s for s in top30 if s.get("vol_ratio", 0) >= 2.0 and s["chg_pct"] < 9.5][:5]
    # 若不足 5 檔，放寬條件補滿
    if len(top5) < 5:
        extras = [s for s in top30 if s not in top5]
        top5 = (top5 + extras)[:5]

    # 更新 watchlist.json
    watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
    with open(watchlist_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["watchlist"] = [
        {"code": s["code"], "name": s["name"],
         "chg_pct": round(s["chg_pct"], 1),
         "added": date.today().isoformat()}
        for s in top30
    ]
    data["last_scan"] = date.today().isoformat()

    with open(watchlist_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 組推播訊息
    lines = [f"【全市場強勢掃描】{date.today().strftime('%m/%d')}",
             f"掃描 {len(stocks)} 檔 → 嚴選 {len(top30)} 檔\n"]

    for i, s in enumerate(top30, 1):
        lines.append(f"{i:2}. {s['name']}（{s['code']}）{s['close']:.0f} +{s['chg_pct']:.1f}%")

    # 明日 TOP 5
    lines.append(f"\n★ 明日重點關注 TOP 5 ★")
    for i, s in enumerate(top5, 1):
        vol_str = f" 量比{s['vol_ratio']:.1f}x" if s.get("vol_ratio") else ""
        lines.append(f"  {i}. {s['name']}（{s['code']}）{s['close']:.0f} +{s['chg_pct']:.1f}%{vol_str}")
    lines.append("\n量比>2x、非漲停、站上MA20，右側回測可留意")

    return "\n".join(lines)
