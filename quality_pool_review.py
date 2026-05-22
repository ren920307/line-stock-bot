"""
quality_pool 月度汰弱留強審查
每月1日跑，對 quality_pool 全部股票打相對強弱分數，
低分股透過 LINE 推播建議汰除清單。

評分邏輯（共100分）：
  40分 — 近60日漲幅 vs 0050（相對強弱）
  30分 — 是否站上60MA
  30分 — 近20日均量 vs 前20日（量能趨勢）

門檻：< 40分 → 列入建議汰除
"""
import json
import os
import time
from datetime import date

import requests

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")
BENCHMARK_CODE = "0050"
WEAK_THRESHOLD = 40
HISTORY_DAYS   = 90   # 抓90天，確保有足夠K棒算60MA


# ── Fugle / LINE 環境變數 ─────────────────────────────
def _fugle_headers():
    return {"X-API-KEY": os.environ.get("FUGLE_API_KEY", "")}

LINE_TOKEN   = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
MY_USER_ID   = os.environ.get("LINE_USER_ID", "")


# ── 資料抓取 ─────────────────────────────────────────
def _fetch_history(code: str):
    """回傳 close/volume list（時間升序），最多 HISTORY_DAYS 根。"""
    from datetime import timedelta
    end   = date.today()
    start = end - timedelta(days=int(HISTORY_DAYS * 1.8))
    url   = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{code}"
    try:
        r = requests.get(url, headers=_fugle_headers(), params={
            "from": start.isoformat(), "to": end.isoformat(),
        }, timeout=10)
        if r.status_code == 429:
            raise RuntimeError("rate_limit")
        candles = r.json().get("data", {})
        if isinstance(candles, dict):
            candles = candles.get("candles", [])
        if not candles:
            candles = r.json().get("candles", [])
        candles = sorted(candles, key=lambda c: c["date"])[-HISTORY_DAYS:]
        closes  = [c["close"]  for c in candles]
        volumes = [c["volume"] for c in candles]
        return closes, volumes
    except RuntimeError:
        raise
    except Exception:
        return [], []


# ── 評分 ─────────────────────────────────────────────
def _score(closes, volumes, bench_ret60: float) -> dict | None:
    if len(closes) < 65:
        return None

    score = 0

    # 1. 相對強弱 vs 大盤（40分）
    ret60 = (closes[-1] / closes[-60] - 1) * 100
    rs    = ret60 - bench_ret60
    if rs > 10:
        score += 40
    elif rs > 0:
        score += 25
    elif rs > -10:
        score += 10

    # 2. 站上60MA（30分）
    ma60 = sum(closes[-60:]) / 60
    above_ma60 = closes[-1] > ma60
    if above_ma60:
        score += 30

    # 3. 量能趨勢（30分）：近20日均量 vs 前20日均量
    vol_recent = sum(volumes[-20:]) / 20
    vol_prev   = sum(volumes[-40:-20]) / 20 if len(volumes) >= 40 else vol_recent
    vol_ratio  = vol_recent / vol_prev if vol_prev > 0 else 1.0
    if vol_ratio > 1.2:
        score += 30
    elif vol_ratio > 0.8:
        score += 15

    return {
        "score":      score,
        "ret60":      round(ret60, 1),
        "rs":         round(rs, 1),
        "above_ma60": above_ma60,
        "vol_ratio":  round(vol_ratio, 2),
    }


# ── 主流程 ───────────────────────────────────────────
def run_review(dry_run: bool = False) -> list[dict]:
    """跑完整審查，回傳所有結果（含分數）。dry_run=True 只計算不推播。"""
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)

    pool = data.get("quality_pool", [])
    print(f"[quality_review] 開始審查 {len(pool)} 檔，基準 {BENCHMARK_CODE}")

    # 抓大盤基準
    b_closes, _ = _fetch_history(BENCHMARK_CODE)
    bench_ret60  = (b_closes[-1] / b_closes[-60] - 1) * 100 if len(b_closes) >= 60 else 0.0
    print(f"[quality_review] 大盤近60日漲幅 {bench_ret60:+.1f}%")

    results = []
    for i, stock in enumerate(pool):
        code, name = stock["code"], stock["name"]
        try:
            closes, volumes = _fetch_history(code)
            detail = _score(closes, volumes, bench_ret60)
            if detail:
                results.append({"code": code, "name": name, **detail})
                print(f"  {code} {name:8s} 分數={detail['score']:3d}  RS={detail['rs']:+5.1f}%  MA60={'O' if detail['above_ma60'] else 'X'}  量比={detail['vol_ratio']:.2f}")
        except RuntimeError:
            print(f"  ⚠️ {code} rate limit，等2s")
            time.sleep(2)
        except Exception as e:
            print(f"  跳過 {code}: {e}")

        # Fugle 免費方案 60req/min，每3檔休息1秒
        if (i + 1) % 3 == 0:
            time.sleep(1.1)

    results.sort(key=lambda x: x["score"])
    weak = [r for r in results if r["score"] < WEAK_THRESHOLD]

    print(f"\n[quality_review] 完成。共 {len(results)} 檔有效，{len(weak)} 檔低分（<{WEAK_THRESHOLD}分）")

    if not dry_run:
        _push_report(results, weak)

    return results


def _push_report(results: list[dict], weak: list[dict]):
    """推播審查結果到 LINE。"""
    today = date.today().isoformat()
    total = len(results)

    if not weak:
        msg = f"[質量池月審 {today}]\n共 {total} 檔，全數通過，無建議汰除。"
    else:
        lines = [f"[質量池月審 {today}]"]
        lines.append(f"共 {total} 檔，建議汰除 {len(weak)} 檔：\n")
        for r in weak:
            ma_tag = "站上" if r["above_ma60"] else "破下"
            lines.append(
                f"❌ {r['code']} {r['name']}  {r['score']}分\n"
                f"   RS={r['rs']:+.1f}%｜60MA:{ma_tag}｜量比:{r['vol_ratio']}"
            )
        lines.append("\n如確認汰除，請傳「#汰弱確認」")
        msg = "\n".join(lines)

    _line_push(msg)

    # 同時存一份待確認名單到 watchlist.json
    _save_pending_removal(weak)


def _save_pending_removal(weak: list[dict]):
    """把建議汰除清單寫進 watchlist.json 的 pending_removal 欄位。"""
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data["pending_removal"] = [
        {"code": r["code"], "name": r["name"], "score": r["score"], "reviewed": date.today().isoformat()}
        for r in weak
    ]
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[quality_review] pending_removal 已存檔（{len(weak)} 檔）")


def confirm_removal():
    """收到 #汰弱確認 時呼叫，從 quality_pool 移除 pending_removal 名單。"""
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)

    pending = {r["code"] for r in data.get("pending_removal", [])}
    if not pending:
        return "目前沒有待確認的汰除名單。"

    before = len(data["quality_pool"])
    data["quality_pool"] = [s for s in data["quality_pool"] if s["code"] not in pending]
    removed = before - len(data["quality_pool"])

    data.pop("pending_removal", None)

    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return f"已從質量池移除 {removed} 檔，剩餘 {len(data['quality_pool'])} 檔。"


def _line_push(text: str):
    if not LINE_TOKEN or not MY_USER_ID:
        print("[LINE] 未設定 token/user_id，僅印出")
        print(text)
        return
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
            json={"to": MY_USER_ID, "messages": [{"type": "text", "text": text[:4900]}]},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[LINE] push 失敗 {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[LINE] push 例外: {e}")


if __name__ == "__main__":
    import sys
    dry = "--dry" in sys.argv
    run_review(dry_run=dry)
