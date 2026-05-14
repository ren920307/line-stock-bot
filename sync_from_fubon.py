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

    _save_snapshot(wl, new_active)
    _push_to_github_and_render()
    return 0


def _push_to_github_and_render():
    """git push watchlist.json 到 GitHub，再 ping Render 拉取最新版本"""
    import subprocess, urllib.request

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    today = date.today().isoformat()

    # git push（依賴本機已設好的 git 憑證）
    try:
        subprocess.run(["git", "add", "watchlist.json"], cwd=repo_dir, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_dir, capture_output=True
        )
        if result.returncode != 0:  # 有變動才 commit
            subprocess.run(
                ["git", "commit", "-m", f"chore: auto sync holdings {today} [skip render]"],
                cwd=repo_dir, check=True, capture_output=True
            )
            subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, check=True, capture_output=True)
            print("  ✅ watchlist.json 已推到 GitHub")
        else:
            print("  ↔️  watchlist.json 無變動，略過 push")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️ git push 失敗：{e.stderr.decode()[:200]}")
        return

    # ping Render 拉取（等 GitHub CDN 更新，稍待 3 秒）
    import time
    time.sleep(3)
    try:
        req = urllib.request.Request(
            "https://line-stock-bot-a54m.onrender.com/pull-watchlist",
            headers={"User-Agent": "sync_from_fubon"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
            print(f"  ✅ Render 已拉取最新 watchlist：{body}")
    except Exception as e:
        print(f"  ⚠️ Render ping 失敗：{e}")


def _save_snapshot(wl: dict, active: dict):
    """把持股快照存到 Claude 記憶資料夾，供下次對話參考"""
    snapshot_path = os.path.expanduser(
        "~/.claude/projects/-Users-ren/memory/holdings_snapshot.md"
    )
    today = date.today().isoformat()
    lines = [
        "---",
        "name: 持股快照",
        "description: 最新持股清單（成本/停損），由 sync_from_fubon.py 自動更新",
        "type: reference",
        "---",
        f"# 持股快照（{today}）",
        "",
        "| 代號 | 名稱 | 成本 | 停損 | 距停損% |",
        "|------|------|------|------|---------|",
    ]
    for h in active.values():
        cost = h.get("cost") or 0
        stop = h.get("stop") or 0
        dist = round((cost - stop) / cost * 100, 1) if cost and stop else "-"
        lines.append(f"| {h['code']} | {h['name']} | {cost:.1f} | {stop if stop else '-'} | {dist} |")

    lines += ["", f"_同步時間：{today}_"]

    with open(snapshot_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  📝 快照已儲存：{snapshot_path}")


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code or 0)  # 跳過 fubon_neo C++ destructor，避免 crash dialog
