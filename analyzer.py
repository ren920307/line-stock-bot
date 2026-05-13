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

    is_limit_up = chg_pct >= 9.5

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

    # 費波延伸目標（用於突破前高時的新壓力）
    fib_range = high60 - low60
    fib_tp1   = round(low60 + fib_range * 1.272, 1)

    # 壓力（必須高於現價）
    resist_lines = []
    if close < high60 * 0.97:
        resist_lines.append(f"{round(high60, 1)} 元")
    else:
        resist_lines.append(f"{fib_tp1} 元（費波TP1延伸）")
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

    # 現價已接近或突破 60 日高（97% 以上）→ 前高壓力意義不大，改用費波 TP1
    target1 = fib_tp1 if close >= high60 * 0.97 else round(high60, 1)
    risk = entry_ref - stop
    rr   = round((target1 - entry_ref) / risk, 1) if risk > 0 else 0

    dist_to_entry = round((close - entry_ref) / entry_ref * 100, 1)
    target_pct    = round((target1 - entry_ref) / entry_ref * 100, 1)

    # ── 信心星等 ──
    # 基礎分：R:R 決定
    if rr >= 5:    stars = 5
    elif rr >= 3:  stars = 4
    elif rr >= 2:  stars = 3
    elif rr >= 1.5: stars = 2
    else:          stars = 1

    # 扣分項（每項 -1 星，累計計算，最低 1 顆）
    penalty = 0
    if dist_to_entry > 10:   penalty += 2   # 離進場區太遠
    elif dist_to_entry > 5:  penalty += 1   # 離進場區偏遠
    if total_chg_pct > 60:   penalty += 1   # 60日漲幅高
    if total_chg_pct > 80:   penalty += 1   # 60日漲幅過高（累計）
    if not (ma5 > ma20 > ma60): penalty += 1  # 均線非多頭排列
    if consec_limit >= 2:    penalty += 1   # 連漲停

    stars = max(1, stars - penalty)
    stars_str = "★" * stars + "☆" * (5 - stars)

    # ── 行動建議（完全由星等決定，確保和星星一致）──
    if stars >= 4:
        if dist_to_entry > 3:
            action = f"現價離進場區約 +{dist_to_entry}%，等回測確認後進場"
        else:
            action = f"R:R {rr}:1，條件優，確認止跌 K 後可進場"
    elif stars == 3:
        if dist_to_entry > 5:
            action = f"現價離進場區約 +{dist_to_entry}%，等回測後確認再進場"
        else:
            action = f"R:R {rr}:1，條件尚可，確認進場區止跌後再行動"
    elif stars == 2:
        if dist_to_entry > 5:
            action = f"現價離進場區約 +{dist_to_entry}%，不追高，等回測"
        elif rr >= 3:
            action = f"R:R {rr}:1，但風險因素拉低評分，謹慎進場"
        else:
            action = f"R:R {rr}:1 偏低，等更好位置"
    else:
        if dist_to_entry > 5:
            action = f"現價離進場區約 +{dist_to_entry}%，不追高"
        else:
            action = f"R:R {rr}:1，條件不足，建議觀望"

    # 漲跌顯示
    chg_arrow = "▲" if chg >= 0 else "▼"
    chg_label = f"+{chg_pct:.1f}%" if chg >= 0 else f"{chg_pct:.1f}%"
    limit_tag = " 漲停" if is_limit_up else ""

    # 趨勢簡化顯示（只顯示狀態，不顯示 MA 數值）
    if ma5 > ma20 > ma60:
        trend_short = "多頭排列 ( 5 / 20 / 60 )"
    elif ma5 < ma20 < ma60:
        trend_short = "空頭排列 ( 5 / 20 / 60 )"
    else:
        trend_short = "均線糾結整理中"

    # 風險提示列表
    risk_notes = []
    if total_chg_pct > 60:
        risk_notes.append(f"60 日漲幅 {total_chg_pct:.0f}%，注意高位風險")
    if consec_limit >= 2:
        risk_notes.append(f"連 {consec_limit} 日漲停，注意隔日開高走低風險")

    # 主要壓力與支撐（只取各一個最重要的）
    main_resist = resist_lines[0] if resist_lines else "—"
    main_support = f"{support_main[0]} 元" if support_main else (f"{support_sec[0]} 元" if support_sec else "—")

    # 進場區描述
    if fvg_position == "below" and fvg_low is not None:
        entry_zone = f"待回測 {fvg_low} ～ {fvg_high} 止跌"
    elif support_main:
        entry_zone = f"待回測 MA20（{support_main[0]} 元）止跌"
    elif support_sec:
        entry_zone = f"待回測 MA60（{support_sec[0]} 元）止跌"
    else:
        entry_zone = "均線全在上方，暫不建議進場"

    # ── 輸出 ──
    name_part = name if name else code
    code_part = code if name else ""

    lines = [
        f"✦ {name_part} {code_part} ✦  {today}",
        "",
        "【 行情現狀 】",
        f"▸ 收盤價 {close:.0f} 元 ( {chg_arrow}{chg_label}{limit_tag} )",
        f"▸ 成交量 {vol // 1000:,} 張",
        f"▸ 趨勢為{trend_short}",
        "",
        "【 關鍵價位 】",
        f"▸ 壓力位 {main_resist}",
        f"▸ 支撐位 {main_support}",
    ]

    if fvg_support_line:
        lines.append(f"▸ FVG 區 {fvg_low} ～ {fvg_high} 元")
    elif fvg_position == "above" and fvg_low is not None:
        lines.append(f"▸ 未填缺口 {fvg_low} ～ {fvg_high} 元（壓力）")

    lines += [
        "",
        "【 交易計畫 】",
        f"▸ 進場區：{entry_zone}",
        f"▸ 停損：{stop} 元",
        f"▸ 目標：{target1} 元 (+{target_pct}%)",
    ]

    # 明日追漲劇本：今日漲幅 >= 4% 且趨勢不是空頭，才顯示
    if chg_pct >= 4 and ma5 > ma60:
        chase_entry  = round(close * 1.005, 0)  # 站穩今收 +0.5% 確認
        breakout_mid = round((open_ + close) / 2, 1)  # 突破K中點

        rr_a = round((target1 - chase_entry) / (chase_entry - stop), 1) if chase_entry > stop else 0
        rr_b_risk = chase_entry - breakout_mid
        rr_b = round((target1 - chase_entry) / rr_b_risk, 1) if rr_b_risk > 0 else 0

        stop_a_str = (f"停損A：{stop} 元　R:R {rr_a}:1"
                      if rr_a >= 2 else
                      f"停損A：{stop} 元　R:R {rr_a}:1（偏低）")
        stop_b_str = (f"停損B：{breakout_mid} 元（K棒中點）　R:R {rr_b}:1"
                      if rr_b >= 2 else
                      f"停損B：{breakout_mid} 元（K棒中點）　R:R {rr_b}:1（偏低）")

        lines += [
            "",
            "【 明日追漲劇本 】",
            f"▸ 條件：開盤站穩 {close:.0f} 元 + 量比 > 1.5x",
            f"▸ 進場：{chase_entry:.0f} 元",
            f"▸ {stop_a_str}（量比 1.5x～2x）",
            f"▸ {stop_b_str}（量比 > 2x）",
            f"▸ 目標：{target1} 元",
        ]

    lines += [
        "",
        "【 推薦指數 】",
        f"▸ 信心程度：{stars_str}",
        f"▸ {action}",
    ]

    for note in risk_notes:
        lines.append(f"▸ {note}")

    return "\n".join(lines)
