# 分點追蹤稽核｜2026-10-14

資料日期：**2026-10-14**。報告產生時間：2026-10-14T18:22:06+08:00（台灣時間）。請先核對資料日期；舊日期的報告不代表今天採集成功。

## 今天是否收齊

| 項目 | 實際結果 |
| --- | --- |
| 當日排行股票 | 90／166檔 |
| 當日排行筆數 | 2,700筆 |
| 完整採集交易日 | 2 |
| 歷史明細累積 | 83,971筆 |
| 曾成功補抓的股票／分點組合 | 1400／3723組；其餘仍待補抓 |
| 仍留有補抓錯誤的組合 | 0 |
| 資料庫容量 | 20.31 MiB |

排行完整不等於歷史完整；只取得買超／賣超各前15名，未上榜不代表沒有交易。歷史回填不能算成當時已知訊號。

## 可重算的資料檢查

| 檢查 | 結果 |
| --- | --- |
| 資料庫結構 | ok |
| 163檔覆蓋及排行筆數與狀態報告一致 | 未通過 |
| 買進－賣出與淨買超不符（允許2張四捨五入差） | 0筆 |
| 當日排行缺有效官方收盤價或成交量 | 30筆 |

## 股價驗證到期狀態

| 觀察期間 | 已有後續股價 | 尚未到期或缺價格 |
| --- | --- | --- |
| 5個交易日 | 14,670筆 | 17,606筆 |
| 10個交易日 | 0筆 | 32,276筆 |
| 20個交易日 | 0筆 | 32,276筆 |

同一筆分點排行有3個觀察期間，不能當成3筆獨立成功交易。尚未到期不填零報酬；滿期後仍須看樣本數、失敗案例、同股同期基準及樣本外結果。

## 實際抽樣：核對分點數量與股價

| 股票 | 方向 | 分點 | 買進張 | 賣出張 | 淨買超張 | 成交量張 | 淨買超占比 | 收盤元 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 6531 | 買超榜 | 群益金鼎 | 120.00 | 35.00 | 85.00 | 4,649.28 | 1.83% | 923.00 |
| 6531 | 買超榜 | 永豐金證券 | 108.00 | 36.00 | 72.00 | 4,649.28 | 1.55% | 923.00 |
| 6531 | 買超榜 | 新光 | 89.00 | 19.00 | 70.00 | 4,649.28 | 1.51% | 923.00 |
| 6531 | 賣超榜 | 香港上海匯豐 | 0.00 | 222.00 | -222.00 | 4,649.28 | -4.77% | 923.00 |
| 6531 | 賣超榜 | 元大證券 | 246.00 | 459.00 | -213.00 | 4,649.28 | -4.58% | 923.00 |
| 6531 | 賣超榜 | 台灣摩根士丹利 | 212.00 | 420.00 | -208.00 | 4,649.28 | -4.47% | 923.00 |

計算方式：淨買超占比＝淨買超張數÷當日成交量張數×100%。來源張數有四捨五入；官方原始成交量單位為股，先除以1000。

抽樣來源（可人工比對日期、分點與買賣數量）：
- [6531 群益金鼎](https://concords.moneydj.com/z/zc/zco/zco0/zco0.djhtm?a=6531&b=9100&BHID=9100)
- [6531 永豐金證券](https://concords.moneydj.com/z/zc/zco/zco0/zco0.djhtm?a=6531&b=0039004100300030&BHID=9A00)
- [6531 新光](https://concords.moneydj.com/z/zc/zco/zco0/zco0.djhtm?a=6531&b=8560&BHID=8560)
- [6531 香港上海匯豐](https://concords.moneydj.com/z/zc/zco/zco0/zco0.djhtm?a=6531&b=8960&BHID=8960)
- [6531 元大證券](https://concords.moneydj.com/z/zc/zco/zco0/zco0.djhtm?a=6531&b=9800&BHID=9800)
- [6531 台灣摩根士丹利](https://concords.moneydj.com/z/zc/zco/zco0/zco0.djhtm?a=6531&b=1470&BHID=1470)

## 補抓錯誤與待處理項目

目前沒有保留的補抓錯誤。

共0組保留錯誤，上表最多顯示10組。錯誤可能是先前嘗試留下的，修正後尚待輪到重抓；不得當成零交易。

## 163檔逐檔稽核

| 股票 | 採集狀態 | 當日筆數 | 缺漏原因 |
| --- | --- | --- | --- |
| 1301 | failed | 0 | Source date missing or stale |
| 1503 | failed | 0 | Source date missing or stale |
| 1513 | ok | 30 | — |
| 1514 | ok | 30 | — |
| 1519 | failed | 0 | Source date missing or stale |
| 1560 | failed | 0 | Source date missing or stale |
| 1773 | ok | 30 | — |
| 2059 | failed | 0 | Source date missing or stale |
| 2301 | ok | 30 | — |
| 2303 | failed | 0 | Source date missing or stale |
| 2308 | ok | 30 | — |
| 2312 | failed | 0 | Source date missing or stale |
| 2313 | ok | 30 | — |
| 2317 | failed | 0 | Source date missing or stale |
| 2324 | ok | 30 | — |
| 2330 | failed | 0 | Source date missing or stale |
| 2344 | failed | 0 | Source date missing or stale |
| 2345 | failed | 0 | Source date missing or stale |
| 2355 | ok | 30 | — |
| 2356 | ok | 30 | — |
| 2357 | ok | 30 | — |
| 2359 | failed | 0 | Source date missing or stale |
| 2360 | ok | 30 | — |
| 2368 | failed | 0 | Source date missing or stale |
| 2369 | failed | 0 | Source date missing or stale |
| 2376 | failed | 0 | Source date missing or stale |
| 2377 | ok | 30 | — |
| 2379 | ok | 30 | — |
| 2382 | failed | 0 | Source date missing or stale |
| 2383 | failed | 0 | Source date missing or stale |
| 2385 | ok | 30 | — |
| 2388 | failed | 0 | Source date missing or stale |
| 2395 | ok | 30 | — |
| 2404 | ok | 30 | — |
| 2408 | failed | 0 | Source date missing or stale |
| 2421 | failed | 0 | Source date missing or stale |
| 2441 | ok | 30 | — |
| 2449 | ok | 30 | — |
| 2454 | ok | 30 | — |
| 2467 | ok | 30 | — |
| 3006 | ok | 30 | — |
| 3008 | failed | 0 | Source date missing or stale |
| 3013 | ok | 30 | — |
| 3014 | failed | 0 | Source date missing or stale |
| 3015 | ok | 30 | — |
| 3017 | ok | 30 | — |
| 3032 | ok | 30 | — |
| 3035 | ok | 30 | — |
| 3037 | ok | 30 | — |
| 3044 | ok | 30 | — |
| 3047 | ok | 30 | — |
| 3071 | failed | 0 | Source date missing or stale |
| 3078 | ok | 30 | — |
| 3081 | ok | 30 | — |
| 3131 | ok | 30 | — |
| 3163 | failed | 0 | Source date missing or stale |
| 3189 | failed | 0 | Source date missing or stale |
| 3217 | ok | 30 | — |
| 3231 | ok | 30 | — |
| 3260 | ok | 30 | — |
| 3289 | failed | 0 | Source date missing or stale |
| 3324 | ok | 30 | — |
| 3325 | ok | 30 | — |
| 3338 | ok | 30 | — |
| 3363 | failed | 0 | Source date missing or stale |
| 3374 | failed | 0 | Source date missing or stale |
| 3380 | ok | 30 | — |
| 3406 | ok | 30 | — |
| 3443 | failed | 0 | Source date missing or stale |
| 3483 | failed | 0 | Source date missing or stale |
| 3515 | failed | 0 | Source date missing or stale |
| 3526 | ok | 30 | — |
| 3529 | ok | 30 | — |
| 3532 | failed | 0 | Source date missing or stale |
| 3533 | ok | 30 | — |
| 3540 | failed | 0 | Source date missing or stale |
| 3580 | ok | 30 | — |
| 3583 | ok | 30 | — |
| 3587 | failed | 0 | Source date missing or stale |
| 3605 | ok | 30 | — |
| 3653 | ok | 30 | — |
| 3661 | ok | 30 | — |
| 3665 | failed | 0 | Source date missing or stale |
| 3680 | failed | 0 | Source date missing or stale |
| 3689 | failed | 0 | Source date missing or stale |
| 3693 | ok | 30 | — |
| 3706 | failed | 0 | Source date missing or stale |
| 3711 | failed | 0 | Source date missing or stale |
| 3715 | ok | 30 | — |
| 4566 | ok | 30 | — |
| 4585 | failed | 0 | Source date missing or stale |
| 4903 | ok | 30 | — |
| 4908 | failed | 0 | Source date missing or stale |
| 4938 | failed | 0 | Source date missing or stale |
| 4958 | failed | 0 | Source date missing or stale |
| 4966 | ok | 30 | — |
| 4967 | ok | 30 | — |
| 4977 | ok | 30 | — |
| 4979 | ok | 30 | — |
| 4989 | failed | 0 | Source date missing or stale |
| 5269 | ok | 30 | — |
| 5274 | ok | 30 | — |
| 5289 | ok | 30 | — |
| 5347 | failed | 0 | Source date missing or stale |
| 5426 | ok | 30 | — |
| 5443 | ok | 30 | — |
| 5469 | ok | 30 | — |
| 5536 | ok | 30 | — |
| 6117 | failed | 0 | Source date missing or stale |
| 6125 | ok | 30 | — |
| 6139 | ok | 30 | — |
| 6166 | failed | 0 | Source date missing or stale |
| 6182 | failed | 0 | Source date missing or stale |
| 6187 | failed | 0 | Source date missing or stale |
| 6191 | failed | 0 | Source date missing or stale |
| 6196 | ok | 30 | — |
| 6203 | failed | 0 | Source date missing or stale |
| 6215 | ok | 30 | — |
| 6223 | ok | 30 | — |
| 6230 | failed | 0 | Source date missing or stale |
| 6239 | failed | 0 | Source date missing or stale |
| 6257 | ok | 30 | — |
| 6269 | ok | 30 | — |
| 6274 | failed | 0 | Source date missing or stale |
| 6275 | ok | 30 | — |
| 6282 | ok | 30 | — |
| 6290 | failed | 0 | Source date missing or stale |
| 6412 | failed | 0 | Source date missing or stale |
| 6414 | failed | 0 | Source date missing or stale |
| 6426 | ok | 30 | — |
| 6442 | ok | 30 | — |
| 6462 | failed | 0 | Source date missing or stale |
| 6485 | ok | 30 | — |
| 6488 | failed | 0 | Source date missing or stale |
| 6510 | failed | 0 | Source date missing or stale |
| 6515 | failed | 0 | Source date missing or stale |
| 6530 | failed | 0 | Source date missing or stale |
| 6531 | ok | 30 | — |
| 6533 | ok | 30 | — |
| 6548 | failed | 0 | Source date missing or stale |
| 6584 | failed | 0 | Source date missing or stale |
| 6640 | ok | 30 | — |
| 6643 | ok | 30 | — |
| 6667 | failed | 0 | Source date missing or stale |
| 6669 | failed | 0 | Source date missing or stale |
| 6684 | ok | 30 | — |
| 6770 | failed | 0 | Source date missing or stale |
| 6788 | failed | 0 | Source date missing or stale |
| 6789 | ok | 30 | — |
| 6805 | failed | 0 | Source date missing or stale |
| 6829 | failed | 0 | Source date missing or stale |
| 6830 | failed | 0 | Source date missing or stale |
| 6933 | ok | 30 | — |
| 7556 | failed | 0 | Source date missing or stale |
| 8028 | ok | 30 | — |
| 8039 | ok | 30 | — |
| 8046 | ok | 30 | — |
| 8064 | failed | 0 | Source date missing or stale |
| 8103 | ok | 30 | — |
| 8110 | failed | 0 | Source date missing or stale |
| 8150 | failed | 0 | Source date missing or stale |
| 8210 | failed | 0 | Source date missing or stale |
| 8227 | ok | 30 | — |
| 8299 | ok | 30 | — |
| 8358 | ok | 30 | — |
| 8996 | ok | 30 | — |

[查看原始覆蓋表](coverage.csv) · [採集狀態](status.json) · [股價驗證狀態](relationship_status.json) · [執行紀錄](https://github.com/leowang51768-ops/lobster-radar-cloud/actions)

目前沒有依此自動判定關鍵分點或變更雷達進場規則。
