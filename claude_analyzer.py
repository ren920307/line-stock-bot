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

    prompt = f"""你是台股技術分析專家，操作風格是右側交易為主、SMC為輔，目標 R:R ≥ 1:3。

輸出規則：
- 純文字，不使用任何 markdown（不用 **、##、---、`、表格符號）
- 所有價位必須來自下方提供的真實資料，絕對不能自行推估或編造
- 每個區塊最多 3 行，不廢話，直接給數字和結論
- 三個進場劇本必須是前面分析的結論，不能憑空給數字

=== {label} 最新資料 ===

{price_summary}

近期新聞：
{news}

=== 分析順序（必須按此順序，後面的結論要根據前面的分析）===

【今日數據判讀】
MA排列：MA5 vs MA20 vs MA60（多頭/空頭/混亂）
量能：今日量 vs 均量（放量/縮量/爆量）
價格位置：站上/跌破 MA20、60日高低點相對位置

【SMC結構】
階段：積累 / 推進 / 派發 / 下跌
OB（機構成本帶，回測有撐）：XXX～XXX，說明為何是這個區間
FVG（跳空缺口，可能回補）：XXX～XXX
BSL（前高掃單目標）：XXX

【費波共振分析】
根據上方費波那契數據，指出哪個回測位與 OB 或 MA 共振最強
共振最強位：XXX（費波 X.XXX ＋ OB/MA20/MA60）
次要支撐：XXX
TP1：XXX（費波 1.272）　TP2：XXX（費波 1.618）

【支撐與阻力】
強支撐：XXX（說明來源：OB / FVG / MA / 費波）
壓力：XXX（說明來源）
停損邏輯：跌破 XXX 收盤 → 出場，原因是 XXX

【風險評估】
① XXX（如：高位追強、連漲停隔日開高走低風險）
② XXX（如：R:R 不足、倉位建議）
若目前位置 R:R < 1:3，明確寫出「現價不宜追，等回測至 XXX」

【三個進場劇本】
根據以上 OB / FVG / 費波共振位，給出三個有根據的進場條件：
① 追價｜條件：XXX　進場 XXX　停損 XXX　TP1 XXX　TP2 XXX　R:R 1:X
② 回測｜條件：回測 XXX（OB/FVG/費波共振）撐穩收盤　進場 XXX　停損 XXX　TP1 XXX　TP2 XXX　R:R 1:X
③ 突破｜條件：突破 XXX 且量比>Xx　進場 XXX　停損 XXX　TP1 XXX　TP2 XXX　R:R 1:X

整體傾向：偏多操作 / 等回測 / 觀望 / 偏空迴避"""

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
