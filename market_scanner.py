"""
全市場強勢股掃描（右側交易邏輯）：
第一層 15 檔：站上MA20、漲幅>2%、量比>1.2x、非漲停
第二層 TOP5：MA5>MA20、漲幅3-9%、量比>1.5x、近3日至少2日收紅
"""
import requests
import pandas as pd
import json
import os
import re
from datetime import date
from fugle_fetcher import fetch_history


def _is_stock(code: str, name: str) -> bool:
    if re.match(r'^0\d{4}', code):
        return False
    if re.match(r'^\d{6}', code):
        return False
    if any(x in name for x in ["ETF", "債", "權", "特", "存託"]):
        return False
    if not re.match(r'^\d{4}$', code):
        return False
    return True


def fetch_market_data() -> list:
    stocks = []

    # 上市
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
                if chg_pct <= 0 or vol < 100:
                    continue
                stocks.append({"code": code, "name": name, "close": close,
                                "chg_pct": chg_pct, "volume": vol, "market": "TSE"})
            except Exception:
                continue
    except Exception:
        pass

    # 上櫃
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


def _enrich(s: dict, df: pd.DataFrame) -> dict:
    """補充 MA5、MA20、量比、近3日收紅數"""
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    last = df.iloc[-1]

    avg_vol = df["Volume"].tail(10).mean()
    vol_ratio = s["volume"] * 1000 / avg_vol if avg_vol > 0 else 0

    # 近 3 日收紅（收 > 開）
    recent3 = df.tail(3)
    green_days = int((recent3["Close"] >= recent3["Open"]).sum())

    s["ma5"]       = round(float(last["MA5"]),  1) if not pd.isna(last["MA5"])  else None
    s["ma20"]      = round(float(last["MA20"]), 1) if not pd.isna(last["MA20"]) else None
    s["vol_ratio"] = round(vol_ratio, 1)
    s["green3"]    = green_days
    return s


def run_daily_scan() -> str:
    stocks = fetch_market_data()
    if not stocks:
        return "全市場資料抓取失敗，請稍後再試。"

    # 候選：按漲幅排序取前 60，用 Fugle 補技術指標
    candidates = sorted(stocks, key=lambda x: x["chg_pct"], reverse=True)[:60]

    enriched = []
    for s in candidates:
        try:
            df = fetch_history(s["code"], days=25)
            if df.empty or len(df) < 5:
                continue
            s = _enrich(s, df)
            if s["ma20"] is None:
                continue
            enriched.append(s)
        except Exception:
            continue

    # ── 第一層：15 檔（較鬆）──
    # 站上MA20、漲幅>2%、量比>1.2x（接受漲停）
    layer1 = [
        s for s in enriched
        if s["close"] > s["ma20"]
        and s["chg_pct"] >= 2.0
        and s["vol_ratio"] >= 1.2
    ]
    # 加權分數：漲幅50% + 量比30% + 連紅20%
    max_chg = max((s["chg_pct"] for s in layer1), default=1)
    max_vol = max((s["vol_ratio"] for s in layer1), default=1)
    for s in layer1:
        s["score"] = (
            s["chg_pct"] / max_chg * 0.5 +
            s["vol_ratio"] / max_vol * 0.3 +
            s["green3"] / 3 * 0.2
        )
    layer1.sort(key=lambda x: x["score"], reverse=True)
    top15 = layer1[:15]

    # ── 第二層：TOP 5（嚴）──
    # MA5>MA20、漲幅>3%、量比>1.5x、近3日至少2日收紅（接受漲停）
    top5 = [
        s for s in top15
        if s["ma5"] and s["ma5"] > s["ma20"]
        and s["chg_pct"] >= 3.0
        and s["vol_ratio"] >= 1.5
        and s["green3"] >= 2
    ][:5]

    # 不足 5 檔時放寬補滿
    if len(top5) < 5:
        extras = [s for s in top15 if s not in top5]
        top5 = (top5 + extras)[:5]

    # 更新 watchlist.json（存 top15）
    watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
    with open(watchlist_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["watchlist"] = [
        {"code": s["code"], "name": s["name"],
         "chg_pct": round(s["chg_pct"], 1),
         "added": date.today().isoformat()}
        for s in top15
    ]
    data["last_scan"] = date.today().isoformat()
    with open(watchlist_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 組推播訊息
    lines = [
        f"【全市場強勢掃描】{date.today().strftime('%m/%d')}",
        f"掃描 {len(stocks)} 檔 → 觀察清單 {len(top15)} 檔",
        f"",
        f"篩選條件：站上MA20 / 漲幅>2% / 量比>1.2x",
    ]
    for i, s in enumerate(top15, 1):
        lines.append(
            f"{i:2}. {s['name']}（{s['code']}）"
            f"{s['close']:.0f} +{s['chg_pct']:.1f}%"
            f" 量比{s['vol_ratio']:.1f}x"
        )

    lines.append(f"\n★ 明日重點關注 TOP 5 ★")
    lines.append("條件：MA5>MA20 / 漲>3% / 量比>1.5x / 近3日2紅")
    lines.append("")
    for i, s in enumerate(top5, 1):
        lines.append(
            f"  {i}. {s['name']}（{s['code']}）"
            f"{s['close']:.0f} +{s['chg_pct']:.1f}%"
            f" 量比{s['vol_ratio']:.1f}x"
        )

    lines.append(
        "\n【右側操作攻略】\n"
        "① 不追今日強勢，等明日回測\n"
        "② 回測到今日收盤價附近不破 → 進場\n"
        "③ 停損：跌破今日最低點收盤\n"
        "④ 第一筆小倉，撐穩再加碼（倒金字塔）\n"
        "⑤ 每次加碼後停損上移"
    )

    return "\n".join(lines)
