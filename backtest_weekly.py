"""
本週模擬操盤回測
- 用健檢相同的進場/加碼/停損規則
- 進場：進榜隔日開盤
- 每份 10 萬元（允許零股）
- 加碼上限 3 次（含初始）
- 跌破 MA20 或停損 → 全砍
- 結算：週五收盤現價
"""
import json
import os
import time
from datetime import date, timedelta
import pandas as pd
from fugle_fetcher import fetch_history

SCAN_LOG_PATH = os.path.join(os.path.dirname(__file__), "scan_log.json")
UNIT_AMOUNT = 100_000  # 每份 10 萬
MAX_ADDS = 3            # 含第 1 份共 3 份


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """加 MA5、MA20、量比"""
    df = df.copy()
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["AvgVol20"] = df["Volume"].rolling(20).mean()
    df["VolRatio"] = (df["Volume"] / df["AvgVol20"]).round(2)
    return df


def _next_trading_day(df: pd.DataFrame, after: str):
    """找 after 之後第一個交易日（df 索引必須是 DatetimeIndex）"""
    after_ts = pd.Timestamp(after)
    future = df.index[df.index > after_ts]
    return future[0] if len(future) > 0 else None


def _simulate(code: str, name: str, entry_date_iso: str, week_end_iso: str) -> dict:
    """模擬單檔操作。回傳 {pnl, log, ...}"""
    df = fetch_history(code, days=90)
    if df.empty or len(df) < 25:
        return {"error": f"{name}({code}) 無 K 線資料", "log": [], "pnl": 0}

    df = _enrich(df)

    entry_day = _next_trading_day(df, entry_date_iso)
    if entry_day is None:
        return {"error": f"{name}({code}) 進榜日 {entry_date_iso} 之後尚無交易日", "log": [], "pnl": 0}

    week_end_ts = pd.Timestamp(week_end_iso)
    sim_dates = [d for d in df.index if entry_day <= d <= week_end_ts]
    if not sim_dates:
        return {"error": f"{name}({code}) 進場後無資料", "log": [], "pnl": 0}

    # ── 第 1 份進場 ──
    entry_row = df.loc[entry_day]
    entry_price = float(entry_row["Open"])
    shares = UNIT_AMOUNT / entry_price
    cost_total = UNIT_AMOUNT
    add_count = 1

    ma20_at_entry = entry_row["MA20"]
    initial_stop = max(
        ma20_at_entry * 0.97 if not pd.isna(ma20_at_entry) else 0,
        entry_price * 0.85,
    )
    stop = float(initial_stop)
    log = [f"{entry_day.strftime('%m/%d')} 開盤進場 {entry_price:.1f}（{shares:.0f}股／10萬）停損 {stop:.1f}"]

    exit_price = None
    exit_date  = None
    exit_reason = None

    # ── 逐日模擬（從進場日次日開始，因為進場日當天剛買） ──
    for d in sim_dates[1:]:
        row = df.loc[d]
        close = float(row["Close"])
        ma5   = row["MA5"]
        ma20  = row["MA20"]
        vol_ratio = row["VolRatio"]
        avg_cost = cost_total / shares

        # 動態停損更新
        if not pd.isna(ma20):
            new_stop = max(stop, float(ma20) * 0.97, avg_cost * 0.85)
            if new_stop > stop + 0.01:
                log.append(f"{d.strftime('%m/%d')} 停損上移 {stop:.1f}→{new_stop:.1f}")
                stop = new_stop

        # 出場：跌破停損
        if close <= stop:
            exit_price = close
            exit_date  = d
            exit_reason = f"跌破停損 {stop:.1f}"
            log.append(f"{d.strftime('%m/%d')} 收 {close:.1f} → {exit_reason}，全砍")
            break

        # 出場：跌破 MA20
        if not pd.isna(ma20) and close < float(ma20):
            exit_price = close
            exit_date  = d
            exit_reason = f"跌破 MA20 {float(ma20):.1f}"
            log.append(f"{d.strftime('%m/%d')} 收 {close:.1f} → {exit_reason}，全砍")
            break

        # 加碼：獲利≥5% + 量比≤1.2 + 距MA5±3% + 止跌K
        pnl_pct = (close - avg_cost) / avg_cost * 100
        near_ma5 = (not pd.isna(ma5)) and abs(close - float(ma5)) / float(ma5) <= 0.03
        low_today = float(row["Low"])
        open_today = float(row["Open"])
        chg_pct = (close - float(df.loc[df.index < d, "Close"].iloc[-1])) / float(df.loc[df.index < d, "Close"].iloc[-1]) * 100 if (df.index < d).any() else 0
        stop_k = chg_pct >= 0 or (close - low_today) / close >= 0.01

        if (add_count < MAX_ADDS and pnl_pct >= 5
                and vol_ratio is not None and not pd.isna(vol_ratio) and float(vol_ratio) <= 1.2
                and near_ma5 and stop_k):
            add_shares = UNIT_AMOUNT / close
            shares += add_shares
            cost_total += UNIT_AMOUNT
            add_count += 1
            log.append(
                f"{d.strftime('%m/%d')} 加碼第{add_count}份 {close:.1f}"
                f"（獲利{pnl_pct:.1f}% / 量比{float(vol_ratio):.1f} / 距MA5±3%）"
            )

    # ── 結算 ──
    if exit_price is None:
        # 抱到週末，用最後一天收盤計算
        last_day = sim_dates[-1]
        final_price = float(df.loc[last_day, "Close"])
        final_status = f"抱到 {last_day.strftime('%m/%d')} 收 {final_price:.1f}"
    else:
        final_price = exit_price
        final_status = f"{exit_date.strftime('%m/%d')} {exit_reason} 出場 {exit_price:.1f}"

    pnl = round(final_price * shares - cost_total)
    pnl_pct = round((final_price * shares - cost_total) / cost_total * 100, 1)

    return {
        "code": code,
        "name": name,
        "entry_price": entry_price,
        "final_price": final_price,
        "add_count": add_count,
        "cost_total": cost_total,
        "shares": shares,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
        "final_status": final_status,
        "log": log,
    }


def _format_group(title: str, results: list) -> list:
    """格式化單組結果"""
    lines = [f"\n{title}"]
    if not results:
        lines.append("  本週無進榜")
        return lines

    total_pnl = 0
    win_cnt   = 0
    for r in results:
        if "error" in r:
            lines.append(f"  {r['error']}")
            continue
        sign = "+" if r["pnl"] >= 0 else ""
        adds = f"已加碼 {r['add_count']-1} 次" if r["add_count"] > 1 else "未加碼"
        lines.append(
            f"  {r['name']}（{r['code']}）{r['entry_price']:.0f}→{r['final_price']:.0f}"
            f" {sign}{r['pnl_pct']:.1f}%  {sign}{r['pnl']:,} 元  {adds}"
        )
        for entry in r["log"]:
            lines.append(f"    {entry}")
        total_pnl += r["pnl"]
        if r["pnl"] >= 0:
            win_cnt += 1

    real_results = [r for r in results if "error" not in r]
    if real_results:
        lines.append(
            f"  小計：勝 {win_cnt}/{len(real_results)} / 損益 "
            f"{'+' if total_pnl>=0 else ''}{total_pnl:,} 元"
        )
    return lines


def build_backtest_report() -> str:
    """讀本週 scan_log，模擬追漲組與回測組，回傳完整訊息"""
    try:
        with open(SCAN_LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return "❌ 讀不到 scan_log，無法回測"

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_iso = [(monday + timedelta(days=i)).isoformat() for i in range(5)]
    week_logs = {d: log[d] for d in week_iso if d in log}
    if not week_logs:
        return "本週無掃描紀錄，無法回測"

    # 收集 momentum / pullback 各組第一次出現的股票（按進榜日）
    seen_m, seen_p = set(), set()
    momentum_entries = []
    pullback_entries = []
    for d_iso in sorted(week_logs.keys()):
        for s in week_logs[d_iso].get("momentum", []):
            if s["code"] not in seen_m:
                seen_m.add(s["code"])
                momentum_entries.append((s["code"], s["name"], d_iso))
        for s in week_logs[d_iso].get("pullback", []):
            if s["code"] not in seen_p:
                seen_p.add(s["code"])
                pullback_entries.append((s["code"], s["name"], d_iso))

    week_end = today.isoformat()

    # 模擬（限速避免打爆 Fugle）
    momentum_results = []
    for code, name, d_iso in momentum_entries:
        time.sleep(1.2)
        momentum_results.append(_simulate(code, name, d_iso, week_end))

    pullback_results = []
    for code, name, d_iso in pullback_entries:
        time.sleep(1.2)
        pullback_results.append(_simulate(code, name, d_iso, week_end))

    monday_str = monday.strftime("%m/%d")
    today_str  = today.strftime("%m/%d")
    lines = [f"📊 本週模擬操盤｜{monday_str}~{today_str}（每份 10 萬）"]

    lines += _format_group("🔥 追漲組", momentum_results)
    lines += _format_group("📉 回測組", pullback_results)

    # 比較總損益
    total_m = sum(r["pnl"] for r in momentum_results if "pnl" in r)
    total_p = sum(r["pnl"] for r in pullback_results if "pnl" in r)
    if total_m or total_p:
        winner = "追漲組" if total_m > total_p else ("回測組" if total_p > total_m else "平手")
        lines.append(f"\n📈 本週贏家：{winner}")
        lines.append(f"  追漲 {'+' if total_m>=0 else ''}{total_m:,} vs 回測 {'+' if total_p>=0 else ''}{total_p:,}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_backtest_report())
