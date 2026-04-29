import os, hashlib, hmac, base64, json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import requests
from analyzer import analyze
from twse_fetcher import fetch
from claude_analyzer import deep_analyze
from stock_names import resolve
from commands import cmd_market, cmd_holdings, cmd_scan
from market_scanner import run_daily_scan
import pandas as pd

LINE_TOKEN   = os.environ["LINE_TOKEN"]
LINE_SECRET  = os.environ.get("LINE_SECRET", "")
MY_USER_ID   = os.environ["LINE_USER_ID"]
GROUP_ID_FILE = "group_ids.json"

app = FastAPI()


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
    df = fetch(code)
    if df.empty:
        return "無法取得資料"
    df["MA5"]  = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    last = df.iloc[-1]
    prev = df.iloc[-2]
    chg_pct = (last["Close"] - prev["Close"]) / prev["Close"] * 100
    high60 = df["High"].tail(60).max()
    low60  = df["Low"].tail(60).min()
    recent = df.tail(10)[["Open","High","Low","Close","Volume"]].round(1).to_string()
    is_limit = chg_pct >= 9.5
    chg_label = f"漲停 +{chg_pct:.1f}%" if is_limit else f"{chg_pct:+.1f}%"
    return (
        f"【今日數據】\n"
        f"收：{last['Close']:.0f}（{chg_label}）\n"
        f"開：{last['Open']:.1f}　高：{last['High']:.1f}　低：{last['Low']:.1f}\n"
        f"量：{int(last['Volume'])//1000:,}張\n"
        f"MA5：{last['MA5']:.1f}　MA20：{last['MA20']:.1f}　MA60：{last['MA60']:.1f}\n"
        f"60日高：{high60:.0f}　低：{low60:.0f}\n"
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


@app.get("/daily-scan")
def daily_scan():
    """每日掃全市場，更新 watchlist.json，推播 TOP15+TOP5 到你和所有群組"""
    result = run_daily_scan()
    push_all_groups(result)
    return {"status": "ok", "message": result[:100]}


@app.get("/scan-result")
def scan_result():
    try:
        with open("watchlist.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"status": "ok", "last_scan": data.get("last_scan", ""),
                "watchlist": data.get("watchlist", [])}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/webhook")
async def webhook(request: Request):
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

        text        = msg.get("text", "").strip()
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
            name = query if not query.isdigit() else ""
            send("資料抓取中，約需 15 秒...")
            summary = build_price_summary(code)
            if "無法取得" in summary:
                send(f"抱歉，{query} 的 K 線資料抓取失敗，請稍後再試或改用代號查詢。")
                continue
            result = deep_analyze(code, name, summary)
            label = f"{name}（{code}）" if name else code
            today_block = summary.split("\n近10日")[0]
            send(f"【{label} 深度分析】\n\n{today_block}\n\n{result}")
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
                "第3份：漲+10%+MA5>MA20（追強）\n"
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
                "🟢 加碼候選：獲利+10%追強"
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
            name = query if not query.isdigit() else ""
            send("分析中，請稍候...")
            result = analyze(code, name)
            send(result)
            continue

    return JSONResponse({"status": "ok"})
