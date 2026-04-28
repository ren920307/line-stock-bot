"""
免費指令：#大盤、#持股、#掃描
"""
import json
import requests
import pandas as pd
from twse_fetcher import fetch


def _load_watchlist():
    with open("watchlist.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _quick_stat(code: str) -> dict:
    """抓單檔的收盤、漲幅、MA5、MA20"""
    df = fetch(code, days=25)
    if df.empty:
        return {}
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    chg_pct = (last["Close"] - prev["Close"]) / prev["Close"] * 100
    above_ma20 = last["Close"] > last["MA20"]
    vol_ratio = last["Volume"] / df["Volume"].tail(10).mean() if df["Volume"].tail(10).mean() > 0 else 0
    return {
        "close": last["Close"],
        "chg_pct": chg_pct,
        "ma5": last["MA5"],
        "ma20": last["MA20"],
        "above_ma20": above_ma20,
        "vol_ratio": vol_ratio,
    }


def cmd_market() -> str:
    """#大盤 — 加權指數今日表現"""
    try:
        r = requests.get(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
            params={"ex_ch": "tse_t00.tw", "json": "1", "delay": "0"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        d = r.json()["msgArray"][0]
        name  = d.get("n", "加權指數")
        close = d.get("z", d.get("y", "-"))
        prev  = d.get("y", "-")
        high  = d.get("h", "-")
        low   = d.get("l", "-")
        vol   = d.get("v", "-")
        try:
            chg = float(close) - float(prev)
            chg_pct = chg / float(prev) * 100
            chg_str = f"{chg:+.0f}（{chg_pct:+.2f}%）"
        except Exception:
            chg_str = "-"

        return (
            f"【大盤 加權指數】\n"
            f"收：{close}　{chg_str}\n"
            f"高：{high}　低：{low}\n"
            f"成交量：{vol} 億"
        )
    except Exception as e:
        return f"大盤資料抓取失敗：{e}"


def cmd_holdings() -> str:
    """#持股 — 目前未平倉持股快速報"""
    data = _load_watchlist()
    holdings = data.get("holdings", [])
    if not holdings:
        return "目前無持股資料。"

    lines = ["【持股狀況】"]
    for h in holdings:
        stat = _quick_stat(h["code"])
        if not stat:
            lines.append(f"{h['name']}（{h['code']}）— 資料抓取失敗")
            continue
        arrow = "▲" if stat["chg_pct"] >= 0 else "▼"
        ma_status = "站上MA20" if stat["above_ma20"] else "跌破MA20"
        lines.append(
            f"{h['name']}（{h['code']}）\n"
            f"  收：{stat['close']:.0f} {arrow}{abs(stat['chg_pct']):.1f}%　{ma_status}"
        )
    return "\n".join(lines)


def cmd_scan() -> str:
    """#掃描 — 觀察清單強勢篩選"""
    data = _load_watchlist()
    watchlist = data.get("watchlist", [])
    if not watchlist:
        return "觀察清單是空的。"

    strong = []
    weak   = []

    for w in watchlist:
        stat = _quick_stat(w["code"])
        if not stat:
            continue
        score = 0
        if stat["chg_pct"] >= 3:
            score += 2
        elif stat["chg_pct"] >= 1:
            score += 1
        if stat["above_ma20"]:
            score += 1
        if stat["vol_ratio"] >= 2:
            score += 1

        entry = (
            f"{w['name']}（{w['code']}）"
            f" {stat['close']:.0f} {stat['chg_pct']:+.1f}%"
            f" 量比{stat['vol_ratio']:.1f}x"
            f"【{w.get('theme','')}】"
        )
        if score >= 3:
            strong.append(entry)
        elif stat["chg_pct"] >= 1 and stat["above_ma20"]:
            weak.append(entry)

    lines = ["【觀察清單掃描】"]
    if strong:
        lines.append("\n強勢（值得關注）：")
        lines.extend(f"  ✦ {s}" for s in strong)
    if weak:
        lines.append("\n偏強（持續追蹤）：")
        lines.extend(f"  · {s}" for s in weak)
    if not strong and not weak:
        lines.append("今日觀察清單無強勢標的。")

    return "\n".join(lines)
