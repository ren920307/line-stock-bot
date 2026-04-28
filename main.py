import os, hashlib, hmac, base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import requests
from analyzer import analyze, fetch
from claude_analyzer import deep_analyze
from stock_names import resolve
import pandas as pd

LINE_TOKEN = os.environ["LINE_TOKEN"]
LINE_SECRET = os.environ.get("LINE_SECRET", "")

app = FastAPI()


def push(text: str):
    user_id = os.environ["LINE_USER_ID"]
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )


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
    return (
        f"今日 收:{last['Close']:.0f}（{chg_pct:+.1f}%）開:{last['Open']:.0f} 高:{last['High']:.0f} 低:{last['Low']:.0f} 量:{int(last['Volume'])//1000}張\n"
        f"MA5:{last['MA5']:.1f}  MA20:{last['MA20']:.1f}  MA60:{last['MA60']:.1f}\n"
        f"60日高:{high60:.0f}  低:{low60:.0f}\n"
        f"\n近10日K線：\n{recent}"
    )


@app.get("/")
def health():
    return {"status": "ok"}


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
        text = msg.get("text", "").strip()

        # ## 深度分析（Claude AI）
        if text.startswith("##"):
            query = text[2:].strip()
            code = resolve(query)
            if not code:
                push(f"找不到「{query}」，請用代號或股票名稱。")
                continue
            name = query if not query.isdigit() else ""
            push("Claude 深度分析中，約需 10 秒...")
            summary = build_price_summary(code)
            result = deep_analyze(code, name, summary)
            label = f"{name}（{code}）" if name else code
            push(f"【{label} 深度分析】\n\n{result}")
            continue

        # # 標準分析
        if text.startswith("#"):
            query = text[1:].strip()
            code = resolve(query)
            if not code:
                push(f"找不到「{query}」，請用代號（如 #2330）或常見股票名稱。")
                continue
            name = query if not query.isdigit() else ""
            push("分析中，請稍候...")
            result = analyze(code, name)
            push(result)
            continue

    return JSONResponse({"status": "ok"})
