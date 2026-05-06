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

    resist1  = round(float(high60), 1)   # 前高 = 壓力 / BSL
    support1 = round(float(ma20), 1)     # MA20 = 主要支撐
    support2 = round(float(ob_end), 1)   # OB頂 = 次要支撐

    # 進場區：FVG在現價上方才有意義，否則用MA20
    if has_fvg and fvg_low > close:
        entry_desc = f"回測FVG（{fvg_low}～{fvg_high}）止跌確認"
        entry_ref  = round((fvg_low + fvg_high) / 2, 1)
    else:
        entry_desc = f"回測MA20（{ma20:.0f}）止跌確認"
        entry_ref  = round(float(ma20), 1)

    # 停損：進場參考價 -5%
    stop    = round(entry_ref * 0.95, 1)
    target1 = resist1
    target2 = round(resist1 * 1.08, 1)
    risk    = entry_ref - stop
    rr      = round((target1 - entry_ref) / risk, 1) if risk > 0 else 0

    # 現價判斷
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

    lines += [
        "",
        "關鍵位：",
        f"壓力 {resist1}元（前高/BSL）",
        f"支撐 {support1}元（MA20）　次支撐 {support2}元（OB）",
    ]

    if has_fvg:
        lines.append(f"FVG缺口 {fvg_low}元～{fvg_high}元（未填）")

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
