"""
全市場強勢股掃描（右側交易邏輯）：
第一層 15 檔：站上MA20、多頭排列MA5>MA20>MA60、漲幅>4%、量比>2.5x
第二層 TOP5：額外加「糾結均線發散」或「漲幅最強」排序
"""
import requests
import pandas as pd
import json
import os
import re
from datetime import date
from fugle_fetcher import fetch_history

# 直接過濾黑名單（下市/低勝率）
BLACKLIST = {"6434", "2363", "1503", "1514", "3217", "3592"}

# 篩選門檻
MIN_CHANGE_PCT   = 4.0   # 漲幅 > 4%
MIN_VOL_RATIO    = 2.5   # 量比 > 2.5x
MA_SPREAD_THRESH = 0.08  # 糾結均線：MA5/20/60 離散 < 8%


def _is_stock(code: str, name: str) -> bool:
    if re.match(r'^0\d{4}', code):
        return False
    if re.match(r'^\d{6}', code):
        return False
    if any(x in name for x in ["ETF", "債", "權", "特", "存託"]):
        return False
    if not re.match(r'^\d{4}$', code):
        return False
    if code in BLACKLIST:
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
                if chg_pct < MIN_CHANGE_PCT or vol < 100:
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
                if chg_pct < MIN_CHANGE_PCT or vol < 50:
                    continue
                stocks.append({"code": code, "name": name, "close": close,
                                "chg_pct": chg_pct, "volume": vol, "market": "OTC"})
            except Exception:
                continue
    except Exception:
        pass

    return stocks


def _enrich(s: dict, df: pd.DataFrame) -> dict:
    """補充 MA5/MA20/MA60、量比、近3日收紅、糾結均線"""
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    last = df.iloc[-1]

    avg_vol   = df["Volume"].tail(20).mean()
    vol_ratio = s["volume"] * 1000 / avg_vol if avg_vol > 0 else 0

    recent3   = df.tail(3)
    green3    = int((recent3["Close"] >= recent3["Open"]).sum())

    ma5  = float(last["MA5"])  if not pd.isna(last["MA5"])  else None
    ma20 = float(last["MA20"]) if not pd.isna(last["MA20"]) else None
    ma60 = float(last["MA60"]) if not pd.isna(last["MA60"]) else None

    converged = False
    if ma5 and ma20 and ma60 and min(ma5, ma20, ma60) > 0:
        spread = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / min(ma5, ma20, ma60)
        converged = spread < MA_SPREAD_THRESH

    s["ma5"]       = round(ma5,  1) if ma5  else None
    s["ma20"]      = round(ma20, 1) if ma20 else None
    s["ma60"]      = round(ma60, 1) if ma60 else None
    s["vol_ratio"] = round(vol_ratio, 1)
    s["green3"]    = green3
    s["converged"] = converged
    return s


def run_daily_scan() -> str:
    stocks = fetch_market_data()
    if not stocks:
        return "全市場資料抓取失敗，請稍後再試。"

    # 漲幅前 80 檔補技術指標
    candidates = sorted(stocks, key=lambda x: x["chg_pct"], reverse=True)[:80]

    enriched = []
    for s in candidates:
        try:
            df = fetch_history(s["code"], days=65)
            if df.empty or len(df) < 20:
                continue
            s = _enrich(s, df)
            if s["ma20"] is None:
                continue
            enriched.append(s)
        except Exception:
            continue

    # ── 第一層：15 檔 ──
    # 站上MA20、多頭排列MA5>MA20>MA60、漲幅>4%、量比>2.5x
    layer1 = [
        s for s in enriched
        if s["close"] > (s["ma20"] or 0)
        and s["ma5"] and s["ma20"] and s["ma60"]
        and s["ma5"] > s["ma20"] > s["ma60"]
        and s["chg_pct"] >= MIN_CHANGE_PCT
        and s["vol_ratio"] >= MIN_VOL_RATIO
    ]

    # 加權分數：糾結突破優先，其次漲幅、量比
    max_chg = max((s["chg_pct"] for s in layer1), default=1)
    max_vol = max((s["vol_ratio"] for s in layer1), default=1)
    for s in layer1:
        s["score"] = (
            (0.2 if s["converged"] else 0) +
            s["chg_pct"] / max_chg * 0.5 +
            s["vol_ratio"] / max_vol * 0.3
        )
    layer1.sort(key=lambda x: x["score"], reverse=True)
    top15 = layer1[:15]

    # ── 第二層：TOP 5（糾結突破 or 漲幅最強）──
    top5 = [s for s in top15 if s["converged"]][:5]
    if len(top5) < 5:
        extras = [s for s in top15 if not s["converged"]]
        top5 = (top5 + extras)[:5]

    # 更新 watchlist.json
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
    today = date.today().strftime("%m/%d")
    lines = [
        f"📈 強勢股掃描｜{today}",
        f"條件：多頭排列 / 漲>4% / 量比>2.5x",
        f"共 {len(top15)} 檔通過",
        "",
    ]
    for i, s in enumerate(top15, 1):
        tag = " 糾結↑" if s["converged"] else ""
        lines.append(
            f"{i:2}. {s['name']}（{s['code']}）"
            f" {s['close']:.0f} +{s['chg_pct']:.1f}%"
            f" 量比{s['vol_ratio']:.1f}x{tag}"
        )

    lines.append(f"\n★ 明日重點 TOP5 ★")
    lines.append("（糾結均線發散優先）")
    lines.append("")
    for i, s in enumerate(top5, 1):
        tag = " 糾結↑" if s["converged"] else ""
        lines.append(
            f"  {i}. {s['name']}（{s['code']}）"
            f" {s['close']:.0f} +{s['chg_pct']:.1f}%"
            f" 量比{s['vol_ratio']:.1f}x{tag}"
        )

    lines.append(
        "\n【右側操作攻略】\n"
        "① 不追今日強勢，等明日回測\n"
        "② 回測今日收盤附近不破 → 進場\n"
        "③ 停損：跌破今日最低收盤\n"
        "④ 首倉小，撐穩再加碼（倒金字塔）\n"
        "⑤ 每次加碼停損上移"
    )

    return "\n".join(lines)
