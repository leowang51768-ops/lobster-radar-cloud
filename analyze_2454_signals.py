import json
import os
import urllib.request
import pandas as pd
import yfinance as yf

# 1. 完整 70 檔上市/上櫃監控清單
STOCK_LIST = [
    # 半導體與代工 / 封測 / CoWoS
    "2330.TW", "2454.TW", "2303.TW", "3711.TW", "3131.TWO", "6187.TWO", "3583.TW", "6223.TWO",
    "6257.TW", "2449.TW", "3264.TWO", "3034.TW", "2379.TW", "3035.TW", "3661.TW", "3443.TW",
    # 散熱模組 / 機殼 / 伺服器組裝
    "3653.TW", "3017.TW", "2421.TW", "3324.TWO", "6230.TW", "2317.TW", "2382.TW", "3231.TW",
    "2356.TW", "6669.TW", "2376.TW", "2357.TW", "3706.TW",
    # CCL 銅箔基板 / PCB / 軟板
    "2383.TW", "6213.TW", "8358.TWO", "3037.TW", "3189.TW", "8046.TW", "2368.TW", "3044.TW",
    "4958.TW", "2313.TW", "6153.TW",
    # 被動元件 / 電源 / 記憶體 / 被動組件
    "2327.TW", "2456.TW", "2308.TW", "6282.TW", "2408.TW", "3006.TW", "2451.TW", "3260.TWO",
    # 重電 / 綠能 / 機器人 / 自動化 / 其他重點個股
    "1519.TW", "1503.TW", "1513.TW", "1514.TW", "2359.TW", "4562.TW", "2354.TW", "2059.TW",
    "6176.TW", "3376.TW", "2404.TW", "6414.TW", "3533.TW", "6409.TW", "3081.TWO", "3529.TWO",
    "5269.TW", "6415.TW", "5274.TWO", "8454.TW", "9910.TW", "2204.TW", "2201.TW"
]

buy_signals = []
exit_signals = []
scanned_count = 0

print(f"開始掃描 {len(STOCK_LIST)} 檔股票（執行回測關鍵 K 突破策略）...")

for symbol in STOCK_LIST:
    try:
        df = yf.download(symbol, period="120d", progress=False)
        if df.empty or len(df) < 40:
            continue
        
        scanned_count += 1
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df['MA30'] = df['Close'].rolling(window=30).mean()
        code = symbol.replace('.TW', '').replace('.TWO', '')

        # -------------------------------------------------------------
        # 出場條件判斷：連續 3 根 K 棒最低價低於 30MA
        # -------------------------------------------------------------
        if len(df) >= 3:
            exit_c1 = df['Low'].iloc[-3] < df['MA30'].iloc[-3]
            exit_c2 = df['Low'].iloc[-2] < df['MA30'].iloc[-2]
            exit_c3 = df['Low'].iloc[-1] < df['MA30'].iloc[-1]
            if exit_c1 and exit_c2 and exit_c3:
                exit_signals.append({
                    "code": code,
                    "close": round(float(df['Close'].iloc[-1]), 2),
                    "ma30": round(float(df['MA30'].iloc[-1]), 2)
                })

        # -------------------------------------------------------------
        # 買入條件判斷：
        # 1. 突破：先前連續 3 天收盤價站上 30MA
        # 2. 回測：後續回測跌向該 3 天最低點 (base_low)
        # 3. 關鍵K：3天內重新站上 30MA
        # 4. 觸發：新 K 棒收盤價突破關鍵 K 最高價
        # -------------------------------------------------------------
        n = len(df)
        for i in range(30, n - 4):
            # 條件 1：連 3 天站上 30MA
            if (df['Close'].iloc[i-2] > df['MA30'].iloc[i-2] and
                df['Close'].iloc[i-1] > df['MA30'].iloc[i-1] and
                df['Close'].iloc[i] > df['MA30'].iloc[i]):
                
                base_low = min(df['Low'].iloc[i-2], df['Low'].iloc[i-1], df['Low'].iloc[i])
                
                # 尋找後續回測跌向 base_low 的位置
                for j in range(i + 1, min(i + 15, n - 1)):
                    if df['Low'].iloc[j] <= base_low * 1.01: # 觸碰或接近前低
                        
                        # 尋找回測後 3 天內重新站上 30MA 的「關鍵 K」
                        for k in range(j, min(j + 3, n - 1)):
                            if df['Close'].iloc[k] > df['MA30'].iloc[k]:
                                key_k_high = df['High'].iloc[k]
                                key_k_low = df['Low'].iloc[k]
                                
                                # 檢視後續（含最新一根）是否有收盤突破關鍵 K 最高價
                                latest_close = df['Close'].iloc[-1]
                                if latest_close > key_k_high and (k == n - 2 or k == n - 1):
                                    buy_signals.append({
                                        "code": code,
                                        "close": round(float(latest_close), 2),
                                        "key_high": round(float(key_k_high), 2),
                                        "stop_loss_k": round(float(key_k_low), 2),
                                        "stop_loss_ma": round(float(df['MA30'].iloc[-1]), 2)
                                    })
                                break
                        break

    except Exception as e:
        print(f"處理 {symbol} 時發生錯誤: {e}")

# 寫回 JSON
out = {
    "total_scanned": scanned_count,
    "buy_signals": buy_signals,
    "exit_signals": exit_signals
}

with open('analysis_2454_signals.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

# 發送 LINE 推播訊息
token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
user_id = os.environ.get("LINE_USER_ID")

if token and user_id:
    token = token.strip()
    user_id = user_id.strip()

    msg_lines = ["🚨 【30MA 回測關鍵 K 突破策略】"]
    msg_lines.append(f"📊 成功掃描標的：{scanned_count} / {len(STOCK_LIST)} 檔\n")
    
    # 買入訊號區
    if buy_signals:
        msg_lines.append(f"📈 【買入訊號】共 {len(buy_signals)} 檔符合突破關鍵股價：")
        for item in buy_signals[:10]:
            msg_lines.append(f"• {item['code']} | 收盤: {item['close']}")
            msg_lines.append(f"  └ 關鍵股價: {item['key_high']} | 停損(K低): {item['stop_loss_k']} | 停損(30MA): {item['stop_loss_ma']}")
    else:
        msg_lines.append("📈 【買入訊號】：今日無標的符合回測後突破關鍵股價條件。")

    # 出場警告區
    if exit_signals:
        msg_lines.append(f"\n⚠️ 【出場警告】共 {len(exit_signals)} 檔連3K低於30MA：")
        for item in exit_signals[:10]:
            msg_lines.append(f"• {item['code']} | 收盤: {item['close']} (30MA: {item['ma30']})")

    text_payload = "\n".join(msg_lines)

    url = "https://api.line.me/v2/bot/message/push"
    payload = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": text_payload}]
    }).encode("utf-8")
    
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"LINE push success: Status {resp.status}")
    except Exception as e:
        print(f"LINE push failed: {e}")
