import gc
import json
import os
import time
import requests
import pandas as pd
from datetime import date, datetime
from fugle_fetcher import fetch_quote, fetch_quotes_bulk
from fugle_fetcher import fetch_history as fugle_history
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


def cmd_health_check() -> str:
    """每日持股健檢：損益、距停損、技術訊號"""
    data = _load_watchlist()
    holdings = data.get("holdings", [])
    if not holdings:
        return "目前無持股資料，無法健檢。"

    today = date.today().strftime("%m/%d")
    CIRCLE = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    lines = [f"🏥 持股健檢 {today}"]
    alerts = []

    for i, h in enumerate(holdings):
        time.sleep(1)
        code  = h["code"]
        name  = h["name"]
        cost  = h.get("cost", 0)
        stop  = h.get("stop", 0)

        q = fetch_quote(code)
        if not q or q.get("close") is None:
            lines.append(f"\n{CIRCLE[i]} {name} ({code})\n  ⚠️ 無法取得報價")
            continue

        price   = q["close"]
        chg_pct = q.get("chg_pct", 0) or 0
        pnl_pct = round((price - cost) / cost * 100, 1) if cost > 0 else 0
        to_stop = round((price - stop) / price * 100, 1) if stop > 0 else None

        # 損益符號
        pnl_str  = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"
        chg_str  = f"+{chg_pct:.1f}%" if chg_pct >= 0 else f"{chg_pct:.1f}%"
        stop_str = f"距停損 {to_stop:.1f}%" if to_stop is not None else "無停損"

        # 技術分析（抓 65 日，含費波計算）
        df = fugle_history(code, days=65)
        ma5 = ma20 = None
        vol_ratio = None
        tp2 = None
        near_ma5 = False
        stop_k = False

        if not df.empty and len(df) >= 20:
            df["MA5"]  = df["Close"].rolling(5).mean()
            df["MA20"] = df["Close"].rolling(20).mean()
            last = df.iloc[-1]
            ma5  = float(last["MA5"])  if not pd.isna(last["MA5"])  else None
            ma20 = float(last["MA20"]) if not pd.isna(last["MA20"]) else None

            # 量比
            vol_today = q.get("volume") or 0
            if vol_today == 0:
                vol_today = int(df["Volume"].iloc[-1])
            avg_vol = df["Volume"].iloc[-21:-1].mean()
            vol_ratio = round(vol_today / avg_vol, 1) if avg_vol > 0 else None

            # 費波 1.618 目標（60 日低→高）
            low60  = float(df["Low"].tail(60).min())
            high60 = float(df["High"].tail(60).max())
            tp2 = round(low60 + (high60 - low60) * 1.618, 1)

            # 加碼條件：回測 MA5（現價距 MA5 在 ±2% 內）
            if ma5 and abs(price - ma5) / ma5 <= 0.02:
                near_ma5 = True

            # 止跌 K：收紅（今日漲）或下影線（low 比 close 低 1% 以上）
            low_today  = q.get("low")  or price
            open_today = q.get("open") or price
            if chg_pct >= 0 or (price - low_today) / price >= 0.01:
                stop_k = True

            del df
            gc.collect()

        # 訊號判斷
        signals = []
        if to_stop is not None and price < stop:
            signals.append("🔴 已跌破停損")
            alerts.append(f"{name}({code}) 已跌破停損 {stop:.1f}")
        elif to_stop is not None and to_stop < 3:
            signals.append("🟡 接近停損")
            alerts.append(f"{name}({code}) 距停損僅 {to_stop:.1f}%")

        if chg_pct <= -3:
            if vol_ratio and vol_ratio >= 1.5:
                signals.append("⚠️ 爆量長黑（出貨訊號）")
            else:
                signals.append("⚠️ 今日大跌")

        if ma5 and ma20:
            if price < ma20:
                signals.append("📉 跌破 MA20")
            elif ma5 < ma20:
                signals.append("📉 MA5 < MA20（動能轉弱）")

        # 加碼提醒：獲利≥5% + 量縮 + 回測MA5 + 止跌K
        if (pnl_pct >= 5 and
                vol_ratio and vol_ratio <= 1.2 and
                near_ma5 and stop_k):
            tp2_str = f"TP2 約 {tp2:.0f} 元" if tp2 else ""
            signals.append(f"🔼 加碼觀察：{tp2_str}")
            alerts.append(f"{name}({code}) 可評估加碼，{tp2_str}")

        if not signals:
            signals.append("✅ 正常")

        vol_str  = f"量比 {vol_ratio}x" if vol_ratio else ""
        ma_str   = f"MA5 {ma5:.0f} / MA20 {ma20:.0f}" if ma5 and ma20 else ""
        tp2_str  = f"TP2 {tp2:.0f}" if tp2 else ""
        tech_str = " / ".join(filter(None, [vol_str, ma_str, tp2_str]))

        block = (
            f"\n{CIRCLE[i]} {name} ({code})\n"
            f"現價 {price:.0f} 元（{chg_str}）/ 成本 {cost:.0f} 元 / 損益 {pnl_str}\n"
            f"{stop_str}"
        )
        if tech_str:
            block += f"\n{tech_str}"
        block += f"\n{' / '.join(signals)}"
        lines.append(block)

    # 彙總警示
    if alerts:
        lines.append("\n⚠️ 需要注意")
        for a in alerts:
            lines.append(f"• {a}")

    return "\n".join(lines)
