"""
歷史 K 線抓取：Fugle API 優先，twstock 作為 fallback。

Render（Linux）上 twstock 連 TWSE/TPEx 常被擋，所以預設用 Fugle 官方 API。
本機若沒設 FUGLE_API_KEY，會自動退回 twstock。
"""
import pandas as pd
from datetime import date

from fugle_fetcher import fetch_history as _fugle_fetch


def _twstock_fetch(code: str, days: int = 120) -> pd.DataFrame:
    try:
        import twstock
        s = twstock.Stock(str(code))
        today = date.today()
        months_needed = max(6, days // 20)
        year = today.year
        month = today.month - months_needed
        while month <= 0:
            month += 12
            year -= 1
        s.fetch_from(year, month)
        n = min(len(s.date), days)
        df = pd.DataFrame({
            "Date":   s.date[-n:],
            "Open":   s.open[-n:],
            "High":   s.high[-n:],
            "Low":    s.low[-n:],
            "Close":  s.price[-n:],
            "Volume": s.capacity[-n:],
        })
        df = df.dropna().set_index("Date").sort_index()
        return df
    except Exception:
        return pd.DataFrame()


def fetch(code: str, days: int = 120) -> pd.DataFrame:
    df = _fugle_fetch(code, days)
    if df is not None and not df.empty:
        return df
    return _twstock_fetch(code, days)
