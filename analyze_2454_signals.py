import json
import os
import urllib.request
import pandas as pd
import yfinance as yf

# 1. 定義監控的 70 檔核心股票清單
STOCK_LIST = [
    # 半導體與代工 / 封測 / CoWoS
    "2330.TW", "2454.TW", "2303.TW", "3711.TW", "3131.TW", "6187.TW", "3583.TW", "6223.TW",
    "6257.TW", "2449.TW", "3264.TWO", "3034.TW", "2379.TW", "3035.TW", "3661.TW", "3443.TW",
    
    # 散熱模組 / 機殼 / 伺服器組裝
    "3653.TW", "3017.TW", "2421.TW", "3324.TWO", "6230.TW", "2317.TW", "2382.TW", "3231.TW",
    "2356.TW", "6669.TW", "2376.TW", "2357.TW", "3706.TW",
    
    # CCL 銅箔基板 / PCB / 軟板
    "2383.TW", "6213.TW", "8358.TW", "3037.TW", "3189.TW", "8046.TW", "2368.TW", "3044.TW",
    "4958.TW", "2313.TW", "6153.TW",
    
    # 被動元件 / 電源 / 記憶體 / 被動組件
    "2327.TW", "2456.TW", "2308.TW", "6282.TW", "2408.TW", "3006.TW", "2451.TW", "3260.TWO",
    
    # 重電 / 綠能 / 機器人 / 自動化 / 其他重點個股
    "1519.TW", "1503.TW", "1513.TW", "1514.TW", "2359.TW", "4562.TW", "2354.TW", "2059.TW",
    "6176.TW", "3376.TW", "2404.TW", "6414.TW", "3533.TW", "6409.TW", "3081.TWO", "3529.TWO",
    "5269.TW", "6415.TW", "5274.TWO", "8454.TW", "9910.TW", "2204.TW", "2201.TW"
]

ma30_signals = []

print(f"開始掃描 {len(STOCK_LIST)} 檔股票數據...")

for symbol in STOCK_LIST:
    try:
        df = yf.download(symbol, period="60d", progress=False)
        if df.empty or len(df) < 30:
            continue
        
        # 處理 yfinance 的 MultiIndex 欄位結構
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 計算 30MA
        df['MA30'] = df['Close'].rolling(window=30).mean()

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close_price = float(latest['Close'])
        ma30_price = float(latest['MA30'])
        prev_close = float(prev['Close'])
        prev_ma30 = float(prev['MA30'])

        code = symbol.replace('.TW', '').replace('.TWO', '')

        # 訊號判斷：突破 30MA 或 回踩 30MA 附近（誤差 1.5% 內）
        is_breakthrough = (prev_close < prev_ma30) and (close_price >= ma30_price)
        is_retest_support = (close_price >= ma30_price) and (abs(close_price - ma30_price) / ma30_price <= 0.015)

        if is_breakthrough or is_retest_support:
            signal_type = "突破30MA" if is_breakthrough else "回踩30MA"
            ma30_signals.append({
                "code": code,
                "type": signal_type,
                "close": round(close_price, 2),
                "ma30": round(ma30_price, 2)
            })
    except Exception as e:
        print(f"處理 {symbol} 時發生錯誤: {e}")

# 將結果寫回 JSON
out = {
    "total_scanned": len(STOCK_LIST),
    "secondary_30ma_signals": ma30_signals
}

with open('analysis_2454_signals.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

# 發送 LINE 綜合推播
token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
user_id = os.environ.get("LINE_USER_ID")

if token and user_id:
    token = token.strip()
    user_id = user_id.strip()

    msg_lines = ["🚨 【龍蝦雷達與 30MA 全標的訊號日報】"]
    
    if ma30_signals:
        msg_lines.append(f"\n📈 【今日觸發 30MA 訊號共 {len(ma30_signals)} 檔】")
        # 避免 LINE 訊息過長，最多顯示前 15 檔精選標的
        for item in ma30_signals[:15]:
            msg_lines.append(f"• {item['code']} | {item['type']} | 收盤: {item['close']} (30MA: {item['ma30']})")
        
        if len(ma30_signals) > 15:
            msg_lines.append(f"\n*(其餘 {len(ma30_signals) - 15} 檔已寫回 GitHub JSON 資料庫)*")
    else:
        msg_lines.append("\n📈 【30MA 訊號】：今日 70 檔監控標的均無觸發訊號")

    url = "https://api.line.me/v2/bot/message/push"
    payload = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": "\n".join(msg_lines)}]
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
