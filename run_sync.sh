#!/bin/bash
# 每個交易日 14:00 自動同步富邦持股到 watchlist.json

DOW=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$DOW" -ge 6 ]; then
    echo "$(date '+%Y-%m-%d %H:%M'): 週末，略過" >> "$HOME/Desktop/Claude資料區/股票財經專案/line-stock-bot/log/sync.log"
    exit 0
fi

cd "$HOME/Desktop/Claude資料區/股票財經專案/line-stock-bot" || exit 1

echo "$(date '+%Y-%m-%d %H:%M'): 開始同步持股..." >> log/sync.log

/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 sync_from_fubon.py >> log/sync.log 2>&1

echo "$(date '+%Y-%m-%d %H:%M'): 完成" >> log/sync.log

# 通知 Render 拉最新 watchlist.json
curl -sf --max-time 30 "https://line-stock-bot-a54m.onrender.com/pull-watchlist" \
    >> log/sync.log 2>&1 \
    && echo "" >> log/sync.log \
    || echo "$(date '+%Y-%m-%d %H:%M'): ⚠️ pull-watchlist 失敗" >> log/sync.log
