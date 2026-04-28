import json
import requests
import pandas as pd
from fugle_fetcher import fetch_quote, fetch_quotes_bulk
from twse_fetcher import fetch as fetch_history


def _load_watchlist():
    with open("watchlist.json", "r", encoding="utf-8") as f:
        return json.load(f)


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
        lines.append(
            f"{h['name']}（{h['code']}）\n"
            f"  {q['close']:.0f} {arrow}{abs(chg):.1f}%"
        )
    return "\n".join(lines)


def cmd_scan() -> str:
    """#掃描"""
    data = _load_watchlist()
    watchlist = data.get("watchlist", [])
    if not watchlist:
        return "觀察清單是空的。"

    codes = [w["code"] for w in watchlist]
    quotes = fetch_quotes_bulk(codes)

    strong, weak = [], []

    for w in watchlist:
        q = quotes.get(w["code"], {})
        if not q or q.get("close") is None:
            continue
        chg = q.get("chg_pct", 0) or 0

        # 抓 MA20 判斷位置
        df = fetch_history(w["code"], days=25)
        above_ma20 = False
        vol_ratio = 1.0
        if not df.empty:
            df["MA20"] = df["Close"].rolling(20).mean()
            last = df.iloc[-1]
            above_ma20 = q["close"] > (last["MA20"] if not pd.isna(last["MA20"]) else 0)
            avg_vol = df["Volume"].tail(10).mean()
            vol_ratio = (q.get("volume") or 0) / avg_vol if avg_vol > 0 else 0

        score = 0
        if chg >= 3: score += 2
        elif chg >= 1: score += 1
        if above_ma20: score += 1
        if vol_ratio >= 2: score += 1

        entry = (
            f"{w['name']}（{w['code']}）"
            f" {q['close']:.0f} {chg:+.1f}%"
            f" 量比{vol_ratio:.1f}x"
            f"【{w.get('theme','')}】"
        )
        if score >= 3:
            strong.append(entry)
        elif chg >= 1 and above_ma20:
            weak.append(entry)

    lines = ["【觀察清單掃描】（即時）"]
    if strong:
        lines.append("\n強勢（值得關注）：")
        lines.extend(f"  ✦ {s}" for s in strong)
    if weak:
        lines.append("\n偏強（持續追蹤）：")
        lines.extend(f"  · {s}" for s in weak)
    if not strong and not weak:
        lines.append("今日觀察清單無強勢標的。")

    return "\n".join(lines)
