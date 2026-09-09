🦞 龍蝦雷達本機版 — 第一步使用說明

你目前已經完成：
1. C:\LobsterRadar
2. backup
3. logs
4. venv
5. pandas / yfinance / requests

把這個壓縮檔內的檔案解壓縮到：
C:\LobsterRadar

解壓後應該看到：
C:\LobsterRadar\
  backup\
  logs\
  venv\
  lobster_update.py
  啟動龍蝦雷達.cmd
  檢查龍蝦環境.cmd
  README.txt

第一次先雙擊：
檢查龍蝦環境.cmd

確認沒有 ERROR 後，再雙擊：
啟動龍蝦雷達.cmd

第一次執行會：
- 自動建立 lobster_radar.db
- 自動建立 progress.json
- 抓取上市櫃股票主檔
- 每次處理 40 檔
- 寫入最近約 6 個月日K
- 計算 20MA
- 計算 MACD(6,13,9)
- 失敗股票放入 failed_list
- 下次續跑，不重頭開始
- 每次執行前備份資料庫到 backup
- 執行紀錄寫到 logs

重要：
目前這是「第一階段資料庫骨架＋日K技術資料建置」。
資料庫內已預先建立法人、融資、基本面、四要點、5/10/20日績效資料表，
但這些欄位的「官方資料抓取器」會在下一階段接上。
這樣做的目的，是先讓最重要的持久化主檔與續傳機制穩定，不會重頭來。
