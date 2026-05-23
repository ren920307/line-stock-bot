"""
每週五 15:35 執行
回算本週每天 TOP5 進榜股票的績效
從進榜當日收盤 → 本週五收盤，各自獨立計算
"""
import json
import os
import time
from datetime import date, timedelta
from fugle_fetcher import fetch_quote

SCAN_LOG_PATH = os.path.join(os.path.dirname(__file__), "scan_log.json")


def _this_week_dates() -> list:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=i)).isoformat() for i in range(5)]


def build_weekly_report() -> str:
    try:
        with open(SCAN_LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return "❌ 無掃描紀錄，無法產生週報。"

    week_dates = _this_week_dates()
    today_str  = date.today().strftime("%m/%d")
    monday_str = (date.today() - timedelta(days=date.today().weekday())).strftime("%m/%d")

    # 抓本週有紀錄的日期
    week_logs = {d: log[d] for d in week_dates if d in log}
    if not week_logs:
        return "本週尚無掃描紀錄。"

    # 收集所有需要查現價的股票（去重）
    # scan_log 格式：{"momentum": [...], "pullback": [...]}
    def _all_stocks(entries):
        if isinstance(entries, list):
            return entries
        return entries.get("momentum", []) + entries.get("pullback", [])

    all_codes = list({s["code"] for entries in week_logs.values() for s in _all_stocks(entries)})

    # 用 Fugle 抓現價
    current_prices = {}
    for code in all_codes:
        time.sleep(1)
        q = fetch_quote(code)
        if q and q.get("close"):
            current_prices[code] = q["close"]

    # 現價全部抓不到，跳過不推播
    if not current_prices:
        return None

    # 組報告
    lines = [
        f"📊 本週選股績效｜{monday_str}~{today_str}",
        "",
    ]

    total_pnl  = 0
    total_cnt  = 0
    win_cnt    = 0

    for day_iso in sorted(week_logs.keys()):
        entries = _all_stocks(week_logs[day_iso])
        day_label = f"{int(day_iso[5:7])}/{int(day_iso[8:10])}"
        hold_days = (date.today() - date.fromisoformat(day_iso)).days

        lines.append(f"【{day_label} 進榜｜持有 {hold_days} 天】")

        for s in entries:
            code        = s["code"]
            name        = s["name"]
            entry_price = s.get("entry_price") or s.get("price")
            cur_price   = current_prices.get(code)

            if not cur_price or not entry_price:
                lines.append(f"  {name}（{code}） 無現價資料")
                continue

            pnl_pct = (cur_price - entry_price) / entry_price * 100
            pnl_amt = round((cur_price - entry_price) * 1000)  # 一張 = 1000 股
            sign    = "▲" if pnl_pct >= 0 else "▼"
            arrow   = "+" if pnl_pct >= 0 else ""

            lines.append(
                f"  {name}（{code}）"
                f" {entry_price:.0f}→{cur_price:.0f}"
                f" {sign}{abs(pnl_pct):.1f}%"
                f" {arrow}{pnl_amt:,}"
            )

            total_pnl += pnl_amt
            total_cnt += 1
            if pnl_pct >= 0:
                win_cnt += 1

        lines.append("")

    if total_cnt > 0:
        win_rate = win_cnt / total_cnt * 100
        sign = "+" if total_pnl >= 0 else ""
        lines.append(f"勝率 {win_cnt}/{total_cnt}（{win_rate:.0f}%）")
        lines.append(f"本週總損益（各買一張）{sign}{total_pnl:,} 元")

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_weekly_report())
