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

【輸出規則，違反即重來】
- 純文字，禁止任何 markdown（不用 **、##、---、`、表格符號）
- 所有價位必須來自下方真實資料，禁止自行推估或編造
- 大標題用【】，列點用▶，劇本編號用①②③④
- 所有價格後加「元」
- 全部劇本停損位統一，只有一個停損基準
- R:R < 1:2 直接寫「不建議，R:R僅X:1」不展開
- 每個區塊精簡，禁止長篇解釋

=== {label} 最新資料 ===

{price_summary}

近期新聞：
{news}

=== 分析順序（嚴格照此順序）===

【消息面】
①事件 → 影響方向
②事件 → 影響方向
無重大消息則寫：無重大消息，以技術面為主

【簡易判斷】
▶MA排列：多頭/空頭/混亂，一句話結論（禁止重列MA數字）
▶量能：縮量/放量/爆量，一句話說明意義（禁止重列成交量數字）
▶位置：現價在費波哪個區間，一句話

【SMC結構】
▶階段：積累/推進/派發/下跌，一句話
▶OB：XXX元～XXX元（說明來源）
▶FVG：XXX元～XXX元（無則寫「無明顯FVG」）
▶BSL：XXX元

【費波共振與關鍵位】（低XXX元→高XXX元）
▶首選進場區：XXX元（費波X.XXX + OB/MA共振原因）
▶次要支撐：XXX元（來源）
▶停損基準：XXX元（來源）
▶壓力：XXX元　TP1：XXX元　TP2：XXX元

【整體傾向】
▶R:R：現價進場約X:1（TP1基準）
▶若R:R < 1:3：現價不宜追，等回測至 XXX元
▶結論：偏多等回測 / 可追 / 觀望 / 偏空迴避，一句話說明理由

【四個進場劇本】
停損統一：XXX元（來源說明）

①回測｜XXX元（費波+OB共振）止跌K確認
進場 XXX元　停損 XXX元　TP1 XXX元　TP2 XXX元　R:R X:1　有效 X日

②突破｜收盤突破XXX元，量大於1.5倍均量
停損A：XXX元（量比1.5x～2x）　R:R X:1
停損B：突破K中點XXX元（量大於2x）　R:R X:1　有效 X日

③追價｜XXX元站穩（BSL掃單完成）
進場 XXX元　停損 XXX元　TP1 XXX元　TP2 XXX元　R:R X:1　有效 X日
若R:R < 1:2直接寫「不建議，R:R僅X:1」

④加碼｜已持有獲利≥5%，量縮回測MA5或費波0.5不破
加碼價 XXX元　新停損 XXX元　TP1 XXX元　TP2 XXX元　R:R X:1　有效 X日
若停損距離不足3%直接寫「不建議，停損太緊」"""

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
        return resp.json()["content"][0]["text"]
    return f"Claude API 錯誤：{resp.status_code} {resp.text[:200]}"
