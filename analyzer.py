import pandas as pd
from datetime import date
from twse_fetcher import fetch
from fugle_fetcher import fetch_quote


def analyze(code: str, name: str = "") -> str:
    df = fetch(code)
    if df.empty:
        return f"找不到 {code} 的資料，請確認代號是否正確。"

    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    prev2 = df.iloc[-3] if len(df) >= 3 else prev

    q = fetch_quote(code)
    if q and q.get("close"):
        close      = float(q["close"])
        high       = float(q["high"]) if q.get("high") else float(last["High"])
        low        = float(q["low"])  if q.get("low")  else float(last["Low"])
        vol        = int(q["volume"]) if q.get("volume") else int(last["Volume"])
        open_      = float(q.get("open") or last["Open"])
        prev_close = float(q["prev"]) if q.get("prev") else float(prev["Close"])
    else:
        close      = float(last["Close"])
        open_      = float(last["Open"])
        high       = float(last["High"])
        low        = float(last["Low"])
        vol        = int(last["Volume"])
        prev_close = float(prev["Close"])

    ma5  = float(last["MA5"])
    ma20 = float(last["MA20"])
    ma60 = float(last["MA60"])
    chg     = close - prev_close
    chg_pct = chg / prev_close * 100

    high60 = float(df["High"].tail(60).max())
    low60  = float(df["Low"].tail(60).min())

    total_chg_pct = (close - low60) / low60 * 100

    today = date.today().strftime("%Y/%m/%d")
    label = f"{name}（{code}）" if name else code

    # 漲停判斷
    is_limit_up = chg_pct >= 9.5
    if is_limit_up:
        chg_label = f"漲停 +{chg_pct:.1f}%\n關聖帝君已經給我九個聖杯"
    else:
        chg_label = f"{chg:+.1f}（{chg_pct:+.1f}%）"

    # 均線排列（嚴格定義：MA5>MA20>MA60 才是多頭）
    if ma5 > ma20 > ma60:
        trend = "多頭排列（MA5>MA20>MA60）"
    elif ma5 < ma20 < ma60:
        trend = "空頭排列（MA5<MA20<MA60）"
    else:
        trend = "均線糾結整理中"

    # 連漲停偵測
    consec_limit = 0
    for i in range(len(df) - 1, max(len(df) - 6, 0) - 1, -1):
        row = df.iloc[i]
        p   = df.iloc[i - 1]
        if (row["Close"] - p["Close"]) / p["Close"] * 100 >= 9.5:
            consec_limit += 1
        else:
            break

    # FVG：三K結構（前K高 < 後K低）才算，並判斷位於現價上方還是下方
    # 用前3根K棒：prev2(i-2), prev(i-1), last(i)
    fvg_low = fvg_high = None
    fvg_position = None  # "above" / "below"

    prev2_high = float(prev2["High"])
    last_low   = float(last["Low"]) if not q else float(q.get("low") or last["Low"])

    if last_low > prev2_high * 1.003:
        # 近3K形成看漲FVG（缺口在下方，屬未填支撐）
        fvg_low  = round(prev2_high, 1)
        fvg_high = round(last_low, 1)
        fvg_position = "below"
    else:
        # 退而求其次：偵測昨收→今開的跳空（大於0.5%）
        gap_low  = round(prev_close, 1)
        gap_high = round(open_, 1)
        if gap_high > gap_low * 1.005:
            fvg_low  = gap_low
            fvg_high = gap_high
            # 跳空後現價在缺口上方：缺口是下方支撐；現價在缺口下方：缺口是上方壓力
            fvg_position = "below" if close >= gap_high else "above"

    # 壓力（高於現價）
    resist_lines = [f"{round(high60, 1)}元（60日高/BSL）"]
    if fvg_position == "above" and fvg_low is not None:
        resist_lines.append(f"未填缺口 {fvg_low}～{fvg_high}元（壓力）")

    # 支撐（低於現價）
    support_main = None
    support_sec  = None

    if ma20 < close:
        support_main = (round(ma20, 1), "MA20")
    if ma60 < close:
        support_sec = (round(ma60, 1), "MA60")

    fvg_support_line = None
    if fvg_position == "below" and fvg_low is not None and fvg_high < close:
        fvg_support_line = f"FVG缺口 {fvg_low}元～{fvg_high}元（未填支撐）"

    # 進場區：優先 FVG 支撐 > MA20 > MA60
    if fvg_position == "below" and fvg_low is not None:
        entry_ref  = round((fvg_low + fvg_high) / 2, 1)
        entry_desc = f"回測FVG（{fvg_low}～{fvg_high}）止跌確認"
    elif support_main:
        entry_ref  = support_main[0]
        entry_desc = f"回測MA20（{entry_ref}）止跌確認"
    elif support_sec:
        entry_ref  = support_sec[0]
        entry_desc = f"回測MA60（{entry_ref}）止跌確認"
    else:
        entry_ref  = round(close, 1)
        entry_desc = "均線全在上方，結構偏弱，暫不建議進場"

    # 停損：取 MA60 下 3% 與進場價下 5% 中較高者（有結構支撐優先）
    stop_ma60  = round(ma60 * 0.97, 1)
    stop_entry = round(entry_ref * 0.95, 1)
    stop = max(stop_ma60, stop_entry) if stop_ma60 < entry_ref else stop_entry

    target1 = round(high60, 1)
    risk = entry_ref - stop
    rr   = round((target1 - entry_ref) / risk, 1) if risk > 0 else 0

    dist_to_entry = round((close - entry_ref) / entry_ref * 100, 1)
    if dist_to_entry > 5:
        action = f"現價離進場區 +{dist_to_entry}%，等回測再看"
    elif rr >= 3:
        action = f"進場區附近，R:R {rr}:1，條件符合"
    elif rr >= 1.5:
        action = f"R:R {rr}:1，偏低，等更好位置"
    else:
        action = f"R:R {rr}:1，不宜追，等回測"

    # 風險警示
    warnings = []
    if total_chg_pct > 60:
        warnings.append(f"⚠ 60日漲幅 {total_chg_pct:.0f}%，高位風險大")
    if consec_limit >= 2:
        warnings.append(f"⚠ 連 {consec_limit} 日漲停，隔日開高走低風險高")

    lines = [
        f"【{label}】{today}",
        f"收 {close:.0f}元（{chg_label}）　量 {vol // 1000:,}張",
        f"MA5 {ma5:.0f}元　MA20 {ma20:.0f}元　MA60 {ma60:.0f}元",
        f"60日高 {high60:.0f}元　低 {low60:.0f}元",
        "",
        f"趨勢：{trend}",
    ]

    if consec_limit >= 2:
        lines.append(f"連 {consec_limit} 日漲停，強勢推進中")

    lines += ["", "關鍵位："]

    # 壓力區（由低到高）
    for r in resist_lines:
        lines.append(f"壓力 {r}")

    # 支撐區（高到低）
    if support_main:
        lines.append(f"支撐 {support_main[0]}元（{support_main[1]}）")
    if fvg_support_line:
        lines.append(fvg_support_line)
    if support_sec:
        lines.append(f"次支撐 {support_sec[0]}元（{support_sec[1]}）")

    lines += [
        "",
        f"進場區：{entry_desc}",
        f"停損：{stop}元　目標：{target1}元（+{round((target1-entry_ref)/entry_ref*100,1)}%）",
        f"→ {action}",
    ]

    if warnings:
        lines.append("")
        lines += warnings

    return "\n".join(lines)
