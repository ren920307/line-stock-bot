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

    last = df.iloc[-1]
    prev = df.iloc[-2]

    q = fetch_quote(code)
    if q and q.get("close"):
        close      = float(q["close"])
        high_today = float(q["high"])  if q.get("high")  else float(last["High"])
        low_today  = float(q["low"])   if q.get("low")   else float(last["Low"])
        vol        = int(q["volume"])  if q.get("volume") else int(last["Volume"])
        open_today = float(q.get("open") or last["Open"])
        prev_close = float(q["prev"])  if q.get("prev")  else float(prev["Close"])
    else:
        close      = float(last["Close"])
        open_today = float(last["Open"])
        high_today = float(last["High"])
        low_today  = float(last["Low"])
        vol        = int(last["Volume"])
        prev_close = float(prev["Close"])

    ma5  = float(last["MA5"])
    ma20 = float(last["MA20"])
    ma60 = float(last["MA60"])

    chg_pct  = (close - prev_close) / prev_close * 100
    is_limit = chg_pct >= 9.5

    avg_vol   = df["Volume"].iloc[-21:-1].mean()
    vol_ratio = round(vol / avg_vol, 1) if avg_vol > 0 else 1.0

    high60    = float(df["High"].tail(60).max())
    low60     = float(df["Low"].tail(60).min())
    fib_range = high60 - low60

    if fib_range > 0:
        fib_tp1 = round(low60 + fib_range * 1.272, 1)
        fib_tp2 = round(low60 + fib_range * 1.618, 1)
        ratio   = (close - low60) / fib_range
    else:
        fib_tp1 = round(high60 * 1.10, 1)
        fib_tp2 = round(high60 * 1.20, 1)
        ratio   = 1.0

    if ratio >= 1.0:    fib_pos = "突破前高"
    elif ratio >= 0.786: fib_pos = "費波 0.786 以上"
    elif ratio >= 0.618: fib_pos = "費波 0.618～0.786"
    elif ratio >= 0.500: fib_pos = "費波 0.500～0.618"
    elif ratio >= 0.382: fib_pos = "費波 0.382～0.500"
    else:                fib_pos = "費波 0.382 以下"

    if ma5 > ma20 > ma60:   trend = "多頭排列（5>20>60）"
    elif ma5 < ma20 < ma60: trend = "空頭排列（5<20<60）"
    else:                   trend = "均線糾結"

    # 目標：接近前高用費波延伸，否則取 60 日高
    target = fib_tp1 if close >= high60 * 0.90 else round(high60, 1)
    in_up  = ma20 > ma60

    today    = date.today().strftime("%Y/%m/%d")
    label    = f"{name}（{code}）" if name else code
    chg_sign = "▲" if chg_pct >= 0 else "▼"
    chg_str  = f"+{chg_pct:.1f}%" if chg_pct >= 0 else f"{chg_pct:.1f}%"
    limit_tag = "（漲停）" if is_limit else ""

    def _rr(entry, stop, tgt):
        risk = entry - stop
        return round((tgt - entry) / risk, 1) if risk > 0.01 else 0

    lines = [
        f"✦ {label} ✦  {today}",
        "",
        "【 行情現狀 】",
        f"▸ 收盤 {close:.0f} 元（{chg_sign}{chg_str}）{limit_tag}",
        f"▸ 量比 {vol_ratio}x / {vol//1000:,} 張",
        f"▸ {trend}　MA5 {ma5:.0f} / MA20 {ma20:.0f} / MA60 {ma60:.0f}",
        f"▸ 費波位置：{fib_pos}（60日 {low60:.0f}～{high60:.0f}）",
        "",
        "【 右側進場計畫 】",
    ]

    # ── 情境判斷 ──
    is_strong   = chg_pct >= 4 and vol_ratio >= 1.5
    is_moderate = 2 <= chg_pct < 4
    is_pullback = -3 <= chg_pct <= 3 and vol_ratio <= 1.5 and in_up
    is_danger   = chg_pct <= -7

    stars  = 2
    action = "等待訊號"

    if is_danger:
        lines += [
            f"⚠️ 今日重挫（{chg_str}），暫不追",
            f"▸ 等縮量止跌K（紅K或下影線）確認後再評估",
            f"▸ 關鍵防守：MA20 {ma20:.0f} 元；若守不住等 MA60 {ma60:.0f} 元再看",
        ]
        stars  = 1
        action = "重挫觀望，勿急進"

    elif is_strong:
        entry  = round(close * 1.005)
        stop_a = round(low_today, 1)
        k_mid  = round((open_today + close) / 2, 1)
        stop_b = k_mid if k_mid < close * 0.999 else round(low_today * 1.01, 1)
        rr_a   = _rr(entry, stop_a, target)
        rr_b   = _rr(entry, stop_b, target)

        lines += [
            f"🔥 強勢日（{chg_str} / 量比 {vol_ratio}x） → 明日追漲",
            f"▸ 條件：開盤站穩 {close:.0f} 元 + 量比 > 1.5x",
            f"▸ 進場：{entry:.0f} 元（+0.5% 確認站穩）",
            f"▸ 停損A {stop_a} 元（今低）　R:R {rr_a}:1　← 量比 1.5x～2x",
            f"▸ 停損B {stop_b} 元（K棒中點）　R:R {rr_b}:1　← 量比 > 2x",
            f"▸ 目標 TP1：{target:.0f} 元　TP2：{fib_tp2:.0f} 元",
        ]

        if in_up:
            pb_entry = round(ma20 * 1.01)
            pb_stop  = round(ma20 * 0.95, 1)
            pb_rr    = _rr(pb_entry, pb_stop, target)
            dist     = round((close / pb_entry - 1) * 100, 1)
            lines += [
                "",
                f"📉 備案：若明日開低不追，等回測 MA20（{ma20:.0f}）止跌K",
                f"▸ 進場 {pb_entry:.0f} / 停損 {pb_stop} / R:R {pb_rr}:1（現價距 MA20 約 +{dist}%）",
            ]

        rr_main = rr_b
        if rr_main >= 3:   stars = 5
        elif rr_main >= 2: stars = 4
        else:              stars = 3
        if not in_up:      stars -= 1
        if is_limit:       stars -= 1
        if ratio < 0.382:  stars -= 1
        stars = max(1, min(5, stars))

        if stars >= 4:
            action = f"強勢確認，明日開盤站穩 {close:.0f} 直接追，用停損B控風險"
        else:
            action = f"強勢但有疑慮，用停損A（今低）保守操作或等回測"

    elif is_moderate:
        entry  = round(close * 1.005)
        stop_a = round(low_today, 1)
        rr_a   = _rr(entry, stop_a, target)

        lines += [
            f"▸ 今日 {chg_str} / 量比 {vol_ratio}x，追漲條件未完全到位（需漲 ≥4% + 量比 ≥1.5x）",
            f"▸ 若量比突然放大至 > 1.5x，可追：進場 {entry:.0f} / 停損今低 {stop_a} / R:R {rr_a}:1",
        ]

        if in_up:
            pb_entry = round(ma20 * 1.01)
            pb_stop  = round(ma20 * 0.95, 1)
            pb_rr    = _rr(pb_entry, pb_stop, target)
            lines.append(
                f"▸ 回測等待：縮量回 MA20（{ma20:.0f}）止跌K，進場 {pb_entry:.0f} / 停損 {pb_stop} / R:R {pb_rr}:1"
            )

        if rr_a >= 3:   stars = 4
        elif rr_a >= 2: stars = 3
        else:           stars = 2
        if not in_up:   stars -= 1
        stars  = max(1, min(5, stars))
        action = "量比不足，等爆量確認或回測 MA20 再進"

    elif is_pullback:
        near_ma20 = abs(close - ma20) / ma20 <= 0.05

        if near_ma20:
            pb_entry = round(close * 1.005)
            note     = f"現價貼近 MA20（{ma20:.0f}），等今明縮量止跌K（紅K或下影線）確認"
        else:
            dist_pct = round((close / ma20 - 1) * 100, 1)
            pb_entry = round(ma20 * 1.01)
            note     = f"距 MA20（{ma20:.0f}）還有 {dist_pct:.1f}%，等縮量回測再等止跌K"

        pb_stop = round(ma20 * 0.95, 1)
        pb_rr   = _rr(pb_entry, pb_stop, target)

        lines += [
            f"📉 回測整理（{chg_str} / 量比 {vol_ratio}x）",
            f"▸ {note}",
            f"▸ 進場：{pb_entry:.0f} 元 / 停損：{pb_stop} 元（MA20 下 5%）",
            f"▸ R:R：{pb_rr}:1　目標 TP1：{target:.0f}　TP2：{fib_tp2:.0f}",
        ]

        if pb_rr >= 4:      stars = 5
        elif pb_rr >= 3:    stars = 4
        elif pb_rr >= 2:    stars = 3
        else:               stars = 2
        if vol_ratio > 1.5: stars -= 1
        if ratio < 0.236:   stars -= 1
        stars = max(1, min(5, stars))

        if near_ma20:
            action = f"回測到位，等止跌K確認即可進場，R:R {pb_rr}:1"
        else:
            action = "回測中，耐心等 MA20 附近縮量確認"

    else:
        if in_up and close > ma20:
            dist_pct = round((close / ma20 - 1) * 100, 1)
            pb_entry = round(ma20 * 1.01)
            pb_stop  = round(ma20 * 0.95, 1)
            pb_rr    = _rr(pb_entry, pb_stop, target)
            lines += [
                f"▸ 均線多頭，現價在 MA20 上方 {dist_pct:.1f}%，無明確訊號",
                f"▸ 等強勢訊號（漲 ≥4% + 量比 ≥1.5x）追漲",
                f"▸ 或等回測 MA20（{ma20:.0f}）縮量止跌K：進場 {pb_entry:.0f} / 停損 {pb_stop} / R:R {pb_rr}:1",
            ]
            stars  = 3 if pb_rr >= 3 else 2
            action = "等追漲或回測訊號，勿無故追"
        else:
            lines += [
                f"▸ {trend}，結構偏弱，暫不適合追入",
                f"▸ 等均線轉為多頭排列（MA20 > MA60）再觀察",
            ]
            stars  = 1
            action = "結構偏弱，觀望"

    # ── 部位管理（三份制）──
    lines += [
        "",
        "【 部位管理 】",
        "▸ 第2份（攤平）：跌 -10% + 站回 MA20，加一份，停損不動",
        "▸ 第3份（追強）：獲利 +10% 且 MA5 > MA20，追加，停損上移至第2份成本",
        "▸ 全砍線：第1份成本 -15%，無條件出場，不猶豫",
    ]

    stars_str = "★" * max(1, min(5, stars)) + "☆" * (5 - max(1, min(5, stars)))
    lines += [
        "",
        "【 推薦指數 】",
        f"▸ {stars_str}",
        f"▸ {action}",
    ]

    return "\n".join(lines)
