import os
import requests


def deep_analyze(code: str, name: str, price_summary: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "未設定 ANTHROPIC_API_KEY，無法使用深度分析。"

    label = f"{name}（{code}）" if name else code
    prompt = f"""你是台股技術分析專家，風格是右側交易為主、SMC為輔。

以下是 {label} 的最新資料：
{price_summary}

請用以下格式分析，語氣直接像交易老手：
1. 目前市場結構（BOS/CHoCH/積累/推進）
2. 關鍵 SMC 區域（Order Block、FVG、流動性）
3. 右側進場條件（具體價位或K棒條件）
4. 停損位與目標位（附 R:R）
5. 主要風險（2～3點）

不要廢話，直接給判斷。"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )

    if resp.status_code == 200:
        return resp.json()["content"][0]["text"]
    return f"Claude API 錯誤：{resp.status_code}"
