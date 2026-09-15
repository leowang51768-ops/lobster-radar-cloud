import json
import os
import urllib.request

# === 假資料與核心邏輯占位（請維持原有的計算 logic） ===
# 此處 out 變數承接原本分析腳本的產出
out = {
    "code": "2454",
    "name": "聯發科",
    "primary_key_signals": [],
    "strong20_signals": [],
    "secondary_30ma_signals": [],
    "counts": {
        "primary_key": 0,
        "strong20": 0,
        "secondary_30ma": 0,
        "total": 0
    }
}

# 存成 JSON 檔供後續步驟讀取
with open('analysis_2454_signals.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(json.dumps(out, ensure_ascii=False, indent=2))

# ==========================================
# === 修正格式後的 LINE Push 推播區塊 ===
# ==========================================
token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
user_id = os.environ.get("LINE_USER_ID")

if token and user_id:
    token = token.strip()
    user_id = user_id.strip()

    msg_lines = ["🚨 【龍蝦雷達與 30MA 訊號日報】"]
    
    # 讀取 30MA 回踩與突破訊號
    ma30_list = out.get('secondary_30ma_signals', [])
    if ma30_list:
        msg_lines.append("\n📈 【30MA 回踩/突破訊號】")
        for item in ma30_list[:5]:
            code = str(item.get('code', ''))
            name = str(item.get('name', ''))
            close = str(item.get('close', ''))
            msg_lines.append(f"• {code} {name} | 收盤: {close}")
    else:
        msg_lines.append("\n📈 【30MA 訊號】：今日無符合標的")

    text_payload = "\n".join(msg_lines)

    url = "https://api.line.me/v2/bot/message/push"
    payload = json.dumps({
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text_payload
            }
        ]
    }).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=payload,
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
else:
    print("LINE push skipped: Missing token or user_id environment variables.")
