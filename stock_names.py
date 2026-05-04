import json
import os

_cache = {}

def _load():
    global _cache
    path = os.path.join(os.path.dirname(__file__), "stock_map.json")
    with open(path, "r", encoding="utf-8") as f:
        _cache = json.load(f)

_load()

def resolve(text: str):
    text = text.strip()
    if text.isdigit():
        return text
    if text in _cache:
        return _cache[text]
    # 前綴模糊比對（至少 2 字）
    if len(text) >= 2:
        for name, code in _cache.items():
            if name.startswith(text):
                return code
    return None


def get_name(code: str) -> str:
    """從代號反查中文名稱"""
    for name, c in _cache.items():
        if c == code:
            return name
    return ""
