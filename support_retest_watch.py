#!/usr/bin/env python3
"""Observation-only breakout support-retest scanner.

This route never creates formal recommendations and never enters performance
tracking. It watches for:
1) a prior 60-session resistance pivot with at least two touches;
2) a close breaking above that pivot 3-20 sessions ago;
3) the latest session retesting the pivot area on lower volume;
4) the latest close reclaiming/holding the pivot with a bullish turn.

The structural stop shown in the alert is one percent below the pivot.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
OUTPUT = BASE / "support_retest_watch.csv"

PIVOT_LOOKBACK = 60
PIVOT_TOUCH_BAND = 0.03
MIN_PIVOT_TOUCHES = 2
BREAKOUT_MIN_DAYS = 3
BREAKOUT_MAX_DAYS = 20
BREAKOUT_BUFFER = 0.003
BREAKOUT_MIN_VOLUME_RATIO = 1.20
RETEST_LOW_BELOW = 0.01
RETEST_LOW_ABOVE = 0.02
MAX_CLOSE_EXTENSION = 0.10
STOP_BUFFER = 0.01
MIN_AVG20_VOLUME_LOTS = 1000
MIN_AVG20_TURNOVER = 30_000_000
MA60_MAX_BELOW = 0.10
MA60_MAX_5D_DECLINE = 0.02


def read_market() -> pd.DataFrame:
    if not DB.exists():
        raise SystemExit(f"Database missing: {DB}")
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT date, market, stock_id AS code, stock_name AS name, "
            "open, high, low, close, volume, turnover "
            "FROM prices ORDER BY stock_id, date",
            con,
        )
    finally:
        con.close()
    for col in ("open", "high", "low", "close", "volume", "turnover"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df["volume_lots"] = df["volume"] / 1000.0
    return df.dropna(subset=["open", "high", "low", "close"])


def prepare(group: pd.DataFrame) -> pd.DataFrame:
    x = group.sort_values("date").reset_index(drop=True).copy()
    x["avg20_lots"] = x["volume_lots"].rolling(20).mean()
    x["avg20_turnover"] = x["turnover"].rolling(20).mean()
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma60"] = x["close"].rolling(60).mean()
    return x


def trend_and_liquidity_ok(x: pd.DataFrame, i: int) -> bool:
    if i < 65:
        return False
    row = x.iloc[i]
    ma60 = float(row.ma60)
    old_ma60 = float(x.iloc[i - 5].ma60)
    ma_change = ma60 / old_ma60 - 1.0 if old_ma60 > 0 else -1.0
    return bool(
        math.isfinite(ma60)
        and float(row.close) >= ma60 * (1.0 - MA60_MAX_BELOW)
        and ma_change >= -MA60_MAX_5D_DECLINE
        and float(row.avg20_lots) >= MIN_AVG20_VOLUME_LOTS
        and float(row.avg20_turnover) >= MIN_AVG20_TURNOVER
    )


def resistance_pivot(x: pd.DataFrame, break_i: int) -> tuple[float, int] | None:
    start = max(0, break_i - PIVOT_LOOKBACK)
    window = x.iloc[start:break_i]
    if len(window) < 20:
        return None
    highest = float(window["high"].max())
    touched = window[window["high"] >= highest * (1.0 - PIVOT_TOUCH_BAND)]
    if len(touched) < MIN_PIVOT_TOUCHES:
        return None
    # The old range ceiling itself becomes the post-breakout support pivot.
    return highest, int(len(touched))


def close_location(row: pd.Series) -> float:
    spread = float(row.high) - float(row.low)
    return (float(row.close) - float(row.low)) / spread if spread > 0 else 1.0


def classify_latest(group: pd.DataFrame) -> dict | None:
    x = prepare(group)
    i = len(x) - 1
    if not trend_and_liquidity_ok(x, i):
        return None

    today = x.iloc[i]
    close = float(today.close)
    selected = None
    first = max(20, i - BREAKOUT_MAX_DAYS)
    last = i - BREAKOUT_MIN_DAYS
    for break_i in range(last, first - 1, -1):
        pivot_data = resistance_pivot(x, break_i)
        if pivot_data is None:
            continue
        pivot, touches = pivot_data
        breakout = x.iloc[break_i]
        prior = x.iloc[break_i - 1]
        prior5_volume = float(x.iloc[break_i - 5:break_i]["volume_lots"].mean())
        breakout_ratio = (
            float(breakout.volume_lots) / prior5_volume if prior5_volume > 0 else 0.0
        )
        crossed = (
            float(breakout.close) >= pivot * (1.0 + BREAKOUT_BUFFER)
            and float(prior.close) <= pivot * (1.0 + BREAKOUT_BUFFER)
        )
        efficient = close_location(breakout) >= 0.60
        if not (crossed and efficient and breakout_ratio >= BREAKOUT_MIN_VOLUME_RATIO):
            continue

        post = x.iloc[break_i + 1:i + 1]
        held_between = bool(
            len(post) >= 1 and
            (post["close"] >= pivot * (1.0 - RETEST_LOW_BELOW)).all()
        )
        low = float(today.low)
        touched = (
            low >= pivot * (1.0 - RETEST_LOW_BELOW)
            and low <= pivot * (1.0 + RETEST_LOW_ABOVE)
        )
        reclaimed = close >= pivot
        turned_up = close > float(today.open) or close > float(x.iloc[i - 1].close)
        volume_contracts = float(today.volume_lots) < float(breakout.volume_lots)
        extension = close / pivot - 1.0
        if not (
            held_between and touched and reclaimed and turned_up
            and volume_contracts and extension <= MAX_CLOSE_EXTENSION
        ):
            continue

        precision = abs(low / pivot - 1.0)
        candidate = {
            "date": today.date.strftime("%Y-%m-%d"),
            "code": str(today.code),
            "name": str(today["name"]),
            "market": str(today.market),
            "stage": "突破後回踩支撐並收復",
            "breakout_date": breakout.date.strftime("%Y-%m-%d"),
            "close": round(close, 2),
            "retest_low": round(low, 2),
            "pivot": round(pivot, 2),
            "support_lower": round(pivot * (1.0 - RETEST_LOW_BELOW), 2),
            "support_upper": round(pivot * (1.0 + RETEST_LOW_ABOVE), 2),
            "stop_price": round(pivot * (1.0 - STOP_BUFFER), 2),
            "pivot_touches": touches,
            "breakout_volume_lots": round(float(breakout.volume_lots), 0),
            "today_volume_lots": round(float(today.volume_lots), 0),
            "volume_contraction_ratio": round(
                float(today.volume_lots) / float(breakout.volume_lots), 2
            ),
            "avg20_volume_lots": round(float(today.avg20_lots), 0),
            "avg20_turnover": round(float(today.avg20_turnover), 0),
            "ma20": round(float(today.ma20), 2),
            "ma60": round(float(today.ma60), 2),
            "close_extension_pct": round(extension * 100, 2),
            "retest_precision_pct": round(precision * 100, 2),
            "status": "僅觀察，不是買進通知",
            "invalidation": f"收盤跌破樞紐下方1%（{pivot * (1.0 - STOP_BUFFER):.2f}）失效",
        }
        if selected is None or precision < selected["_precision"]:
            candidate["_precision"] = precision
            selected = candidate

    if selected is not None:
        selected.pop("_precision", None)
    return selected


def send_line(rows: list[dict], trade_date: str) -> None:
    force = os.environ.get("SUPPORT_RETEST_FORCE_NOTIFY", "").strip() == "1"
    if not rows and not force:
        print("No support-retest observations; LINE skipped")
        return
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        print("LINE token missing; observations written to CSV only")
        return

    lines = [
        f"👀 龍蝦雷達｜上漲回踩支撐觀察｜{trade_date}",
        "僅觀察，不是買進訊號；不寫入正式推薦與績效追蹤。",
    ]
    if not rows:
        lines.append("✅ LINE測試成功｜目前無符合條件個股")
    for rank, row in enumerate(rows[:10], 1):
        lines += [
            "",
            f"{rank}. {row['code']} {row['name']}｜{row['stage']}",
            f"收盤{row['close']}｜回踩低點{row['retest_low']}｜原突破樞紐{row['pivot']}",
            f"支撐區{row['support_lower']}～{row['support_upper']}｜防守價{row['stop_price']}",
            f"突破量{int(row['breakout_volume_lots'])}張｜今日量{int(row['today_volume_lots'])}張｜縮量比{row['volume_contraction_ratio']}",
            f"判定：守住並收復支撐，等待後續量價轉強；{row['status']}",
            f"失效：{row['invalidation']}",
        ]

    text = "\n".join(lines)
    payload = json.dumps(
        {"messages": [{"type": "text", "text": text[:4900]}]},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print("Support-retest LINE status:", response.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Support-retest LINE failed: {exc.code} {detail}") from exc


def main() -> int:
    market = read_market()
    rows = []
    for _, group in market.groupby("code", sort=False):
        result = classify_latest(group)
        if result:
            rows.append(result)
    rows.sort(key=lambda r: (
        r["retest_precision_pct"],
        r["volume_contraction_ratio"],
        -r["pivot_touches"],
        r["code"],
    ))

    fields = [
        "date", "code", "name", "market", "stage", "breakout_date", "close",
        "retest_low", "pivot", "support_lower", "support_upper", "stop_price",
        "pivot_touches", "breakout_volume_lots", "today_volume_lots",
        "volume_contraction_ratio", "avg20_volume_lots", "avg20_turnover",
        "ma20", "ma60", "close_extension_pct", "retest_precision_pct",
        "status", "invalidation",
    ]
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    trade_date = market.date.max().strftime("%Y-%m-%d")
    print(json.dumps({
        "latest_trade_date": trade_date,
        "support_retest_observations": len(rows),
        "formal_recommendations_changed": False,
    }, ensure_ascii=False))
    send_line(rows, trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
