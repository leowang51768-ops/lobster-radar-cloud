# 分點追蹤稽核｜2026-11-23

資料日期：**2026-11-23**。報告產生時間：2026-11-25T00:28:03+08:00（台灣時間）。請先核對資料日期；舊日期的報告不代表今天採集成功。

## 今天是否收齊

| 項目 | 實際結果 |
| --- | --- |
| 當日排行股票 | 0／262檔 |
| 當日排行筆數 | 0筆 |
| 完整採集交易日 | 0 |
| 歷史明細累積 | 521,306筆 |
| 曾成功補抓的股票／分點組合 | 8694／9781組；其餘仍待補抓 |
| 仍留有補抓錯誤的組合 | 88 |
| 資料庫容量 | 99.34 MiB |

排行完整不等於歷史完整；只取得買超／賣超各前15名，未上榜不代表沒有交易。歷史回填不能算成當時已知訊號。

## 可重算的資料檢查

| 檢查 | 結果 |
| --- | --- |
| 資料庫結構 | ok |
| 163檔覆蓋及排行筆數與狀態報告一致 | 未通過 |
| 買進－賣出與淨買超不符（允許2張四捨五入差） | 0筆 |
| 當日排行缺有效官方收盤價或成交量 | 0筆 |

## 股價驗證到期狀態

| 觀察期間 | 已有後續股價 | 尚未到期或缺價格 |
| --- | --- | --- |
| 5個交易日 | 107,560筆 | 27,550筆 |
| 10個交易日 | 102,670筆 | 32,440筆 |
| 20個交易日 | 63,566筆 | 71,544筆 |

同一筆分點排行有3個觀察期間，不能當成3筆獨立成功交易。尚未到期不填零報酬；滿期後仍須看樣本數、失敗案例、同股同期基準及樣本外結果。

## 實際抽樣：核對分點數量與股價

| 股票 | 方向 | 分點 | 買進張 | 賣出張 | 淨買超張 | 成交量張 | 淨買超占比 | 收盤元 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

計算方式：淨買超占比＝淨買超張數÷當日成交量張數×100%。來源張數有四捨五入；官方原始成交量單位為股，先除以1000。

抽樣來源（可人工比對日期、分點與買賣數量）：


## 補抓錯誤與待處理項目

| 股票 | 分點 | 最後嘗試日期 | 錯誤 |
| --- | --- | --- | --- |
| 3515 | 凱基-站前 | 2026-11-23 | History date or numeric mismatch |
| 3526 | 群益金鼎-內湖 | 2026-11-23 | History date or numeric mismatch |
| 3526 | 富邦-台中 | 2026-11-23 | History date or numeric mismatch |
| 3526 | 群益金鼎 | 2026-11-23 | History date or numeric mismatch |
| 3526 | 凱基 | 2026-11-23 | History date or numeric mismatch |
| 3529 | 永豐金-匯立 | 2026-11-23 | History date or numeric mismatch |
| 3529 | 合庫證券 | 2026-11-23 | History date or numeric mismatch |
| 3529 | 統一 | 2026-11-23 | History date or numeric mismatch |
| 3532 | 第一金 | 2026-11-23 | History date or numeric mismatch |
| 3532 | 群益金鼎 | 2026-11-23 | History date or numeric mismatch |

共88組保留錯誤，上表最多顯示10組。錯誤可能是先前嘗試留下的，修正後尚待輪到重抓；不得當成零交易。

## 163檔逐檔稽核

| 股票 | 採集狀態 | 當日筆數 | 缺漏原因 |
| --- | --- | --- | --- |
| 1101 | failed | 0 | Source date missing or stale |
| 1102 | failed | 0 | Source date missing or stale |
| 1104 | failed | 0 | Source date missing or stale |
| 11044 | failed | 0 | Source date missing or stale |
| 1210 | failed | 0 | Source date missing or stale |
| 1216 | failed | 0 | Source date missing or stale |
| 1229 | failed | 0 | Source date missing or stale |
| 1301 | failed | 0 | Source date missing or stale |
| 1303 | failed | 0 | Source date missing or stale |
| 1326 | failed | 0 | Source date missing or stale |
| 1402 | failed | 0 | Source date missing or stale |
| 1409 | failed | 0 | Source date missing or stale |
| 1476 | failed | 0 | Source date missing or stale |
| 1477 | failed | 0 | Source date missing or stale |
| 1503 | failed | 0 | Source date missing or stale |
| 1504 | failed | 0 | Source date missing or stale |
| 1513 | failed | 0 | Source date missing or stale |
| 1514 | failed | 0 | Source date missing or stale |
| 1519 | failed | 0 | Source date missing or stale |
| 1560 | failed | 0 | Source date missing or stale |
| 1590 | failed | 0 | Source date missing or stale |
| 1605 | failed | 0 | Source date missing or stale |
| 1608 | failed | 0 | Source date missing or stale |
| 1609 | failed | 0 | Source date missing or stale |
| 1717 | failed | 0 | Source date missing or stale |
| 1736 | failed | 0 | Source date missing or stale |
| 1773 | failed | 0 | Source date missing or stale |
| 1802 | failed | 0 | Source date missing or stale |
| 1809 | failed | 0 | Source date missing or stale |
| 1810 | failed | 0 | Source date missing or stale |
| 1904 | failed | 0 | Source date missing or stale |
| 1905 | failed | 0 | Source date missing or stale |
| 1907 | failed | 0 | Source date missing or stale |
| 2002 | failed | 0 | Source date missing or stale |
| 2006 | failed | 0 | Source date missing or stale |
| 2027 | failed | 0 | Source date missing or stale |
| 2049 | failed | 0 | Source date missing or stale |
| 2059 | failed | 0 | Source date missing or stale |
| 2101 | failed | 0 | Source date missing or stale |
| 2103 | failed | 0 | Source date missing or stale |
| 2105 | failed | 0 | Source date missing or stale |
| 2106 | failed | 0 | Source date missing or stale |
| 2206 | failed | 0 | Source date missing or stale |
| 2207 | failed | 0 | Source date missing or stale |
| 2258 | failed | 0 | Source date missing or stale |
| 2301 | failed | 0 | Source date missing or stale |
| 2303 | failed | 0 | Source date missing or stale |
| 2308 | failed | 0 | Source date missing or stale |
| 2312 | failed | 0 | Source date missing or stale |
| 2313 | failed | 0 | Source date missing or stale |
| 2317 | failed | 0 | Source date missing or stale |
| 2324 | failed | 0 | Source date missing or stale |
| 2327 | failed | 0 | Source date missing or stale |
| 2330 | failed | 0 | Source date missing or stale |
| 2344 | failed | 0 | Source date missing or stale |
| 2345 | failed | 0 | Source date missing or stale |
| 2347 | failed | 0 | Source date missing or stale |
| 2355 | failed | 0 | Source date missing or stale |
| 2356 | failed | 0 | Source date missing or stale |
| 2357 | failed | 0 | Source date missing or stale |
| 2359 | failed | 0 | Source date missing or stale |
| 2360 | failed | 0 | Source date missing or stale |
| 2368 | failed | 0 | Source date missing or stale |
| 2369 | failed | 0 | Source date missing or stale |
| 2376 | failed | 0 | Source date missing or stale |
| 2377 | failed | 0 | Source date missing or stale |
| 2379 | failed | 0 | Source date missing or stale |
| 2382 | failed | 0 | Source date missing or stale |
| 2383 | failed | 0 | Source date missing or stale |
| 2385 | failed | 0 | Source date missing or stale |
| 2388 | failed | 0 | Source date missing or stale |
| 2395 | failed | 0 | Source date missing or stale |
| 2404 | failed | 0 | Source date missing or stale |
| 2408 | failed | 0 | Source date missing or stale |
| 2409 | failed | 0 | Source date missing or stale |
| 2412 | failed | 0 | Source date missing or stale |
| 2421 | failed | 0 | Source date missing or stale |
| 2441 | failed | 0 | Source date missing or stale |
| 2449 | failed | 0 | Source date missing or stale |
| 2454 | failed | 0 | Source date missing or stale |
| 2464 | failed | 0 | Source date missing or stale |
| 2467 | failed | 0 | Source date missing or stale |
| 2474 | failed | 0 | Source date missing or stale |
| 2480 | failed | 0 | Source date missing or stale |
| 2542 | failed | 0 | Source date missing or stale |
| 2597 | failed | 0 | Source date missing or stale |
| 2603 | failed | 0 | Source date missing or stale |
| 2610 | failed | 0 | Source date missing or stale |
| 2615 | failed | 0 | Source date missing or stale |
| 2618 | failed | 0 | Source date missing or stale |
| 2704 | failed | 0 | Source date missing or stale |
| 2707 | failed | 0 | Source date missing or stale |
| 2727 | failed | 0 | Source date missing or stale |
| 2881 | failed | 0 | Source date missing or stale |
| 2882 | failed | 0 | Source date missing or stale |
| 2891 | failed | 0 | Source date missing or stale |
| 2903 | failed | 0 | Source date missing or stale |
| 2912 | failed | 0 | Source date missing or stale |
| 2915 | failed | 0 | Source date missing or stale |
| 3006 | failed | 0 | Source date missing or stale |
| 3008 | failed | 0 | Source date missing or stale |
| 3013 | failed | 0 | Source date missing or stale |
| 3014 | failed | 0 | Source date missing or stale |
| 3015 | failed | 0 | Source date missing or stale |
| 3017 | failed | 0 | Source date missing or stale |
| 3029 | failed | 0 | Source date missing or stale |
| 3032 | failed | 0 | Source date missing or stale |
| 3035 | failed | 0 | Source date missing or stale |
| 3036 | failed | 0 | Source date missing or stale |
| 3037 | failed | 0 | Source date missing or stale |
| 3044 | failed | 0 | Source date missing or stale |
| 3045 | failed | 0 | Source date missing or stale |
| 3047 | failed | 0 | Source date missing or stale |
| 3071 | failed | 0 | Source date missing or stale |
| 3078 | failed | 0 | Source date missing or stale |
| 3081 | failed | 0 | Source date missing or stale |
| 3086 | failed | 0 | Source date missing or stale |
| 3105 | failed | 0 | Source date missing or stale |
| 3131 | failed | 0 | Source date missing or stale |
| 3163 | failed | 0 | Source date missing or stale |
| 3189 | failed | 0 | Source date missing or stale |
| 3217 | failed | 0 | Source date missing or stale |
| 3231 | failed | 0 | Source date missing or stale |
| 3234 | failed | 0 | Source date missing or stale |
| 3260 | failed | 0 | Source date missing or stale |
| 3289 | failed | 0 | Source date missing or stale |
| 3293 | failed | 0 | Source date missing or stale |
| 3324 | failed | 0 | Source date missing or stale |
| 3325 | failed | 0 | Source date missing or stale |
| 3338 | failed | 0 | Source date missing or stale |
| 3363 | failed | 0 | Source date missing or stale |
| 3374 | failed | 0 | Source date missing or stale |
| 3380 | failed | 0 | Source date missing or stale |
| 3406 | failed | 0 | Source date missing or stale |
| 3443 | failed | 0 | Source date missing or stale |
| 3481 | failed | 0 | Source date missing or stale |
| 3483 | failed | 0 | Source date missing or stale |
| 3515 | failed | 0 | Source date missing or stale |
| 3526 | failed | 0 | Source date missing or stale |
| 3529 | failed | 0 | Source date missing or stale |
| 3532 | failed | 0 | Source date missing or stale |
| 3533 | failed | 0 | Source date missing or stale |
| 3540 | failed | 0 | Source date missing or stale |
| 3580 | failed | 0 | Source date missing or stale |
| 3583 | failed | 0 | Source date missing or stale |
| 3587 | failed | 0 | Source date missing or stale |
| 3605 | failed | 0 | Source date missing or stale |
| 3653 | failed | 0 | Source date missing or stale |
| 3660 | failed | 0 | Source date missing or stale |
| 3661 | failed | 0 | Source date missing or stale |
| 3665 | failed | 0 | Source date missing or stale |
| 3680 | failed | 0 | Source date missing or stale |
| 3689 | failed | 0 | Source date missing or stale |
| 3693 | failed | 0 | Source date missing or stale |
| 3702 | failed | 0 | Source date missing or stale |
| 3706 | failed | 0 | Source date missing or stale |
| 3711 | failed | 0 | Source date missing or stale |
| 3715 | failed | 0 | Source date missing or stale |
| 4566 | failed | 0 | Source date missing or stale |
| 4585 | failed | 0 | Source date missing or stale |
| 4763 | failed | 0 | Source date missing or stale |
| 4903 | failed | 0 | Source date missing or stale |
| 4908 | failed | 0 | Source date missing or stale |
| 4938 | failed | 0 | Source date missing or stale |
| 4958 | failed | 0 | Source date missing or stale |
| 4966 | failed | 0 | Source date missing or stale |
| 4967 | failed | 0 | Source date missing or stale |
| 4977 | failed | 0 | Source date missing or stale |
| 4979 | failed | 0 | Source date missing or stale |
| 4989 | failed | 0 | Source date missing or stale |
| 5269 | failed | 0 | Source date missing or stale |
| 5274 | failed | 0 | Source date missing or stale |
| 5289 | failed | 0 | Source date missing or stale |
| 5347 | failed | 0 | Source date missing or stale |
| 5426 | failed | 0 | Source date missing or stale |
| 5434 | failed | 0 | Source date missing or stale |
| 5443 | failed | 0 | Source date missing or stale |
| 5469 | failed | 0 | Source date missing or stale |
| 5475 | failed | 0 | Source date missing or stale |
| 5478 | failed | 0 | Source date missing or stale |
| 5522 | failed | 0 | Source date missing or stale |
| 5536 | failed | 0 | Source date missing or stale |
| 5871 | failed | 0 | Source date missing or stale |
| 5880 | failed | 0 | Source date missing or stale |
| 5903 | failed | 0 | Source date missing or stale |
| 5904 | failed | 0 | Source date missing or stale |
| 6117 | failed | 0 | Source date missing or stale |
| 6125 | failed | 0 | Source date missing or stale |
| 6139 | failed | 0 | Source date missing or stale |
| 6166 | failed | 0 | Source date missing or stale |
| 6182 | failed | 0 | Source date missing or stale |
| 6187 | failed | 0 | Source date missing or stale |
| 6191 | failed | 0 | Source date missing or stale |
| 6196 | failed | 0 | Source date missing or stale |
| 6203 | failed | 0 | Source date missing or stale |
| 6214 | failed | 0 | Source date missing or stale |
| 6215 | failed | 0 | Source date missing or stale |
| 6223 | failed | 0 | Source date missing or stale |
| 6230 | failed | 0 | Source date missing or stale |
| 6239 | failed | 0 | Source date missing or stale |
| 6257 | failed | 0 | Source date missing or stale |
| 6269 | failed | 0 | Source date missing or stale |
| 6274 | failed | 0 | Source date missing or stale |
| 6275 | failed | 0 | Source date missing or stale |
| 6282 | failed | 0 | Source date missing or stale |
| 6290 | failed | 0 | Source date missing or stale |
| 6412 | failed | 0 | Source date missing or stale |
| 6414 | failed | 0 | Source date missing or stale |
| 6426 | failed | 0 | Source date missing or stale |
| 6442 | failed | 0 | Source date missing or stale |
| 6446 | failed | 0 | Source date missing or stale |
| 6462 | failed | 0 | Source date missing or stale |
| 6485 | failed | 0 | Source date missing or stale |
| 6488 | failed | 0 | The read operation timed out |
| 6505 | failed | 0 | Source date missing or stale |
| 6510 | failed | 0 | Source date missing or stale |
| 6515 | failed | 0 | Source date missing or stale |
| 6530 | failed | 0 | Source date missing or stale |
| 6531 | failed | 0 | Source date missing or stale |
| 6533 | failed | 0 | Source date missing or stale |
| 6548 | failed | 0 | Source date missing or stale |
| 6584 | failed | 0 | Source date missing or stale |
| 6640 | failed | 0 | Source date missing or stale |
| 6643 | failed | 0 | Source date missing or stale |
| 6667 | failed | 0 | Source date missing or stale |
| 6669 | failed | 0 | Source date missing or stale |
| 6684 | failed | 0 | Source date missing or stale |
| 6690 | failed | 0 | Source date missing or stale |
| 6770 | failed | 0 | Source date missing or stale |
| 6788 | failed | 0 | Source date missing or stale |
| 6789 | failed | 0 | Source date missing or stale |
| 6805 | failed | 0 | Source date missing or stale |
| 6829 | failed | 0 | Source date missing or stale |
| 6830 | failed | 0 | Source date missing or stale |
| 6919 | failed | 0 | Source date missing or stale |
| 6933 | failed | 0 | Source date missing or stale |
| 7556 | failed | 0 | Source date missing or stale |
| 7714 | failed | 0 | Source date missing or stale |
| 7765 | failed | 0 | Source date missing or stale |
| 7799 | failed | 0 | Source date missing or stale |
| 7805 | failed | 0 | Source date missing or stale |
| 8028 | failed | 0 | Source date missing or stale |
| 8039 | failed | 0 | Source date missing or stale |
| 8046 | failed | 0 | Source date missing or stale |
| 8064 | failed | 0 | Source date missing or stale |
| 8103 | failed | 0 | Source date missing or stale |
| 8110 | failed | 0 | Source date missing or stale |
| 8150 | failed | 0 | Source date missing or stale |
| 8210 | failed | 0 | Source date missing or stale |
| 8227 | failed | 0 | Source date missing or stale |
| 8299 | failed | 0 | Source date missing or stale |
| 8358 | failed | 0 | Source date missing or stale |
| 8454 | failed | 0 | Source date missing or stale |
| 8464 | failed | 0 | Source date missing or stale |
| 8926 | failed | 0 | Source date missing or stale |
| 8932 | failed | 0 | Source date missing or stale |
| 8996 | failed | 0 | Source date missing or stale |
| 9904 | failed | 0 | Source date missing or stale |
| 9910 | failed | 0 | Source date missing or stale |
| 9917 | failed | 0 | Source date missing or stale |
| 9937 | failed | 0 | Source date missing or stale |
| 9945 | failed | 0 | Source date missing or stale |

[查看原始覆蓋表](coverage.csv) · [採集狀態](status.json) · [股價驗證狀態](relationship_status.json) · [執行紀錄](https://github.com/leowang51768-ops/lobster-radar-cloud/actions)

目前沒有依此自動判定關鍵分點或變更雷達進場規則。
