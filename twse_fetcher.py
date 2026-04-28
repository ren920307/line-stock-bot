import pandas as pd
import twstock
from datetime import date


def fetch(code: str, days: int = 120) -> pd.DataFrame:
    try:
        s = twstock.Stock(str(code))
        # 預設只有當月，需要往前多抓
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
