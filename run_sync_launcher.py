#!/usr/bin/env python3
"""LaunchAgent launcher — 替代 run_sync.sh，繞過 bash 的 Desktop 存取限制"""
import os
import sys
import subprocess
import datetime
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "log", "sync.log")
RENDER_URL = "https://line-stock-bot-a54m.onrender.com/pull-watchlist"
PYTHON = sys.executable


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"{ts}: {msg}\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def keychain(service):
    return subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", "ren", "-w"],
        capture_output=True, text=True
    ).stdout.strip()


def notify_line(msg):
    """同步出問題時推 LINE 給 REN；通知本身失敗只記 log"""
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {keychain('line_bot_token')}"},
            json={"to": keychain("line_bot_userid"), "messages": [{"type": "text", "text": msg[:4900]}]},
            timeout=15,
        ).raise_for_status()
    except Exception as e:
        log(f"⚠️ LINE 通知失敗: {e}")


def find_problems(returncode, stdout, stderr):
    """從 sync_from_fubon.py 的結果挑出要通知的問題"""
    if returncode != 0:
        return [f"同步程式失敗（exit {returncode}）：{stderr.strip()[-300:]}"]
    return [line.strip() for line in stdout.splitlines()
            if "git push 失敗" in line or "Render ping 失敗" in line]


def main():
    weekday = datetime.datetime.now().isoweekday()
    if weekday >= 6:
        log("週末，略過")
        return

    log("開始同步持股...")
    result = subprocess.run(
        [PYTHON, os.path.join(BASE_DIR, "sync_from_fubon.py")],
        capture_output=True, text=True
    )
    if result.stdout:
        log(result.stdout.strip())
    problems = find_problems(result.returncode, result.stdout, result.stderr)
    if result.returncode != 0:
        log(f"❌ sync_from_fubon.py 失敗（exit {result.returncode}）: {result.stderr.strip()}")
    else:
        log("同步完成，通知 Render 更新持股...")
        try:
            resp = requests.get(RENDER_URL, timeout=30)
            log(f"pull-watchlist: {resp.json()}")
        except Exception as e:
            log(f"⚠️ pull-watchlist 失敗: {e}")
            problems.append(f"pull-watchlist 失敗：{e}")

    if problems:
        notify_line("⚠️ 持股同步有問題，LINE 的持股／停損監控可能還是舊資料\n"
                    + "\n".join(problems) + "\n細節見 line-stock-bot/log/sync.log")


def _selftest():
    assert find_problems(1, "", "boom") == ["同步程式失敗（exit 1）：boom"]
    assert find_problems(0, "  ⚠️ git push 失敗：rejected\n  ✅ ok", "") == ["⚠️ git push 失敗：rejected"]
    assert find_problems(0, "  ✅ watchlist.json 已推到 GitHub", "") == []
    print("selftest ok")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else main()
