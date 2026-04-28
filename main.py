import os, hashlib, hmac, base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import requests
from analyzer import analyze
from stock_names import resolve

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

        if not text.startswith("#"):
            continue

        query = text[1:].strip()
        code = resolve(query)

        if not code:
            push(f"找不到「{query}」，請用代號（如 #2330）或常見股票名稱。")
            continue

        name = query if not query.isdigit() else ""
        push("分析中，請稍候...")
        result = analyze(code, name)
        push(result)

    return JSONResponse({"status": "ok"})
