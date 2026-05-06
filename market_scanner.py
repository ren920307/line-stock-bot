"""
全市場強勢股掃描（右側交易邏輯）
- 宇宙清單來自 universe.json（每日 09:00 更新）
- 報價 / 技術指標全部用 Fugle
- 15:25 開始，約 5 分鐘跑完，15:30 推播

兩組結果：
  強勢追價組 TOP5：今日動能強，明日等回測確認
  回測進場組 TOP5：近期強勢、今日縮量回測，位置接近費波/MA20
"""
import gc
import json
import os
import time
import pandas as pd
from datetime import date
from fugle_fetcher import fetch_history, fetch_quote

UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "universe.json")
SCAN_LOG_PATH = os.path.join(os.path.dirname(__file__), "scan_log.json")

# 強勢追價組門檻
MOMENTUM_MIN_CHG    = 4.0   # 漲幅 > 4%
MOMENTUM_MIN_VOL    = 2.5   # 量比 > 2.5x（用前20日不含今日）

# 回測進場組門檻
PULLBACK_MAX_CHG    = 2.0   # 今日漲幅 < 2%（正在回調）
PULLBACK_MIN_CHG    = -3.0  # 今日跌幅 > -3%（沒有崩跌）
PULLBACK_MAX_VOL    = 1.2   # 量比 < 1.2x（縮量）
PULLBACK_DAYS       = 5     # 近幾日內曾有強勢


def _load_universe() -> list:
    try:
        with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("stocks", [])
    except Exception:
        return []


def _calc_fib_position(close: float, low60: float, high60: float) -> str:
    """回傳現價在費波哪個區間（從低點到高點）"""
    rng = high60 - low60
    if rng <= 0:
        return ""
    ratio = (close - low60) / rng
    if ratio >= 1.0:
        return "突破前高"
    elif ratio >= 0.786:
        return "費波0.786以上"
    elif ratio >= 0.618:
        return "費波0.618～0.786"
    elif ratio >= 0.500:
        return "費波0.500～0.618"
    elif ratio >= 0.382:
        return "費波0.382～0.500"
    elif ratio >= 0.236:
        return "費波0.236～0.382"
    else:
        return "費波0.236以下"


def _enrich(s: dict, df: pd.DataFrame) -> dict:
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    last = df.iloc[-1]

    # 量比：用前20日不含今日
    hist_vol  = df["Volume"].iloc[-21:-1]
    avg_vol   = hist_vol.mean() if len(hist_vol) >= 5 else df["Volume"].tail(20).mean()
    vol_ratio = s["volume"] * 1000 / avg_vol if avg_vol > 0 else 0

    ma5  = float(last["MA5"])  if not pd.isna(last["MA5"])  else None
    ma20 = float(last["MA20"]) if not pd.isna(last["MA20"]) else None
    ma60 = float(last["MA60"]) if not pd.isna(last["MA60"]) else None

    high60 = float(df["High"].tail(60).max())
    low60  = float(df["Low"].tail(60).min())

    # 近5日最大單日漲幅（回測組用）
    recent5 = df.tail(6)
    max_recent_chg = 0.0
    for i in range(1, len(recent5)):
        prev_c = float(recent5.iloc[i-1]["Close"])
        curr_c = float(recent5.iloc[i]["Close"])
        if prev_c > 0:
            chg = (curr_c - prev_c) / prev_c * 100
            max_recent_chg = max(max_recent_chg, chg)

    # 現價距 MA20 的百分比
    dist_ma20 = round((s["close"] - ma20) / ma20 * 100, 1) if ma20 else None

    s["ma5"]            = round(ma5,  1) if ma5  else None
    s["ma20"]           = round(ma20, 1) if ma20 else None
    s["ma60"]           = round(ma60, 1) if ma60 else None
    s["vol_ratio"]      = round(vol_ratio, 1)
    s["high60"]         = round(high60, 1)
    s["low60"]          = round(low60, 1)
    s["fib_pos"]        = _calc_fib_position(s["close"], low60, high60)
    s["max_recent_chg"] = round(max_recent_chg, 1)
    s["dist_ma20"]      = dist_ma20
    return s


def _is_uptrend(s: dict) -> bool:
    """MA 多頭排列且現價站上 MA20"""
    return (
        s["ma5"] and s["ma20"] and s["ma60"] and
        s["ma5"] > s["ma20"] > s["ma60"] and
        s["close"] > s["ma20"]
    )


def _save_scan_log(momentum: list, pullback: list):
    try:
        try:
            with open(SCAN_LOG_PATH, "r", encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            log = {}

        today = date.today().isoformat()
        log[today] = {
            "momentum": [{"code": s["code"], "name": s["name"], "price": s["close"]} for s in momentum],
            "pullback": [{"code": s["code"], "name": s["name"], "price": s["close"]} for s in pullback],
        }

        keys = sorted(log.keys())[-30:]
        log = {k: log[k] for k in keys}

        with open(SCAN_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  ⚠️ scan_log 寫入失敗：{e}")


def run_daily_scan() -> str:
    universe = _load_universe()
    if not universe:
        return "宇宙清單為空，請確認 universe.json 是否已更新。"

    print(f"  掃描 {len(universe)} 檔...")

    momentum_passed = []  # 強勢追價組候選
    pullback_passed = []  # 回測進場組候選

    for s in universe:
        try:
            time.sleep(1)

            q = fetch_quote(s["code"])
            if not q or q.get("chg_pct") is None or q.get("close") is None:
                continue

            chg_pct = q["chg_pct"]
            close   = q["close"]
            volume  = (q.get("volume") or 0) // 1000

            # 抓歷史 K 線
            df = fetch_history(s["code"], days=70)
            if df.empty or len(df) < 25:
                del df
                continue

            stock = {
                "code": s["code"], "name": s["name"],
                "close": close, "chg_pct": chg_pct, "volume": volume,
            }
            stock = _enrich(stock, df)
            del df
            gc.collect()

            if not _is_uptrend(stock):
                continue

            # ── 強勢追價組 ──
            if (chg_pct >= MOMENTUM_MIN_CHG and
                    stock["vol_ratio"] >= MOMENTUM_MIN_VOL):
                # 距前高太近（< 3%）扣分，不排除但降權
                near_high = stock["close"] >= stock["high60"] * 0.97
                stock["near_high"] = near_high
                momentum_passed.append(stock)

            # ── 回測進場組 ──
            elif (PULLBACK_MIN_CHG <= chg_pct <= PULLBACK_MAX_CHG and
                    stock["vol_ratio"] <= PULLBACK_MAX_VOL and
                    stock["max_recent_chg"] >= 4.0 and
                    stock["dist_ma20"] is not None and
                    abs(stock["dist_ma20"]) <= 5.0):
                pullback_passed.append(stock)

        except Exception:
            continue

    # ── 強勢追價組排序 ──
    if momentum_passed:
        max_chg = max(s["chg_pct"] for s in momentum_passed)
        max_vol = max(s["vol_ratio"] for s in momentum_passed)
        for s in momentum_passed:
            s["score"] = (
                s["chg_pct"] / max_chg * 0.5 +
                s["vol_ratio"] / max_vol * 0.3 +
                (0.0 if s["near_high"] else 0.2)   # 距前高遠加分
            )
        momentum_passed.sort(key=lambda x: x["score"], reverse=True)

    # ── 回測進場組排序（距MA20越近越好）──
    if pullback_passed:
        pullback_passed.sort(key=lambda x: abs(x["dist_ma20"]))

    top_m = momentum_passed[:5]
    top_p = pullback_passed[:5]

    _save_scan_log(top_m, top_p)

    CIRCLE = "①②③④⑤⑥⑦⑧⑨⑩"
    today  = date.today().strftime("%m/%d")

    def fmt_momentum(i, s):
        high_tag = " ⚠近前高" if s["near_high"] else ""
        return (
            f"{CIRCLE[i-1]} {s['name']}({s['code']})"
            f" {s['close']:.0f}元 +{s['chg_pct']:.1f}%"
            f" 量比{s['vol_ratio']:.1f}x"
            f" {s['fib_pos']}{high_tag}"
        )

    def fmt_pullback(i, s):
        dist_str = f"MA20{'↑' if s['dist_ma20'] >= 0 else '↓'}{abs(s['dist_ma20']):.1f}%"
        return (
            f"{CIRCLE[i-1]} {s['name']}({s['code']})"
            f" {s['close']:.0f}元 {s['chg_pct']:+.1f}%"
            f" 量比{s['vol_ratio']:.1f}x"
            f" {dist_str} {s['fib_pos']}"
        )

    lines = [f"【強勢股掃描】{today}"]

    # 強勢追價組
    lines.append("")
    if top_m:
        lines.append(f"★ 強勢追價 TOP{len(top_m)}（今日動能強，明日等回測）★")
        lines.append("條件：漲>4% / 量比>2.5x / MA多頭")
        for i, s in enumerate(top_m, 1):
            lines.append(fmt_momentum(i, s))
    else:
        lines.append("★ 強勢追價：今日無符合標的")

    # 回測進場組
    lines.append("")
    if top_p:
        lines.append(f"★ 回測進場 TOP{len(top_p)}（縮量回測，位置佳）★")
        lines.append("條件：近5日曾漲>4% / 今日縮量 / 距MA20在5%內")
        for i, s in enumerate(top_p, 1):
            lines.append(fmt_pullback(i, s))
    else:
        lines.append("★ 回測進場：今日無符合標的")

    # 操作提示
    lines.append(
        "\n【操作提示】\n"
        "① 追價組：明日回測今日收盤不破才進，停損跌破今日低點\n"
        "② 回測組：今日或明日止跌K確認（紅K或下影線）才進\n"
        "③ 兩組停損均設支撐下方，R:R用TP2計算需≥1:3\n"
        "④ 首倉小，確認後倒金字塔加碼"
    )

    return "\n".join(lines)
