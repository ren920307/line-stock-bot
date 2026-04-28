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
    return _cache.get(text)
