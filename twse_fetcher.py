"""
台股 K 線資料：使用 twstock 套件抓證交所/櫃買歷史資料。
"""
import pandas as pd
import twstock


def fetch(code: str, days: int = 120) -> pd.DataFrame:
    try:
        s = twstock.Stock(str(code))
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
