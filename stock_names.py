import requests

_cache = {}  # {name: code}


def _build_cache():
    global _cache
    mapping = {}
    # 上市
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        for d in r.json():
            name = d.get("Name", "").strip()
            code = d.get("Code", "").strip()
            if name and code:
                mapping[name] = code
    except Exception:
        pass
    # 上櫃
    try:
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        for d in r.json():
            name = d.get("CompanyName", "").strip()
            code = d.get("SecuritiesCompanyCode", "").strip()
            if name and code:
                mapping[name] = code
    except Exception:
        pass
    _cache = mapping


def resolve(text: str):
    text = text.strip()
    if text.isdigit():
        return text
    if not _cache:
        _build_cache()
    return _cache.get(text)
