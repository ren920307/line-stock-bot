import os, hashlib, hmac, base64, json
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import requests
from analyzer import analyze
from twse_fetcher import fetch
from claude_analyzer import deep_analyze
from stock_names import resolve, get_name
from commands import cmd_market, cmd_holdings, cmd_scan
from market_scanner import run_daily_scan
from universe_builder import build_universe
from weekly_report import build_weekly_report
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

LINE_TOKEN   = os.environ["LINE_TOKEN"]
LINE_SECRET  = os.environ.get("LINE_SECRET", "")
MY_USER_ID   = os.environ["LINE_USER_ID"]
GROUP_ID_FILE = "group_ids.json"

scheduler = BackgroundScheduler(timezone=pytz.timezone("Asia/Taipei"))

def _job_update_universe():
    try:
        build_universe()
    except Exception as e:
        print(f"[universe] 更新失敗：{e}")

def _job_daily_scan():
    try:
        push("⏳ 掃描開始...")
        result = run_daily_scan()
        push(result)
    except Exception as e:
        import traceback
        push(f"❌ 掃描失敗：{e}\n{traceback.format_exc()[-300:]}")

def _job_weekly_report():
    try:
        result = build_weekly_report()
        push(result)
    except Exception as e:
        push(f"❌ 週報失敗：{e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    tz = pytz.timezone("Asia/Taipei")
    # 每天 09:00 更新宇宙清單（週一到週五）
    scheduler.add_job(_job_update_universe, CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone=tz))
    # 每天 15:25 開始掃描（約 5 分鐘後推播）
    scheduler.add_job(_job_daily_scan, CronTrigger(hour=15, minute=25, day_of_week="mon-fri", timezone=tz))
    # 每週五 15:35 推週績效
    scheduler.add_job(_job_weekly_report, CronTrigger(hour=15, minute=35, day_of_week="fri", timezone=tz))
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)


# ── 群組 ID 管理 ──────────────────────────────────────
def _load_groups() -> list:
    try:
        with open(GROUP_ID_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def _save_groups(ids: list):
    with open(GROUP_ID_FILE, "w") as f:
        json.dump(ids, f)

def _register_group(gid: str):
    ids = _load_groups()
    if gid not in ids:
        ids.append(gid)
        _save_groups(ids)


# ── 推播函式 ─────────────────────────────────────────
def push(text: str, target_id: str = None):
    """推播給指定 ID（預設推給你）"""
    tid = target_id or MY_USER_ID
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": tid, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )

def reply(token: str, text: str):
    """回覆給發訊息的來源（個人或群組）"""
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"replyToken": token, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )

def push_all_groups(text: str):
    """推播給你 + 所有已登記群組"""
    push(text, MY_USER_ID)
    for gid in _load_groups():
        push(text, gid)


# ── 工具函式 ─────────────────────────────────────────
def verify_signature(body: bytes, sig: str) -> bool:
    if not LINE_SECRET:
        return True
    digest = hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), sig)


def build_price_summary(code: str) -> str:
    from fugle_fetcher import fetch_quote
    df = fetch(code)
    if df.empty:
        return "無法取得資料"
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    last = df.iloc[-1]
    prev = df.iloc[-2]
    high60 = df["High"].tail(60).max()
    low60  = df["Low"].tail(60).min()
    recent = df.tail(10)[["Open","High","Low","Close","Volume"]].round(1).to_string()

    q = fetch_quote(code)
    if q and q.get("close"):
        close      = float(q["close"])
        open_      = float(q.get("open") or last["Open"])
        high       = float(q["high"]) if q.get("high") else float(last["High"])
        low        = float(q["low"])  if q.get("low")  else float(last["Low"])
        vol        = int(q["volume"]) if q.get("volume") else int(last["Volume"])
        prev_close = float(q["prev"]) if q.get("prev") else float(prev["Close"])
    else:
        close      = float(last["Close"])
        open_      = float(last["Open"])
        high       = float(last["High"])
        low        = float(last["Low"])
        vol        = int(last["Volume"])
        prev_close = float(prev["Close"])

    chg_pct   = (close - prev_close) / prev_close * 100
    is_limit  = chg_pct >= 9.5
    chg_label = f"漲停 +{chg_pct:.1f}%\n關聖帝君已經給我九個聖杯" if is_limit else f"{chg_pct:+.1f}%"

    # 費波計算：60日明顯低點 → 60日高點
    swing_low  = float(df["Low"].tail(60).min())
    swing_high = float(df["High"].tail(60).max())
    rng = swing_high - swing_low
    fib = {
        "low":   swing_low,
        "high":  swing_high,
        "0.236": round(swing_high - rng * 0.236, 1),
        "0.382": round(swing_high - rng * 0.382, 1),
        "0.500": round(swing_high - rng * 0.500, 1),
        "0.618": round(swing_high - rng * 0.618, 1),
        "0.786": round(swing_high - rng * 0.786, 1),
        "tp1":   round(swing_low  + rng * 1.272, 1),
        "tp2":   round(swing_low  + rng * 1.618, 1),
    }
    fib_block = (
        f"\n【費波那契（{swing_low:.1f}→{swing_high:.1f}）】\n"
        f"回測 0.236：{fib['0.236']}　0.382：{fib['0.382']}　0.500：{fib['0.500']}\n"
        f"回測 0.618：{fib['0.618']}　0.786：{fib['0.786']}\n"
        f"TP1(1.272)：{fib['tp1']}　TP2(1.618)：{fib['tp2']}"
    )

    return (
        f"【今日數據】\n"
        f"收：{close:.0f}（{chg_label}）\n"
        f"開：{open_:.1f}　高：{high:.1f}　低：{low:.1f}\n"
        f"量：{vol//1000:,}張\n"
        f"MA5：{last['MA5']:.1f}　MA20：{last['MA20']:.1f}　MA60：{last['MA60']:.1f}\n"
        f"60日高：{high60:.0f}　低：{low60:.0f}\n"
        f"{fib_block}\n"
        f"\n近10日K線：\n{recent}"
    )


def scan_top15_msg() -> str:
    """給群組看的掃描結果（只有 TOP15+TOP5，不含損益）"""
    try:
        with open("watchlist.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        watchlist = data.get("watchlist", [])
        last_scan = data.get("last_scan", "")
        if not watchlist:
            return "尚無掃描資料，請稍後再試。"
        from datetime import date
        scan_date = last_scan or date.today().isoformat()
        lines = [f"【全市場強勢掃描】{scan_date}",
                 f"篩選條件：站上MA20 / 漲>2% / 量比>1.2x", ""]
        for i, s in enumerate(watchlist[:15], 1):
            chg = f"+{s['chg_pct']:.1f}%" if s.get("chg_pct") else ""
            lines.append(f"{i:2}. {s['name']}（{s['code']}）{chg}")
        lines.append("\n右側策略：等回測不破再進，停損前低。")
        return "\n".join(lines)
    except Exception:
        return "掃描資料讀取失敗。"


# ── 路由 ─────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/update-universe")
def update_universe():
    """手動觸發宇宙清單更新"""
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "universe.json")
    file_exists = _os.path.exists(path)
    file_size = _os.path.getsize(path) if file_exists else 0
    count = build_universe()
    return {"status": "ok", "count": count, "file_exists": file_exists, "file_size": file_size}


@app.get("/daily-scan")
def daily_scan():
    """手動觸發每日掃描（APScheduler 背景執行，不受 Render worker 回收影響）"""
    scheduler.add_job(_job_daily_scan, 'date', run_date=datetime.now(), id='manual_scan', replace_existing=True)
    return {"status": "ok", "message": "掃描已啟動，約 5 分鐘後推播到 LINE"}


@app.get("/test-scan")
def test_scan():
    """測試用：只跑前20檔，同步回傳結果"""
    from market_scanner import _load_universe, _enrich, _is_uptrend, MOMENTUM_MIN_CHG, MOMENTUM_MIN_VOL, PULLBACK_MIN_CHG, PULLBACK_MAX_CHG, PULLBACK_MAX_VOL
    from fugle_fetcher import fetch_history, fetch_quote
    import time, gc
    universe = _load_universe()[:20]
    lines = [f"測試掃描（前20檔）"]
    cnt_quote_ok = cnt_hist_ok = cnt_uptrend = 0
    for s in universe:
        try:
            time.sleep(0.5)
            q = fetch_quote(s["code"])
            if not q or q.get("chg_pct") is None or q.get("close") is None:
                continue
            cnt_quote_ok += 1
            df = fetch_history(s["code"], days=70)
            if df.empty or len(df) < 25:
                del df
                continue
            cnt_hist_ok += 1
            volume = q.get("volume") or 0
            if volume == 0 and not df.empty:
                volume = int(df["Volume"].iloc[-1])
            stock = {"code": s["code"], "name": s["name"], "close": q["close"], "chg_pct": q["chg_pct"], "volume": volume}
            stock = _enrich(stock, df)
            del df; gc.collect()
            uptrend = _is_uptrend(stock)
            if uptrend:
                cnt_uptrend += 1
            lines.append(f"{s['code']} {s['name']} chg={q['chg_pct']}% vol_ratio={stock['vol_ratio']} uptrend={uptrend}")
        except Exception as e:
            lines.append(f"{s['code']} 錯誤：{e}")
    lines.append(f"報價OK:{cnt_quote_ok} K線OK:{cnt_hist_ok} 多頭:{cnt_uptrend}")
    result = "\n".join(lines)
    push(result)
    return {"status": "ok", "result": result}


@app.get("/scan-result")
def scan_result():
    try:
        with open("watchlist.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"status": "ok", "last_scan": data.get("last_scan", ""),
                "watchlist": data.get("watchlist", [])}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _do_analysis(code: str, name: str, reply_token: str):
    """背景跑分析，reply 回覆（免費）"""
    result = analyze(code, name)
    reply(reply_token, result)

def _do_deep_analysis(code: str, name: str, reply_token: str):
    """背景跑深度分析，reply 回覆（免費）"""
    summary = build_price_summary(code)
    if "無法取得" in summary:
        reply(reply_token, f"抱歉，{name or code} 的 K 線資料抓取失敗。")
        return
    result = deep_analyze(code, name, summary)
    label = f"{name}（{code}）" if name else code
    # 今日數據區塊：取費波那契之前的部分
    today_block = summary.split("\n【費波那契")[0]
    reply(reply_token, f"【{label} 深度分析】\n{today_block}\n\n{result}")


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    sig = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, sig):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = await request.json()
    for event in data.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue

        text        = msg.get("text", "").strip().replace("＃", "#")
        reply_token = event.get("replyToken", "")
        source      = event.get("source", {})
        source_type = source.get("type", "user")          # user / group / room
        sender_id   = source.get("userId", "")
        group_id    = source.get("groupId", "") or source.get("roomId", "")
        is_me       = sender_id == MY_USER_ID
        is_group    = source_type in ("group", "room")


        def send(text: str):
            """推播到來源（群組或個人）"""
            target = group_id if is_group else MY_USER_ID
            push(text, target)

        def send_me(text: str):
            """只推給你"""
            push(text, MY_USER_ID)

        # 群組登記指令（只有你能用）
        if text == "#設定群組" and is_group and is_me:
            _register_group(group_id)
            send(f"群組已登記！\nID：{group_id}\n之後每日掃描結果會自動推播到這裡。")
            continue

        # ## 深度分析
        if text.startswith("##"):
            query = text[2:].strip()
            code = resolve(query)
            if not code:
                send(f"找不到「{query}」，請用代號或股票名稱。")
                continue
            name = get_name(code) or (query if not query.isdigit() else "")
            background_tasks.add_task(_do_deep_analysis, code, name, reply_token)
            continue

        # 固定指令
        if text == "#規則":
            send(
                "【查詢指令】\n"
                "#股票名稱或代號　技術分析（右側+SMC）\n"
                "##股票名稱或代號　Claude深度分析+新聞\n"
                "#大盤　加權指數今日表現\n"
                "#掃描　今日強勢股TOP15+TOP5\n"
                "\n"
                "【每日推播】（15:30自動）\n"
                "第一篇：強勢掃描TOP5+TOP15+右側攻略\n"
                "第二篇（私人）：持股損益+訊號\n"
                "\n"
                "【操作規則】\n"
                "三份資金，相同金額：\n"
                "第1份：突破確認進場\n"
                "第2份：跌-10%+站上MA20（攤平）\n"
                "第3份：突破20日高+量比>1.5x（追強）\n"
                "\n"
                "停損：跌-15%全砍，不猶豫\n"
                "ETF長抱不計停利\n"
                "單股持倉>60萬不加碼\n"
                "\n"
                "【訊號說明】\n"
                "🔴 賣出：硬停損或移動停利\n"
                "⚠️ 注意：虧損-8%～-15%\n"
                "📍 SMC回測：回測MA20右側確認\n"
                "🟡 攤平候選：跌-10%+站MA20\n"
                "🟢 加碼候選：突破20日高+量比>1.5x+MA5>MA20"
            )
            continue

        if text == "#大盤":
            send(cmd_market())
            continue

        if text == "#掃描":
            send("掃描中，請稍候...")
            send(scan_top15_msg())
            continue

        # 只限你的指令
        if text == "#持股":
            if not is_me:
                continue  # 其他人打沒反應
            send_me("查詢持股中...")
            send_me(cmd_holdings())
            continue

        # 標準分析
        if text.startswith("#"):
            query = text[1:].strip()
            code = resolve(query)
            if not code:
                send(f"找不到「{query}」，請用代號（如 #2330）或常見股票名稱。")
                continue
            name = get_name(code) or (query if not query.isdigit() else "")
            background_tasks.add_task(_do_analysis, code, name, reply_token)
            continue

    return JSONResponse({"status": "ok"})
