"""
用 Fugle Marketdata REST API 抓台股資料。
歷史 K 線 → 給分析用
即時報價 → 給掃描/持股用
"""
import os
import requests
import pandas as pd
from datetime import date, timedelta

BASE = "https://api.fugle.tw/marketdata/v1.0/stock"


def _headers() -> dict:
    return {"X-API-KEY": os.environ.get("FUGLE_API_KEY", "")}


class FugleRateLimitError(Exception):
    """Fugle API 超過速率限制（429）"""


def fetch_history(code: str, days: int = 120) -> pd.DataFrame:
    """歷史 K 線，給技術分析用"""
    end = date.today()
    start = end - timedelta(days=int(days * 1.8))
    url = f"{BASE}/historical/candles/{code}"
    try:
        r = requests.get(url, headers=_headers(), params={
            "from": start.isoformat(),
            "to": end.isoformat(),
        }, timeout=10)
        if r.status_code == 429:
            raise FugleRateLimitError(f"歷史K線 429 rate limit: {code}")
        data = r.json()
        candles = data.get("data")
        if isinstance(candles, dict):
            candles = candles.get("candles", [])
        if not candles:
            candles = data.get("candles", [])
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles)
        df["Date"] = pd.to_datetime(df["date"])
        df = df.set_index("Date").sort_index()
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })[["Open", "High", "Low", "Close", "Volume"]]
        return df.tail(days)
    except FugleRateLimitError:
        raise
    except Exception:
        return pd.DataFrame()


def fetch_quote(code: str) -> dict:
    """單檔即時報價"""
    url = f"{BASE}/intraday/quote/{code}"
    try:
        r = requests.get(url, headers=_headers(), timeout=8)
        if r.status_code == 429:
            raise FugleRateLimitError(f"報價 429 rate limit: {code}")
        d = r.json()
        close = d.get("lastPrice") or d.get("closePrice") or d.get("previousClose")
        prev  = d.get("previousClose") or d.get("referencePrice")
        high  = d.get("highPrice")
        low   = d.get("lowPrice")
        open_ = d.get("openPrice")
        vol_raw = d.get("total", {}).get("tradeVolume") if isinstance(d.get("total"), dict) else d.get("tradeVolume")
        vol     = int(vol_raw) * 1000 if vol_raw else None  # Fugle 單位是張，乘回股與 twstock 一致
        chg_pct = round((close - prev) / prev * 100, 2) if (close and prev) else None
        return {"close": close, "prev": prev, "high": high, "low": low, "open": open_, "volume": vol, "chg_pct": chg_pct}
    except FugleRateLimitError:
        raise
    except Exception:
        return {}


def fetch_quotes_bulk(codes: list) -> dict:
    """批次抓多檔即時報價，回傳 {code: {...}}"""
    result = {}
    for code in codes:
        result[code] = fetch_quote(code)
    return result
