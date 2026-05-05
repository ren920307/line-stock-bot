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
- 所有價格數字後面必須加「元」，例如：57元、51.3元、63.4元

=== {label} 最新資料 ===

{price_summary}

近期新聞：
{news}

=== 分析順序（必須按此順序，後面的結論要根據前面的分析）===

【今日數據】
MA排列：MA5 vs MA20 vs MA60（多頭/空頭/混亂），說明排列意義
量能：今日量 vs 均量（放量/縮量/爆量），量比數字說出來
價格位置：站上/跌破 MA20、距60日高低點位置與含義

【SMC結構】
階段：積累 / 推進 / 派發 / 下跌
OB（機構成本帶，回測有撐）：XXX元～XXX元，說明為何是這個區間
FVG（跳空缺口，可能回補）：XXX元～XXX元
BSL（前高掃單目標）：XXX元

【費波共振與支撐阻力】
根據上方費波那契數據，找出與 OB / MA 共振最強的回測位
共振最強位：XXX元（費波 X.XXX ＋ OB/MA20/MA60 說明共振原因）
次要支撐：XXX元（來源說明）
壓力：XXX元（來源說明）
停損邏輯：跌破 XXX元 收盤出場，原因是 XXX
TP1：XXX元（費波 1.272）　TP2：XXX元（費波 1.618）

【風險與整體傾向】
① XXX（高位追強風險、連漲後隔日風險等）
② XXX（R:R 評估，若現價 R:R < 1:3 明確寫「現價不宜追，等回測至 XXX元」）
整體傾向：偏多操作 / 等回測 / 觀望 / 偏空迴避，一句話說明理由

【三個進場劇本】
根據以上 OB / FVG / 費波共振位推導，每個劇本條件都必須有前面分析的根據：
① 回測｜條件：回測 XXX元（費波共振＋OB/FVG）撐穩收盤　進場 XXX元　停損 XXX元　TP1 XXX元　TP2 XXX元　R:R 1:X
② 突破｜條件：突破 XXX元 且量比>Xx　進場 XXX元　停損 XXX元　TP1 XXX元　TP2 XXX元　R:R 1:X
③ 追價｜條件：XXX（說明何種情況才追）　進場 XXX元　停損 XXX元　TP1 XXX元　TP2 XXX元　R:R 1:X"""

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
