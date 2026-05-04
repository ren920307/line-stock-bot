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

    # 永遠先嘗試 Fugle（盤中或收盤後當日都有資料），失敗才退回 twstock 歷史
    q = fetch_quote(code)
    if q and q.get("close"):
        close   = float(q["close"])
        high    = float(q["high"]) if q.get("high") else float(last["High"])
        low     = float(q["low"])  if q.get("low")  else float(last["Low"])
        vol     = int(q["volume"]) if q.get("volume") else int(last["Volume"])
        open_   = float(q.get("open") or last["Open"])
        prev_close = float(q["prev"]) if q.get("prev") else float(prev["Close"])
    else:
        close     = float(last["Close"])
        open_     = float(last["Open"])
        high      = float(last["High"])
        low       = float(last["Low"])
        vol       = int(last["Volume"])
        prev_close = float(prev["Close"])
    ma5    = last["MA5"]
    ma20   = last["MA20"]
    ma60   = last["MA60"]
    chg     = close - prev_close
    chg_pct = chg / prev_close * 100

    high60 = df["High"].tail(60).max()
    low60  = df["Low"].tail(60).min()
    high20 = df["High"].tail(20).max()
    low20  = df["Low"].tail(20).min()

    total_chg_pct = (close - low60) / low60 * 100

    today = date.today().strftime("%Y/%m/%d")
    label = f"{name}（{code}）" if name else code

    # 漲停判斷
    is_limit_up = chg_pct >= 9.5
    if is_limit_up:
        chg_label = f"漲停 +{chg_pct:.1f}%\n關聖帝君已經給我九個聖杯"
    else:
        chg_label = f"{chg:+.1f}（{chg_pct:+.1f}%）"

    # 均線排列
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

    # 近期起漲點（20日最低點位置）
    min_idx   = df["Low"].tail(20).idxmin()
    min_price = df["Low"].tail(20).min()
    # 估算起漲日
    min_pos   = df.index.get_loc(min_idx)
    days_ago  = len(df) - 1 - min_pos

    # OB 區：起漲前後的大陽棒區域
    ob_start = round(float(min_price) * 1.0, 0)
    ob_end   = round(float(min_price) * 1.10, 0)

    # FVG：今日跳空缺口
    fvg_low  = round(prev_close, 1)
    fvg_high = round(float(open_), 1)
    has_fvg  = fvg_high > fvg_low * 1.005

    # 支撐阻力
    support1 = round(prev_close, 1)  # 昨收
    support2 = round(float(ob_end), 1)          # OB頂
    resist1  = round(float(high60), 1)           # 前高

    # 進場、停損、目標
    if has_fvg:
        entry_desc = f"回測FVG缺口區（{fvg_low}～{fvg_high}）撐穩"
        entry_mid  = (fvg_low + fvg_high) / 2
    else:
        entry_desc = f"回測MA20（{ma20:.0f}）附近撐穩"
        entry_mid  = float(ma20) * 1.01

    stop     = round(float(support1) * 0.985, 1)
    target1  = resist1
    target2  = round(resist1 * 1.08, 1)
    risk     = entry_mid - stop
    reward1  = target1 - entry_mid
    rr       = round(reward1 / risk, 1) if risk > 0 else 0
    t1_pct   = round((target1 - entry_mid) / entry_mid * 100, 1)
    t2_pct   = round((target2 - entry_mid) / entry_mid * 100, 1)

    # 風險提示
    risks = []
    if total_chg_pct > 60:
        risks.append(f"• 60日漲幅達 {total_chg_pct:.0f}%，絕對高位，倉位控制優先")
    if consec_limit >= 2:
        risks.append(f"• 連 {consec_limit} 日漲停，隔日易開高走低，不追開盤價")
    risks.append("• 第一筆小倉，等回測確認再加碼")
    risks.append("• 每次加碼後停損上移（倒金字塔）")

    # SMC 說明
    ob_desc  = f"機構買盤成本區，回測此區有強撐"
    fvg_desc = f"今日跳空留下的未填缺口，機構可能回測此區再推升" if has_fvg else "今日無明顯跳空缺口"
    liq_desc = f"前高 {resist1} 上方聚集大量散戶停損單，是機構下一個掃單目標"

    lines = [
        f"【{label} 技術分析】",
        f"{today}",
        "",
        "【今日數據】",
        f"收：{close:.0f}（{chg_label}）",
        f"開：{open_:.1f}　高：{high:.1f}　低：{low:.1f}",
        f"量：{vol // 1000:,}張",
        f"MA5：{ma5:.1f}　MA20：{ma20:.1f}　MA60：{ma60:.1f}",
        f"60日高：{high60:.0f}　低：{low60:.0f}",
        "",
        "【形態判讀】",
        f"{trend}。",
    ]

    if consec_limit >= 2:
        lines.append(f"連 {consec_limit} 日漲停，機構強力推進。")
    lines.append(f"SMC角度：OB在 {ob_start:.0f}～{ob_end:.0f}（{ob_desc}）。")
    if has_fvg:
        lines.append(f"今日跳空留下 {fvg_low}～{fvg_high} FVG缺口（{fvg_desc}）。")
    lines.append(f"Buy-Side Liquidity 在前高 {resist1}（{liq_desc}）。")

    lines += [
        "",
        "【支撐與阻力】",
        f"強支撐：{support1}（昨收）",
        f"次支撐：{support2}（OB頂部）",
        f"壓力：{resist1}（60日前高）",
        "",
        "【進場建議（右側）】",
        f"進場：{entry_desc}",
        f"停損：跌破 {stop} 收盤出場",
        f"短線目標：{target1}（+{t1_pct}%）",
        f"中線目標：{target2}（+{t2_pct}%）",
        f"R:R ≈ {rr}:1",
        "",
        "【風險提示】",
    ] + risks + [
        "",
        "等右側確認再進，停損優先。",
    ]

    return "\n".join(lines)
