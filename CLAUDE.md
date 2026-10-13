# LINE 股票分析 Bot｜專案設定

## 基本
- GitHub：`https://github.com/ren920307/line-stock-bot`
- 部署：Render（免費，24 小時運行）
- 網址：`https://line-stock-bot-a54m.onrender.com`
- Webhook：`https://line-stock-bot-a54m.onrender.com/webhook`
- 資料來源：歷史 K 線用 twstock、即時報價用 Fugle、深度分析爬鉅亨新聞

## 指令
- `#股票名稱或代號`：技術分析（右側+SMC，免費）
- `##股票名稱或代號`：Claude Sonnet 深度分析＋爬新聞（花 token）
- `#大盤`：加權指數今日表現
- `#持股`：未平倉持股即時報價
- `#掃描`：觀察清單強勢篩選（即時）

## 設定檔與部署規則
- 觀察清單與持股：`watchlist.json`
- **`watchlist.json` 修改後必須 `git add watchlist.json && git commit && git push`**，Render 用 git 版本
- 排程：掃描、週報已走 GitHub Actions；健檢（16:00）與宇宙清單（09:00）待遷移至 GitHub Actions
- 本機持股同步：weekday 15:00 launchd（com.ren.stock-sync）跑 `run_sync_launcher.py`（Mac 須開著）

## 憑證
LINE token / Anthropic / Fugle 的 keychain 取用方式見全域 CLAUDE.md。

## 語言
全部繁體中文。
