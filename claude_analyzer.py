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

    prompt = f"""你是台股技術分析專家，操作風格右側交易為主、SMC為輔，目標 R:R ≥ 3:1。

【輸出規則，違反即重來】
- 純文字，禁止任何 markdown
- 所有價位必須來自下方真實資料，禁止自行推估
- 大標題格式：【 區塊名稱 】（含空格）
- 列點用 ▸，劇本編號用①②③④
- 所有價格後加「元」
- R:R = 報酬÷風險，寫成「X:1」；閾值：回測 ≥ 3:1，突破/追價/加碼 ≥ 2:1
- R:R 1.5 ～ 閾值以下：寫「偏低，R:R X:1」
- R:R ≤ 1.5：寫「不建議，R:R X:1」，不展開
- 每點一句話，禁止長篇解釋，禁止重列數字

【定義】
- 多頭排列：MA5 > MA20 > MA60 且現價在三線之上，缺一不可；否則均線糾結
- FVG：第一K高點 < 第三K低點（看漲），缺口未填補才算有效；標明現價上方（壓力）或下方（支撐）
- 支撐必須低於現價，壓力必須高於現價
- BSL：近期高點密集區，必須高於現價；今日已突破則往上找新BSL

=== {label} 最新資料 ===
{price_summary}

近期新聞：
{news}

=== 輸出格式（依序，不可跳過）===

【 消息面 】
▸ ①事件 → 影響（一句話）
▸ ②事件 → 影響（一句話）
無消息則：▸ 無重大消息，以技術面為主

【 技術判斷 】
▸ MA：多頭排列/均線糾結/空頭排列，一句話結論
▸ 量能：縮量/放量/爆量，一句話說明意義
▸ 位置：現價在費波哪個區間，一句話

【 SMC 結構 】
▸ 階段：積累/推進/派發/下跌，一句話
▸ OB：XXX ～ XXX 元（來源說明，不得用MA倍數換算）
▸ FVG：XXX ～ XXX 元（上方壓力/下方支撐），或「無明顯FVG」
▸ BSL：XXX 元

【 關鍵價位 】（低XXX元→高XXX元）
▸ 首選進場：XXX 元（費波X.XXX + OB/MA共振，低於現價）
▸ 次要支撐：XXX 元（來源）
▸ 停損：XXX 元（OB下緣或結構低點）
▸ 壓力 XXX 元　TP1 XXX 元　TP2 XXX 元

【 整體傾向 】
▸ 現價 R:R：X:1（若 < 3:1 說明需等回測到哪裡）
▸ 結論：一句話

【 進場劇本 】
停損統一：XXX 元（來源）　TP1 XXX 元　TP2 XXX 元

①回測｜XXX 元（費波+OB共振）止跌K確認
▸ 進場 XXX　停損 XXX　R:R X:1　有效 X日

②突破｜收盤突破XXX 元（高於現價），量 > 1.5x均量
▸ 停損A XXX 元　R:R X:1（量比1.5x～2x）
▸ 停損B XXX 元（突破K中點）　R:R X:1（量比>2x）

③追價｜XXX 元站穩（BSL掃單完成，高於現價）
▸ 進場 XXX　停損 XXX　R:R X:1

④加碼｜獲利≥5%，量縮回測MA5或費波0.5不破
▸ 加碼 XXX　停損 XXX　R:R X:1　有效 X日"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )

    if resp.status_code == 200:
        try:
            return resp.json()["content"][0]["text"]
        except Exception as e:
            return f"Claude API 回應解析失敗：{e}\n{resp.text[:200]}"
    return f"Claude API 錯誤：{resp.status_code} {resp.text[:200]}"
