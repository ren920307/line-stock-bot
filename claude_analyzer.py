import os
import requests
from bs4 import BeautifulSoup


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

=== {label} 最新資料 ===

【價格與均線】
{price_summary}

【近期新聞】
{news}

=== 請依照以下格式輸出分析 ===

【基本面與消息面】
整理新聞重點，說明近期有無重大利多/利空，影響力如何。

【市場結構（SMC）】
判斷目前是積累/推進/派發/下跌哪個階段，標出 Order Block、FVG、流動性位置，用中文說明每個區域的意義。

【技術面判讀（右側）】
均線排列、價格位置、量能狀況，近期走勢特徵。

【進場建議】
進場條件（具體價位或K棒條件）
停損位
短線目標／中線目標
R:R

【風險提示】
2～3點，直接說重點。

語氣像交易老手和朋友說話，不要廢話。"""

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
