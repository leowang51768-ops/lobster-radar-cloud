import os
import sqlite3
import pandas as pd
import requests

# ---------------------------------------------------------
# 1. 設定與環境變數
# ---------------------------------------------------------
DB_PATH = "lobster_tw_6m_prices.sqlite"
LINE_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# 70 檔自選股清單（範例標的，可自行擴充或修改）
WATCH_LIST = [
    "2330", "2317", "2454", "2308", "2382", "3231", "2356", "6669", "3017", "3661",
    "3443", "6187", "3583", "8054", "2368", "8358", "2408", "3034", "2379", "3035"
]

# ---------------------------------------------------------
# 2. 資料庫讀取函式
# ---------------------------------------------------------
def load_market_data():
    if not os.path.exists(DB_PATH):
        print(f"錯誤：找不到資料庫 {DB_PATH}")
        return None
    
    conn = sqlite3.connect(DB_PATH)
    # 假設 SQLite 資料表名稱為 daily_prices，包含股票代碼、日期、開高低收與成交量
    query = """
        SELECT stock_id, date, open, high, low, close, volume 
        FROM daily_prices 
        ORDER BY stock_id, date ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df

# ---------------------------------------------------------
# 3. 策略判定模組
# ---------------------------------------------------------
def analyze_stock(stock_id, group_df):
    """針對單檔股票計算 30 均線突破與技術指標"""
    if len(group_df) < 35:
        return None

    df = group_df.copy().reset_index(drop=True)
    df["MA30"] = df["close"].rolling(window=30).mean()

    signals = []

    # --- 模組 A：30 均線洗盤突破策略 ---
    # 找尋最近 20 天內是否有觸發訊號
    for i in range(len(df) - 5, len(df)):
        if i < 33:
            continue
        
        # 條件 1：連續 3 根 K 棒收盤價站上 30 均線
        c1 = df.loc[i-2, "close"] > df.loc[i-2, "MA30"]
        c2 = df.loc[i-1, "close"] > df.loc[i-1, "MA30"]
        c3 = df.loc[i, "close"] > df.loc[i, "MA30"]

        # 條件 2：此前原先持續在 30 均線下方（檢查前 3~5 根中有跌破 MA30）
        prior_below = (df.loc[i-5:i-3, "close"] < df.loc[i-5:i-3, "MA30"]).any()

        if c1 and c2 and c3 and prior_below:
            trigger_price = min(df.loc[i-2, "low"], df.loc[i-1, "low"], df.loc[i, "low"])
            
            # 檢查後續是否有關鍵 K 觸及洗盤價，且最新收盤價突破關鍵 K 高點
            for j in range(i + 1, len(df)):
                if df.loc[j, "low"] <= trigger_price:  # 關鍵 K 觸及洗盤
                    key_high = df.loc[j, "high"]
                    key_low = df.loc[j, "low"]
                    latest_close = df.loc[len(df)-1, "close"]
                    
                    if latest_close > key_high:
                        signals.append(f"🟢 【30均洗盤突破】最新突破關鍵K高點({key_high}) | 停損設關鍵K低價({key_low})")
                        break

    # --- 模組 B：70 檔監控（以最新一根 K 棒評估破底翻/技術強勢） ---
    if stock_id in WATCH_LIST:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 簡易看漲吞噬 / 破底翻指標範例
        if latest["close"] > prev["high"] and prev["close"] < prev["open"]:
            signals.append("📈 【自選股監控】出現多頭吞噬轉強訊號")

    if signals:
        return f"📌 **{stock_id}**\n" + "\n".join(signals)
    return None

# ---------------------------------------------------------
# 4. LINE Messaging API 推播函式
# ---------------------------------------------------------
def send_line_message(message_text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("未設定 LINE Token 或 User ID，僅於console輸出結果：")
        print(message_text)
        return

    url = "[https://api.line.me/v2/bot/message/push](https://api.line.me/v2/bot/message/push)"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message_text}]
    }
    
    res = requests.post(url, headers=headers, json=payload)
    if res.status_code == 200:
        print("LINE 通知發送成功！")
    else:
        print(f"LINE 發送失敗：{res.status_code}, {res.text}")

# ---------------------------------------------------------
# 5. 主執行入口
# ---------------------------------------------------------
if __name__ == "__main__":
    print("開始讀取 SQLite 資料庫...")
    df_all = load_market_data()
    
    if df_all is not None:
        report_results = []
        grouped = df_all.groupby("stock_id")
        
        for stock_id, group in grouped:
            result = analyze_stock(stock_id, group)
            if result:
                report_results.append(result)
        
        # 彙整推播內容
        if report_results:
            final_msg = "📊 【Lobster 盤後策略雷達日報】\n\n" + "\n\n".join(report_results[:10]) # 防推播過長限制
            send_line_message(final_msg)
        else:
            print("今日無符合條件之標的訊號。")
