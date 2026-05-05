"""
全市場強勢股掃描（右側交易邏輯）
- 宇宙清單來自 universe.json（每日 09:00 更新）
- 報價 / 技術指標全部用 Fugle（不碰 TWSE 報價）
- 15:25 開始，約 5 分鐘跑完 300 檔，15:30 推播
篩選條件：漲幅 > 4%、量比 > 2.5x、MA5 > MA20 > MA60
"""
import gc
import json
import os
import time
import pandas as pd
from datetime import date
from fugle_fetcher import fetch_history, fetch_quote

UNIVERSE_PATH  = os.path.join(os.path.dirname(__file__), "universe.json")
SCAN_LOG_PATH  = os.path.join(os.path.dirname(__file__), "scan_log.json")

MIN_CHANGE_PCT   = 4.0
MIN_VOL_RATIO    = 2.5
MA_SPREAD_THRESH = 0.08


def _load_universe() -> list:
    try:
        with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("stocks", [])
    except Exception:
        return []


def _enrich(s: dict, df: pd.DataFrame) -> dict:
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    last = df.iloc[-1]

    avg_vol   = df["Volume"].tail(20).mean()
    vol_ratio = s["volume"] * 1000 / avg_vol if avg_vol > 0 else 0

    ma5  = float(last["MA5"])  if not pd.isna(last["MA5"])  else None
    ma20 = float(last["MA20"]) if not pd.isna(last["MA20"]) else None
    ma60 = float(last["MA60"]) if not pd.isna(last["MA60"]) else None

    converged = False
    if ma5 and ma20 and ma60 and min(ma5, ma20, ma60) > 0:
        spread = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / min(ma5, ma20, ma60)
        converged = spread < MA_SPREAD_THRESH

    s["ma5"]       = round(ma5,  1) if ma5  else None
    s["ma20"]      = round(ma20, 1) if ma20 else None
    s["ma60"]      = round(ma60, 1) if ma60 else None
    s["vol_ratio"] = round(vol_ratio, 1)
    s["converged"] = converged
    return s


def _save_scan_log(top5: list):
    try:
        try:
            with open(SCAN_LOG_PATH, "r", encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            log = {}

        today = date.today().isoformat()
        log[today] = [
            {"code": s["code"], "name": s["name"], "entry_price": s["close"]}
            for s in top5
        ]

        # 只保留最近 30 天
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

    # 逐一用 Fugle 抓報價 + 技術指標（每秒 1 檔）
    passed = []
    for s in universe:
        try:
            time.sleep(1)

            q = fetch_quote(s["code"])
            if not q or q.get("chg_pct") is None or q.get("close") is None:
                continue

            chg_pct = q["chg_pct"]
            close   = q["close"]
            volume  = (q.get("volume") or 0) // 1000  # 張

            # 漲幅初篩
            if chg_pct < MIN_CHANGE_PCT:
                continue

            # 抓歷史 K 線補技術指標
            df = fetch_history(s["code"], days=65)
            if df.empty or len(df) < 20:
                del df
                continue

            stock = {
                "code": s["code"], "name": s["name"],
                "close": close, "chg_pct": chg_pct, "volume": volume,
            }
            stock = _enrich(stock, df)
            del df
            gc.collect()

            if not (stock["ma5"] and stock["ma20"] and stock["ma60"]):
                continue
            if not (stock["ma5"] > stock["ma20"] > stock["ma60"]):
                continue
            if stock["close"] <= stock["ma20"]:
                continue
            if stock["vol_ratio"] < MIN_VOL_RATIO:
                continue

            passed.append(stock)

        except Exception:
            continue

    if not passed:
        return "今日無符合條件的強勢股。"

    # 加權分數排序
    max_chg = max(s["chg_pct"] for s in passed)
    max_vol = max(s["vol_ratio"] for s in passed)
    for s in passed:
        s["score"] = (
            (0.2 if s["converged"] else 0) +
            s["chg_pct"] / max_chg * 0.5 +
            s["vol_ratio"] / max_vol * 0.3
        )
    passed.sort(key=lambda x: x["score"], reverse=True)

    top5  = passed[:5]
    top10 = passed[:10]

    # 記錄進榜價
    _save_scan_log(top5)

    # 組推播訊息
    today = date.today().strftime("%m/%d")
    lines = [
        f"📈 強勢股掃描｜{today}",
        f"條件：多頭排列 / 漲>4% / 量比>2.5x",
        f"共 {len(passed)} 檔通過",
        "",
        "★ TOP 5 明日重點 ★",
    ]
    for i, s in enumerate(top5, 1):
        tag = " 糾結↑" if s["converged"] else ""
        lines.append(
            f"  {i}. {s['name']}（{s['code']}）"
            f" {s['close']:.0f} +{s['chg_pct']:.1f}%"
            f" 量比{s['vol_ratio']:.1f}x{tag}"
        )

    lines.append("\n【TOP 10 觀察名單】")
    for i, s in enumerate(top10, 1):
        tag = " 糾結↑" if s["converged"] else ""
        lines.append(
            f"{i:2}. {s['name']}（{s['code']}）"
            f" +{s['chg_pct']:.1f}%"
            f" 量比{s['vol_ratio']:.1f}x{tag}"
        )

    lines.append(
        "\n【右側操作攻略】\n"
        "① 不追今日強勢，等明日回測\n"
        "② 回測今日收盤附近不破 → 進場\n"
        "③ 停損：跌破今日最低收盤\n"
        "④ 首倉小，撐穩再加碼（倒金字塔）\n"
        "⑤ 每次加碼停損上移"
    )

    return "\n".join(lines)
