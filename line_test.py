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

    payload = {
        "messages": [
            {
                "type": "text",
                "text": "🦞 龍蝦雷達 LINE 通知測試成功！\nGitHub Actions 已可透過 Messaging API 發送通知。"
            }
        ]
    }

    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("LINE API status:", resp.status)
            print(resp.read().decode("utf-8", errors="replace"))
            return 0

    except urllib.error.HTTPError as e:
        print("LINE API HTTP error:", e.code)
        print(e.read().decode("utf-8", errors="replace"))
        return 1

    except Exception as e:
        print("LINE API error:", repr(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
