#!/bin/bash
# 每個交易日 13:30 自動同步富邦持股到 watchlist.json

DOW=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$DOW" -ge 6 ]; then
    echo "$(date '+%Y-%m-%d %H:%M'): 週末，略過" >> "$HOME/Desktop/Claude資料區/股票財經專案/line-stock-bot/log/sync.log"
    exit 0
fi

cd "$HOME/Desktop/Claude資料區/股票財經專案/line-stock-bot" || exit 1

echo "$(date '+%Y-%m-%d %H:%M'): 開始同步持股..." >> log/sync.log

/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 sync_from_fubon.py >> log/sync.log 2>&1

echo "$(date '+%Y-%m-%d %H:%M'): 完成" >> log/sync.log
