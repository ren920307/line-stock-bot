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
- 所有價位必須來自上方提供的真實資料，絕對不能自行推估或編造
- 若某個數字在資料中找不到，直接寫「資料不足無法判斷」，不要猜

=== {label} 最新資料 ===

價格與均線：
{price_summary}

近期新聞：
{news}

=== 輸出格式（嚴格遵守）===

每個區塊最多 3 行，不廢話，直接給數字和結論。

【消息面】
列 2～3 點，每點一行，格式：「事件 → 影響」

【SMC結構】
階段：XXX（積累/推進/派發/下跌）
OB（機構成本帶，回測有撐）：XXX～XXX
FVG（跳空未填，可能回補）：XXX～XXX　BSL（掃單目標）：XXX

【技術面】
均線：XXX排列　價格位置：XXX　量能：XXX
一句話判斷走勢特徵

【三個進場劇本】
目標價必須使用上方費波那契的 TP1 和 TP2 數字，格式嚴格如下：
① 追價｜進場 XXX　停損 XXX　TP1 XXX　TP2 XXX　R:R X:1
② 回測｜進場條件 OB/FVG XXX～XXX 撐穩　停損 XXX　TP1 XXX　TP2 XXX　R:R X:1
③ 突破｜突破 XXX 量比>X倍　停損 XXX　TP1 XXX　TP2 XXX　R:R X:1

【風險】
① XXX
② XXX

整體傾向：偏多操作 / 觀望 / 偏空迴避"""

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
