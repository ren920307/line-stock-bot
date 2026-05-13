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


def _push_watchlist_to_github():
    """健檢更新（停損移位 / tp1_done）後把 watchlist.json 推回 GitHub，避免 Render 重啟後丟失"""
    import base64
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return
    try:
        url = "https://api.github.com/repos/ren920307/line-stock-bot/contents/watchlist.json"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=headers, timeout=10)
        sha = r.json().get("sha") if r.status_code == 200 else None
        with open("watchlist.json", "r", encoding="utf-8") as f:
            content = f.read()
        body = {
            "message": f"chore: auto update watchlist {date.today().isoformat()} [skip render]",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        requests.put(url, headers=headers, json=body, timeout=15)
    except Exception as e:
        print(f"[watchlist] GitHub push 失敗：{e}")


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
    """每日持股健檢：損益、距停損、技術訊號，並自動更新動態停損"""
    data = _load_watchlist()
    holdings = [h for h in data.get("holdings", []) if not h.get("_archived")]
    if not holdings:
        return "目前無持股資料，無法健檢。"

    today = date.today().strftime("%m/%d")
    CIRCLE = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    lines = [f"🏥 持股健檢 {today}"]
    alerts = []
    stop_updates = []   # 記錄本次移停變動

    for i, h in enumerate(holdings):
        time.sleep(1)
        code  = h["code"]
        name  = h["name"]
        cost  = h.get("cost", 0)
        stop  = h.get("stop") or 0

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
        tp1 = tp2 = None
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

            # 費波目標（60 日低→高）
            low60  = float(df["Low"].tail(60).min())
            high60 = float(df["High"].tail(60).max())
            fib_rng = high60 - low60
            tp1 = round(low60 + fib_rng * 1.272, 1)
            tp2 = round(low60 + fib_rng * 1.618, 1)

            # 加碼條件：回測 MA5（現價距 MA5 在 ±3% 內）
            if ma5 and abs(price - ma5) / ma5 <= 0.03:
                near_ma5 = True

            # 止跌 K：收紅（今日漲）或下影線（low 比 close 低 1% 以上）
            low_today  = q.get("low")  or price
            open_today = q.get("open") or price
            if chg_pct >= 0 or (price - low_today) / price >= 0.01:
                stop_k = True

            del df
            gc.collect()

        # ── 動態停損（每天自動更新，只往上走）──
        stop_moved = False
        old_stop = stop
        if ma20:
            hard_floor = round(cost * 0.85, 1) if cost > 0 else 0
            # 獲利鎖：獲利 ≥ 15% 時改鎖現價 -10%，不再跟 MA20（防止大波段獲利全吐）
            if pnl_pct >= 15:
                candidate = round(price * 0.90, 1)
            else:
                candidate = round(ma20 * 0.97, 1)
            new_stop   = max(stop, candidate, hard_floor)
            # 停損不能高於現價
            if new_stop >= price:
                new_stop = max(stop, hard_floor)
            if new_stop != stop and new_stop > 0:
                h["stop"] = new_stop
                stop_updates.append(h)
                stop_moved = True
                old_stop   = stop
                stop       = new_stop

        to_stop  = round((price - stop) / price * 100, 1) if stop > 0 else None
        if stop_moved:
            stop_str = f"停損 {stop:.0f}（↑{old_stop:.0f}）/ 距停損 {to_stop:.1f}%"
        elif stop > 0:
            stop_str = f"停損 {stop:.0f} / 距停損 {to_stop:.1f}%"
        else:
            stop_str = "停損未設"

        # ── 訊號判斷 ──
        signals = []

        # 出場訊號
        if to_stop is not None and price <= stop:
            signals.append("🔴 跌破停損，出場")
            alerts.append(f"🔴 {name}({code}) 已跌破停損 {stop:.0f}")
        elif to_stop is not None and to_stop < 3:
            signals.append(f"🟠 接近停損（剩 {to_stop:.1f}%）")
            alerts.append(f"🟠 {name}({code}) 距停損僅 {to_stop:.1f}%")
        elif ma20 and price < ma20:
            signals.append("🟠 跌破 MA20，考慮出場")
            alerts.append(f"🟠 {name}({code}) 跌破 MA20（{ma20:.0f}）")

        # 動能轉弱
        if ma5 and ma20 and ma5 < ma20 and price >= ma20:
            signals.append("⚠️ MA5 < MA20，動能轉弱")

        # 今日 K 棒
        if chg_pct <= -5:
            signals.append("⚠️ 爆量長黑" if (vol_ratio and vol_ratio >= 1.5) else "⚠️ 今日大跌 -5%↓")
        elif chg_pct <= -3:
            signals.append("⚠️ 今日大跌")

        # 停利訊號（獲利鎖優先：有出場/停損訊號時跳過）
        tp1_done = h.get("tp1_done", False)
        already_exiting = any("出場" in s or "停損" in s for s in signals)

        if not already_exiting:
            if tp2 and price >= tp2 * 0.95:
                signals.append(f"🎯 到達 TP2 {tp2:.0f}，出場剩餘50%")
                alerts.append(f"🎯 {name}({code}) 到達 TP2 {tp2:.0f}，出場剩餘50%")
            elif not tp1_done and tp1 and price >= tp1 * 0.95:
                signals.append(f"🎯 到達 TP1 {tp1:.0f}，出場50%")
                alerts.append(f"🎯 {name}({code}) 到達 TP1 {tp1:.0f}，出場50%")
                h["tp1_done"] = True
                stop_updates.append(h)

        # 加碼：獲利≥5% + 量縮≤1.2 + 回測MA5±2% + 止跌K
        if (pnl_pct >= 5 and vol_ratio and vol_ratio <= 1.2 and near_ma5 and stop_k
                and not any("出場" in s or "停損" in s for s in signals)):
            tp2_hint = f"目標 {tp2:.0f}" if tp2 else ""
            signals.append(f"🔼 加碼觀察 / {tp2_hint}" if tp2_hint else "🔼 加碼觀察")
            alerts.append(f"🔼 {name}({code}) 加碼條件符合（獲利{pnl_pct:.1f}%，縮量回測MA5）")

        if not signals:
            signals.append("✅ 正常")

        vol_str  = f"量比 {vol_ratio}x" if vol_ratio else ""
        ma_str   = f"MA5 {ma5:.0f} / MA20 {ma20:.0f}" if ma5 and ma20 else ""
        tech_str = " / ".join(filter(None, [vol_str, ma_str]))

        # 停利行（有獲利才顯示）
        tp2_line = ""
        if tp1 and tp2 and pnl_pct > 0:
            dist_tp1 = round((tp1 - price) / price * 100, 1)
            dist_tp2 = round((tp2 - price) / price * 100, 1)
            if tp1_done:
                tp2_line = f"\nTP1 {tp1:.0f} ✓已出50% → TP2 {tp2:.0f}（距 {dist_tp2:.1f}%）→ 出場剩餘50%"
            elif price >= tp1 * 0.95:
                tp2_line = f"\nTP1 {tp1:.0f}（距 {dist_tp1:.1f}%）→ 出場50% / TP2 {tp2:.0f}（距 {dist_tp2:.1f}%）→ 出場剩餘50%"
            else:
                tp2_line = f"\nTP1 {tp1:.0f}（距 {dist_tp1:.1f}%）→ 出場50% / TP2 {tp2:.0f}（距 {dist_tp2:.1f}%）→ 出場剩餘50%"

        block = (
            f"\n{CIRCLE[i]} {name} ({code})\n"
            f"現價 {price:.0f} 元（{chg_str}）/ 成本 {cost:.0f} 元 / 損益 {pnl_str}\n"
            f"{stop_str}"
            f"{tp2_line}"
        )
        if tech_str:
            block += f"\n{tech_str}"
        for sig in signals:
            block += f"\n{sig}"
        lines.append(block)

    # ── 自動更新（移停 / tp1_done）：寫回 watchlist.json 並推 GitHub ──
    if stop_updates:
        _save_watchlist(data)
        _push_watchlist_to_github()

    # ── 今日行動清單（只列需要動作的）──
    if alerts:
        lines.append("\n📋 今日行動清單")
        for a in sorted(alerts, key=lambda x: ("🔼🎯🟠🔴".index(x[0]) if x[0] in "🔼🎯🟠🔴" else 99)):
            lines.append(f"• {a}")

    return "\n".join(lines)
