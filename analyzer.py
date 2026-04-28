import warnings; warnings.filterwarnings("ignore")
import logging, io, contextlib
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
import yfinance as yf
import pandas as pd


def fetch(code: str, days: int = 120) -> pd.DataFrame:
    for suf in (".TWO", ".TW"):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            h = yf.Ticker(code + suf).history(period=f"{days}d", auto_adjust=False)
        if not h.empty:
            return h[["Open", "High", "Low", "Close", "Volume"]]
    return pd.DataFrame()


def analyze(code: str, name: str = "") -> str:
    df = fetch(code)
    if df.empty:
        return f"找不到 {code} 的資料，請確認代號是否正確。"

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    chg = last["Close"] - prev["Close"]
    chg_pct = chg / prev["Close"] * 100

    high60 = df["High"].tail(60).max()
    low60 = df["Low"].tail(60).min()
    high20 = df["High"].tail(20).max()
    low20 = df["Low"].tail(20).min()

    ma5 = last["MA5"]
    ma20 = last["MA20"]
    ma60 = last["MA60"]
    close = last["Close"]

    # 均線排列判斷
    if ma5 > ma20 > ma60:
    	trend = "多頭排列（MA5>MA20>MA60）"
    elif ma5 < ma20 < ma60:
        trend = "空頭排列（MA5<MA20<MA60）"
    else:
        trend = "均線糾結整理中"

    # 價格與均線關係
    if close > ma5 > ma20:
        pos = "站上MA5、MA20，強勢"
    elif close > ma20:
        pos = "站上MA20，偏多"
    elif close > ma60:
        pos = "站上MA60，偏中性"
    else:
        pos = "跌破MA60，偏弱"

    # OB 區抓近期低點反彈起漲區
    recent = df.tail(20)
    ob_low = recent["Low"].min()
    ob_high = recent.loc[recent["Low"] == ob_low, "High"].values
    ob_top = float(ob_high[0]) if len(ob_high) > 0 else float(ma20)

    # 進場、停損、目標
    entry = round(float(ma20) * 1.01, 1)
    stop = round(float(ma20) * 0.97, 1)
    target1 = round(float(high60), 1)
    target2 = round(float(high60) * 1.08, 1)
    risk = entry - stop
    reward1 = target1 - entry
    rr = round(reward1 / risk, 1) if risk > 0 else 0

    label = f"{name}（{code}）" if name else code

    return (
        f"【{label} 技術分析】\n"
        f"收:{close:.0f}（{chg:+.0f} {chg_pct:+.1f}%）\n"
        f"量:{int(last['Volume']):,}張\n"
        f"MA5:{ma5:.1f}  MA20:{ma20:.1f}  MA60:{ma60:.1f}\n"
        f"\n"
        f"【形態】\n"
        f"{trend}，{pos}\n"
        f"60日區間：{low60:.0f}～{high60:.0f}\n"
        f"\n"
        f"【SMC結構】\n"
        f"Order Block：{ob_low:.0f}～{ob_top:.0f}\n"
        f"Buy-Side Liquidity：{high60:.0f}上方\n"
        f"\n"
        f"【進場建議（右側）】\n"
        f"進場：回測MA20（{ma20:.0f}）附近撐穩\n"
        f"停損：{stop:.0f}（MA20下3%）\n"
        f"目標1：{target1:.0f}（前高）\n"
        f"目標2：{target2:.0f}\n"
        f"R:R ≈ {rr}:1\n"
        f"\n"
        f"等右側確認再進，停損優先。"
    )
