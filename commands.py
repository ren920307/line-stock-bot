import json
import os
import requests
import pandas as pd
from datetime import date, datetime
from fugle_fetcher import fetch_quote, fetch_quotes_bulk
from twse_fetcher import fetch as fetch_history


def _load_watchlist():
    with open("watchlist.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _save_watchlist(data):
    with open("watchlist.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _score_stock(code: str, quote: dict) -> int:
    """計算強勢分數 0~4"""
    score = 0
    chg = quote.get("chg_pct", 0) or 0
    if chg >= 5: score += 2
    elif chg >= 2: score += 1
    df = fetch_history(code, days=25)
    if not df.empty:
        df["MA20"] = df["Close"].rolling(20).mean()
        last = df.iloc[-1]
        if quote.get("close", 0) > (last["MA20"] or 0):
            score += 1
        avg_vol = df["Volume"].tail(10).mean()
        vol_ratio = (quote.get("volume") or 0) / avg_vol if avg_vol > 0 else 0
        if vol_ratio >= 1.5:
            score += 1
    return score


def cmd_market() -> str:
    """#大盤"""
    try:
        r = requests.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
            params={"ex_ch": "tse_t00.tw", "json": "1", "delay": "0"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        d = r.json()["msgArray"][0]
        close = d.get("z", d.get("y", "-"))
        prev  = d.get("y", "-")
        high  = d.get("h", "-")
        low   = d.get("l", "-")
        try:
            chg = float(close) - float(prev)
            chg_pct = chg / float(prev) * 100
            chg_str = f"{chg:+.0f}（{chg_pct:+.2f}%）"
        except Exception:
            chg_str = "-"
        return (
            f"【大盤 加權指數】\n"
            f"收：{close}　{chg_str}\n"
            f"高：{high}　低：{low}"
        )
    except Exception as e:
        return f"大盤資料抓取失敗：{e}"


def cmd_holdings() -> str:
    """#持股"""
    data = _load_watchlist()
    holdings = data.get("holdings", [])
    if not holdings:
        return "目前無持股資料。"
    codes = [h["code"] for h in holdings]
    quotes = fetch_quotes_bulk(codes)
    lines = ["【持股即時狀況】"]
    for h in holdings:
        q = quotes.get(h["code"], {})
        if not q or q.get("close") is None:
            lines.append(f"{h['name']}（{h['code']}）— 無法取得報價")
            continue
        chg = q.get("chg_pct", 0) or 0
        arrow = "▲" if chg >= 0 else "▼"
        lines.append(f"{h['name']}（{h['code']}）\n  {q['close']:.0f} {arrow}{abs(chg):.1f}%")
    return "\n".join(lines)


def cmd_scan() -> str:
    """#掃描 — 依題材分類掃描強勢股"""
    data = _load_watchlist()
    themes = data.get("themes", {})
    if not themes:
        return "題材清單是空的。"

    # 收集所有代號（去重）
    all_stocks = {}
    for theme, stocks in themes.items():
        for s in stocks:
            all_stocks[s["code"]] = {"name": s["name"], "theme": theme}

    quotes = fetch_quotes_bulk(list(all_stocks.keys()))

    # 依題材整理強勢股
    theme_results = {}
    for code, info in all_stocks.items():
        q = quotes.get(code, {})
        if not q or q.get("close") is None:
            continue
        chg = q.get("chg_pct", 0) or 0
        if chg < 1:
            continue
        theme = info["theme"]
        if theme not in theme_results:
            theme_results[theme] = []
        theme_results[theme].append({
            "name": info["name"],
            "code": code,
            "close": q["close"],
            "chg": chg,
            "volume": q.get("volume", 0),
        })

    if not theme_results:
        return f"【題材掃描】{date.today().strftime('%m/%d')}\n今日無強勢題材。"

    lines = [f"【題材掃描】{date.today().strftime('%m/%d')}"]
    for theme, stocks in sorted(theme_results.items(), key=lambda x: max(s["chg"] for s in x[1]), reverse=True):
        best = max(stocks, key=lambda s: s["chg"])
        lines.append(f"\n{theme}（{len(stocks)}檔強勢）")
        for s in sorted(stocks, key=lambda x: x["chg"], reverse=True):
            lines.append(f"  {s['name']} {s['close']:.0f} +{s['chg']:.1f}%")

    return "\n".join(lines)


def cmd_daily_scan() -> str:
    """每日自動掃描，更新 watchlist 並推播"""
    data = _load_watchlist()
    themes = data.get("themes", {})

    all_stocks = {}
    for theme, stocks in themes.items():
        for s in stocks:
            all_stocks[s["code"]] = {"name": s["name"], "theme": theme}

    quotes = fetch_quotes_bulk(list(all_stocks.keys()))

    strong = []
    for code, info in all_stocks.items():
        q = quotes.get(code, {})
        if not q or q.get("close") is None:
            continue
        chg = q.get("chg_pct", 0) or 0
        if chg >= 2:
            strong.append({
                "code": code,
                "name": info["name"],
                "theme": info["theme"],
                "close": q["close"],
                "chg": chg,
            })

    # 更新 watchlist
    data["watchlist"] = [
        {"code": s["code"], "name": s["name"], "theme": s["theme"],
         "added": date.today().isoformat()}
        for s in strong
    ]
    _save_watchlist(data)

    if not strong:
        return f"【每日掃描】{date.today().strftime('%m/%d')}\n今日無強勢標的。"

    by_theme = {}
    for s in sorted(strong, key=lambda x: x["chg"], reverse=True):
        t = s["theme"]
        if t not in by_theme:
            by_theme[t] = []
        by_theme[t].append(s)

    lines = [f"【每日強勢掃描】{date.today().strftime('%m/%d')}"]
    for theme, stocks in by_theme.items():
        lines.append(f"\n{theme}：")
        for s in stocks:
            lines.append(f"  {s['name']}（{s['code']}）{s['close']:.0f} +{s['chg']:.1f}%")

    lines.append(f"\n共 {len(strong)} 檔列入觀察清單")
    return "\n".join(lines)
