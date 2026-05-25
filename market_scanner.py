"""
全市場強勢股掃描（右側交易邏輯）
- 宇宙清單來自 universe.json（每日 09:00 更新）
- 報價 / 技術指標全部用 Fugle
- 15:25 開始，約 5 分鐘跑完，15:30 推播

兩組結果：
  強勢追價組 TOP5：今日動能強，明日等回測確認
  回測進場組 TOP5：近期強勢、今日縮量回測，位置接近費波/MA20
"""
import base64
import gc
import json
import os
import time
import pandas as pd
import requests
from datetime import date
from fugle_fetcher import fetch_history, fetch_quote, FugleRateLimitError

UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "universe.json")
SCAN_LOG_PATH = os.path.join(os.path.dirname(__file__), "scan_log.json")

# GitHub 持久化（避免 Render 重啟丟失 scan_log）
GITHUB_REPO  = "ren920307/line-stock-bot"
GITHUB_FILE  = "scan_log.json"


def _push_scan_log_to_github(content: str):
    """把 scan_log 推回 repo，重啟也不會丟。commit 訊息加 [skip render] 避免觸發部署。"""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("  ⚠️ GITHUB_TOKEN 未設定，scan_log 只存本地（重啟會丟）")
        return
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        # 取得當前檔案 sha（更新時必填）
        r = requests.get(url, headers=headers, timeout=10)
        sha = r.json().get("sha") if r.status_code == 200 else None

        body = {
            "message": f"chore: update scan_log {date.today().isoformat()} [skip render]",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(url, headers=headers, json=body, timeout=15)
        if r.status_code in (200, 201):
            print(f"  ✅ scan_log 已推回 GitHub")
        else:
            print(f"  ⚠️ GitHub push 失敗 {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  ⚠️ scan_log push 例外：{e}")


def _pull_scan_log_from_github() -> bool:
    """啟動時從 GitHub raw 拉一次，確保本地有最新資料。回傳是否成功。"""
    try:
        url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{GITHUB_FILE}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(SCAN_LOG_PATH, "w", encoding="utf-8") as f:
                f.write(r.text)
            return True
    except Exception as e:
        print(f"  ⚠️ scan_log pull 例外：{e}")
    return False

# 強勢追價組門檻
MOMENTUM_MIN_CHG    = 4.0   # 漲幅 > 4%
MOMENTUM_MIN_VOL    = 2.0   # 量比 > 2.0x（放寬，原 2.5x）

# 回測進場組門檻
PULLBACK_MAX_CHG    = 3.0   # 今日漲幅 < 3%（放寬，原 2%）
PULLBACK_MIN_CHG    = -3.0  # 今日跌幅 > -3%（沒有崩跌）
PULLBACK_MAX_VOL    = 1.8   # 量比 < 1.8x（放寬，原 1.2x）
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
    # df["Volume"] 是 Fugle 歷史 API 的張數，s["volume"] 也已換算為張
    hist_vol  = df["Volume"].iloc[-21:-1]
    avg_vol   = hist_vol.mean() if len(hist_vol) >= 5 else df["Volume"].tail(20).mean()
    vol_ratio = s["volume"] / avg_vol if avg_vol > 0 else 0

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
    """中長期多頭：MA20 > MA60 且現價站上 MA20（MA5 不強制，震盪日容錯）"""
    return (
        s["ma20"] and s["ma60"] and
        s["ma20"] > s["ma60"] and
        s["close"] > s["ma20"]
    )


CIRCLE = "①②③④⑤⑥⑦⑧⑨⑩"


def _fmt_momentum(i: int, s: dict) -> str:
    chg_str  = f"+{s['chg_pct']:.1f}%"
    high_tag = "⚠️ 近前高，留意壓力" if s["near_high"] else "✅ 強勢突破"
    ma20 = int(s['ma20']) if s['ma20'] else "-"
    ma60 = int(s['ma60']) if s['ma60'] else "-"
    return (
        f"{CIRCLE[i-1]} {s['name']} ({s['code']})\n"
        f"現價 {s['close']:.0f} 元（{chg_str}）/ 量比 {s['vol_ratio']:.1f}x\n"
        f"MA20 {ma20} / MA60 {ma60} / {s['fib_pos']}\n"
        f"{high_tag}"
    )


def _fmt_pullback(i: int, s: dict) -> str:
    if s['chg_pct'] > 0:
        chg_str = f"+{s['chg_pct']:.1f}%"
    elif s['chg_pct'] < 0:
        chg_str = f"{s['chg_pct']:.1f}%"
    else:
        chg_str = "0.0%"
    ma_dir = "站上" if s['dist_ma20'] >= 0 else "跌破"
    ma20 = int(s['ma20']) if s['ma20'] else "-"
    ma60 = int(s['ma60']) if s['ma60'] else "-"
    return (
        f"{CIRCLE[i-1]} {s['name']} ({s['code']})\n"
        f"現價 {s['close']:.0f} 元（{chg_str}）/ 量比 {s['vol_ratio']:.1f}x\n"
        f"MA20 {ma20}（{ma_dir} {abs(s['dist_ma20']):.1f}%）/ MA60 {ma60}"
    )


def _fmt_scan_result(
    title: str, today: str,
    top_m: list, top_p: list,
    cnt_total: int, cnt_quote_ok: int, cnt_hist_ok: int,
    cnt_uptrend: int, cnt_momentum: int, cnt_pullback: int,
) -> str:
    lines = [f"【{title}】 {today}"]

    lines.append("")
    if top_m:
        lines.append(f"🔥 強勢追價 TOP {len(top_m)}")
        lines.append("條件：漲幅 > 4% / 量比 > 2.0x / MA 多頭")
        lines.append("策略：今日動能強，明日等回測")
        for i, s in enumerate(top_m, 1):
            lines.append("")
            lines.append(_fmt_momentum(i, s))
    else:
        lines.append("🔥 強勢追價：今日無符合標的")

    lines.append("")
    if top_p:
        fib_summary = top_p[0]['fib_pos'] if len(set(s['fib_pos'] for s in top_p)) == 1 else "費波中段"
        lines.append(f"📉 回測進場 TOP {len(top_p)}")
        lines.append(f"條件：近 5 日曾漲 > 3.5% / 今日縮量 / 距 MA20 在 8% 內 / {fib_summary}")
        lines.append("策略：縮量回測，確認止跌 K 再進")
        for i, s in enumerate(top_p, 1):
            lines.append("")
            lines.append(_fmt_pullback(i, s))
    else:
        lines.append("📉 回測進場：今日無符合標的")

    lines.append(
        "\n💡 操作提示\n"
        "追價組：明日回測不破今日低才進，停損設今日低點下方。\n"
        "回測組：確認止跌 K（紅 K / 下影線）再進，R:R ≥ 1:3。"
    )
    lines.append(
        f"\n📊 掃描診斷\n"
        f"總計 {cnt_total} 檔 / 報價 K 線正常 {min(cnt_quote_ok, cnt_hist_ok)} 檔"
        f" / MA 多頭 {cnt_uptrend} 檔"
        f" / 追價過關 {cnt_momentum} 檔"
        f" / 回測過關 {cnt_pullback} 檔"
    )
    return "\n".join(lines)


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

        content = json.dumps(log, ensure_ascii=False, indent=2)
        with open(SCAN_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)

        # 推回 GitHub，避免 Render 重啟丟資料
        _push_scan_log_to_github(content)
    except Exception as e:
        print(f"  ⚠️ scan_log 寫入失敗：{e}")


def run_daily_scan() -> str:
    if not os.environ.get("FUGLE_API_KEY"):
        return "❌ 掃描失敗：FUGLE_API_KEY 未設定，請確認 Render 環境變數。"

    universe = _load_universe()
    if not universe:
        return "宇宙清單為空，請確認 universe.json 是否已更新。"

    print(f"  掃描 {len(universe)} 檔...")

    momentum_passed = []  # 強勢追價組候選
    pullback_passed = []  # 回測進場組候選

    cnt_total     = len(universe)
    cnt_quote_ok  = 0   # 報價正常
    cnt_hist_ok   = 0   # 歷史K線正常
    cnt_uptrend   = 0   # 通過多頭排列
    cnt_momentum  = 0   # 通過追價條件
    cnt_pullback  = 0   # 通過回測條件

    try:
      for s in universe:
        try:
            time.sleep(1)

            q = fetch_quote(s["code"])
            if not q or q.get("chg_pct") is None or q.get("close") is None:
                continue
            cnt_quote_ok += 1

            chg_pct = q["chg_pct"]
            close   = q["close"]
            volume  = q.get("volume") or 0   # 單位：股（與歷史K線一致）

            time.sleep(1)  # quote 和 history 之間加間隔，避免超過 Fugle 60/min 上限

            # 抓歷史 K 線
            df = fetch_history(s["code"], days=70)
            if df.empty or len(df) < 25:
                del df
                continue
            cnt_hist_ok += 1

            # intraday volume 盤後可能為 0，fallback 用歷史最後一根
            if volume == 0 and not df.empty:
                volume = int(df["Volume"].iloc[-1])

            stock = {
                "code": s["code"], "name": s["name"],
                "close": close, "chg_pct": chg_pct, "volume": volume,
            }
            stock = _enrich(stock, df)
            del df
            gc.collect()

            if not _is_uptrend(stock):
                continue
            cnt_uptrend += 1

            # ── 強勢追價組 ──
            if (chg_pct >= MOMENTUM_MIN_CHG and
                    stock["vol_ratio"] >= MOMENTUM_MIN_VOL):
                # 距前高太近（< 3%）扣分，不排除但降權
                near_high = stock["close"] >= stock["high60"] * 0.97
                stock["near_high"] = near_high
                momentum_passed.append(stock)
                cnt_momentum += 1

            # ── 回測進場組 ──
            elif (PULLBACK_MIN_CHG <= chg_pct <= PULLBACK_MAX_CHG and
                    stock["vol_ratio"] <= PULLBACK_MAX_VOL and
                    stock["max_recent_chg"] >= 3.5 and
                    stock["dist_ma20"] is not None and
                    abs(stock["dist_ma20"]) <= 8.0):
                pullback_passed.append(stock)
                cnt_pullback += 1

        except FugleRateLimitError:
            raise
        except Exception:
            continue

    except FugleRateLimitError as e:
        print(f"  ⛔ 觸發 Fugle rate limit，掃描中止：{e}")
        return (
            f"【強勢股掃描】⛔ API 速率超限\n"
            f"Fugle 免費方案 60次/分鐘已用盡，掃描中止。\n"
            f"原因：同時有兩個掃描執行（已加鎖修復，明天應正常）。\n"
            f"已掃描 {cnt_quote_ok} 檔 / 共 {cnt_total} 檔。"
        )

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

    today = date.today().strftime("%m/%d")
    return _fmt_scan_result(
        "強勢股掃描", today,
        top_m, top_p,
        cnt_total, cnt_quote_ok, cnt_hist_ok,
        cnt_uptrend, cnt_momentum, cnt_pullback,
    )


WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")


def scan_quality_pool() -> str:
    """對 quality_pool（186檔優質股）跑同樣的強弱掃描，約5分鐘跑完。"""
    if not os.environ.get("FUGLE_API_KEY"):
        return "❌ 掃描失敗：FUGLE_API_KEY 未設定。"

    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        pool = json.load(f).get("quality_pool", [])

    if not pool:
        return "質量池為空，請先建立 quality_pool。"

    print(f"[quality_scan] 開始掃描質量池 {len(pool)} 檔...")

    momentum_passed, pullback_passed = [], []
    cnt_total = len(pool)
    cnt_quote_ok = cnt_hist_ok = cnt_uptrend = cnt_momentum = cnt_pullback = 0

    try:
        for s in pool:
            time.sleep(1)
            q = fetch_quote(s["code"])
            if not q or q.get("chg_pct") is None or q.get("close") is None:
                continue
            cnt_quote_ok += 1

            chg_pct = q["chg_pct"]
            close   = q["close"]
            volume  = q.get("volume") or 0

            time.sleep(1)
            df = fetch_history(s["code"], days=70)
            if df.empty or len(df) < 25:
                del df
                continue
            cnt_hist_ok += 1

            if volume == 0 and not df.empty:
                volume = int(df["Volume"].iloc[-1])

            stock = {"code": s["code"], "name": s["name"],
                     "close": close, "chg_pct": chg_pct, "volume": volume}
            stock = _enrich(stock, df)
            del df
            gc.collect()

            if not _is_uptrend(stock):
                continue
            cnt_uptrend += 1

            if (chg_pct >= MOMENTUM_MIN_CHG and
                    stock["vol_ratio"] >= MOMENTUM_MIN_VOL):
                stock["near_high"] = stock["close"] >= stock["high60"] * 0.97
                momentum_passed.append(stock)
                cnt_momentum += 1
            elif (PULLBACK_MIN_CHG <= chg_pct <= PULLBACK_MAX_CHG and
                    stock["vol_ratio"] <= PULLBACK_MAX_VOL and
                    stock["max_recent_chg"] >= 3.5 and
                    stock["dist_ma20"] is not None and
                    abs(stock["dist_ma20"]) <= 8.0):
                pullback_passed.append(stock)
                cnt_pullback += 1

    except FugleRateLimitError:
        return (
            f"[質量池掃描] ⛔ 速率超限，已掃 {cnt_quote_ok} 檔 / 共 {cnt_total} 檔。\n"
            f"追價 {cnt_momentum} 檔 / 回測 {cnt_pullback} 檔（部分結果）"
        )

    # 排序
    if momentum_passed:
        max_chg = max(s["chg_pct"] for s in momentum_passed)
        max_vol = max(s["vol_ratio"] for s in momentum_passed)
        for s in momentum_passed:
            s["score"] = (s["chg_pct"] / max_chg * 0.5 +
                          s["vol_ratio"] / max_vol * 0.3 +
                          (0.0 if s["near_high"] else 0.2))
        momentum_passed.sort(key=lambda x: x["score"], reverse=True)
    if pullback_passed:
        pullback_passed.sort(key=lambda x: abs(x["dist_ma20"]))

    top_m = momentum_passed[:5]
    top_p = pullback_passed[:5]

    _save_scan_log(top_m, top_p)

    today = date.today().strftime("%m/%d")
    return _fmt_scan_result(
        "質量池掃描", today,
        top_m, top_p,
        cnt_total, cnt_quote_ok, cnt_hist_ok,
        cnt_uptrend, cnt_momentum, cnt_pullback,
    )
