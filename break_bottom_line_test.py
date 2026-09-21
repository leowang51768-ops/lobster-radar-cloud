import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        print("ERROR: LINE_CHANNEL_ACCESS_TOKEN is not set")
        return 2

    text = """🦞 龍蝦雷達｜破底翻新版格式測試
⚠️ 以下為示意數據，非買進推薦

🟢 0000 測試股｜破底翻
階段：C點站回｜早期試單
A點支撐：100.0～101.0
B點低點：96.0（假跌破最低點）
C點收盤：102.0（3日內站回A點）
頸線：108.0｜尚未有效突破
底底高：等待回檔確認
MACD DIF收斂：是
60MA保護：通過
成交量門檻：大於300張｜通過
成交額門檻：3,000萬元｜通過
試單基準：101.5
確認加碼：回檔低點高於B點＋收盤有效突破108.0
失敗警訊：重新跌破A點
結構停損：再破B點，低於95.9

✅ 新版會區分「早期試單」與「確認加碼」"""

    payload = {"messages": [{"type": "text", "text": text}]}
    request = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print("LINE API status:", response.status)
            print(response.read().decode("utf-8", errors="replace"))
            return 0
    except urllib.error.HTTPError as error:
        print("LINE API HTTP error:", error.code)
        print(error.read().decode("utf-8", errors="replace"))
        return 1
    except Exception as error:
        print("LINE API error:", repr(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
