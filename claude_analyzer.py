import os
import requests
from bs4 import BeautifulSoup
from twse_fetcher import fetch as twse_fetch


def fetch_news(code: str) -> str:
    """爬鉅亨網該股最新新聞標題"""
    news_items = []
    try:
        url = f"https://news.cnyes.com/news/cat/twstock?code={code}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("h3, .news-title, [class*='title']")
        seen = set()
        for el in items:
            t = el.get_text(strip=True)
            if t and len(t) > 8 and t not in seen:
                news_items.append(t)
                seen.add(t)
            if len(news_items) >= 8:
                break
    except Exception:
        pass

    # fallback：Yahoo 財經
    if len(news_items) < 3:
        try:
            url = f"https://tw.stock.yahoo.com/quote/{code}.TW/news"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            for el in soup.select("h3"):
                t = el.get_text(strip=True)
                if t and len(t) > 8 and t not in news_items:
                    news_items.append(t)
                if len(news_items) >= 8:
                    break
        except Exception:
            pass

    if not news_items:
        return "（未能取得近期新聞）"
    return "\n".join(f"・{n}" for n in news_items)


def deep_analyze(code: str, name: str, price_summary: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "未設定 ANTHROPIC_API_KEY，無法使用深度分析。"

    label = f"{name}（{code}）" if name else code

    news = fetch_news(code)

    prompt = f"""你是台股技術分析專家，操作風格是右側交易為主、SMC為輔。

輸出規則（非常重要）：
- 純文字，不使用任何 markdown（不用 **、##、---、`、表格符號）
- 用空行分隔段落即可
- 數字與價位要具體，不能含糊

=== {label} 最新資料 ===

價格與均線：
{price_summary}

近期新聞：
{news}

=== 輸出格式 ===

【消息面】
整理新聞 2～3 個重點，直接說影響方向，不超過 5 行。

【市場結構 SMC】
說明現在是哪個階段（積累/推進/派發/下跌）。
給出具體價位：
- Order Block 區間（機構成本帶，回測有撐）：XXX～XXX
- FVG 缺口（跳空未填區間）：XXX～XXX（若無則說明）
- Buy-Side Liquidity（上方掃單目標）：XXX

【技術面】
均線排列、價格位置、量能，3～4 行。

【三個進場劇本】
劇本一 追價（現價或開盤追）
進場：XXX　停損：XXX　目標：XXX　R:R X:1

劇本二 SMC 回測安全位
進場條件：回測 OB/FVG XXX～XXX 撐穩
停損：XXX　目標：XXX　R:R X:1

劇本三 突破確認
進場條件：突破 XXX 且量比 > X 倍收盤確認
停損：XXX　目標：XXX　R:R X:1

【風險提示】
2 點，直接說。

最後一行：整體傾向（偏多操作 / 觀望 / 偏空迴避）"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )

    if resp.status_code == 200:
        return resp.json()["content"][0]["text"]
    return f"Claude API 錯誤：{resp.status_code} {resp.text[:200]}"
