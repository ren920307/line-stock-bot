"""
從富邦 Neo API 同步持股 → watchlist.json holdings

執行：
    python3 sync_from_fubon.py

效果：
- 富邦有、watchlist 沒有 → 新增（停損留空，需手動填）
- 富邦有、watchlist 也有 → 更新成本（停損保留）
- 富邦沒有、watchlist 有 → 標記 _archived + sold_date（不直接刪）
"""
import json
import os
import sys
from datetime import date

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")
FUBON_TOOLS    = os.path.join(os.path.dirname(__file__), "../股票工具")

sys.path.insert(0, FUBON_TOOLS)


def load_watchlist():
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(data):
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def lookup_name(code):
    """查股票中文名稱（TWSE 上市 → TPEx 上櫃）"""
    import urllib.request, json as _json

    def _fetch(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return _json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception:
            return []

    # 上市
    for row in _fetch("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"):
        if str(row.get("公司代號", "")).strip() == code:
            return row.get("公司簡稱", code).strip()

    # 上櫃
    for row in _fetch("https://openapi.twse.com.tw/v1/opendata/t187ap03_O"):
        if str(row.get("公司代號", "")).strip() == code:
            return row.get("公司簡稱", code).strip()

    return code


def main():
    # 1. 取富邦庫存
    try:
        from fubon_live import get_shared
        fb = get_shared()
        positions = fb.get_positions()
    except Exception as e:
        print(f"❌ 富邦 API 失敗：{e}")
        return 1

    fubon_codes = {str(p["symbol"]): p for p in positions}

    # 2. 讀現有 watchlist
    wl = load_watchlist()
    holdings = wl.get("holdings", [])

    # 3. 建立現有 holdings 索引（忽略已 archived）
    active = {h["code"]: h for h in holdings if not h.get("_archived")}
    archived = [h for h in holdings if h.get("_archived")]

    added, updated, removed = [], [], []

    # 4. 富邦 → watchlist
    new_active = {}
    for code, pos in fubon_codes.items():
        cost = round(float(pos["cost"]), 2)
        if code in active:
            old = active[code]
            old["cost"] = cost
            new_active[code] = old
            if abs(cost - (active[code].get("cost") or 0)) > 0.01:
                updated.append(f'{old["name"]}({code}) 成本→{cost}')
        else:
            name = lookup_name(code)
            new_active[code] = {
                "code": code,
                "name": name,
                "cost": cost,
                "stop": None,
                "is_small": False,
                "max_position": 100,
            }
            added.append(f'{name}({code}) @{cost}')

    # 5. watchlist 有但富邦沒有 → archived
    today = date.today().isoformat()
    for code, h in active.items():
        if code not in fubon_codes:
            h["_archived"] = True
            h["sold_date"] = today
            archived.append(h)
            removed.append(f'{h["name"]}({code})')

    # 6. 寫回（active 依富邦順序 + archived）
    wl["holdings"] = list(new_active.values()) + archived
    save_watchlist(wl)

    print(f"✅ 同步完成：持股 {len(new_active)} 檔")
    if added:
        print(f"  ➕ 新增：{', '.join(added)}")
    if updated:
        print(f"  ✏️  成本更新：{', '.join(updated)}")
    if removed:
        print(f"  ➖ 已出場（archived）：{', '.join(removed)}")
    if not added and not updated and not removed:
        print("  ↔️  無變動")

    return 0


if __name__ == "__main__":
    code = main()
    os._exit(code or 0)  # 跳過 fubon_neo C++ destructor，避免 crash dialog
