#!/usr/bin/env python3
"""Close-confirmed first resistance-zone breakout; independent observation route.

Only completed daily bars are used. The current candle never contributes to
its own resistance zone or baseline volume. This scanner does not rewrite
existing formal recommendation history or change the other entry strategies.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd
from stock_universe import filter_market_frame

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
OUTPUT = BASE / "resistance_breakout_watch.csv"
LOOKBACK = 60
MIN_LOTS = 300
MIN_TURNOVER = 30_000_000
MIN_VOL_RATIO = 1.2
BREAK_MARGIN = 0.005
MAX_EXTENSION = 0.05  # Report extended moves, but never label them as trial-entry observations.
FIELDS = ["date","code","name","market","status","close","resistance_lower",
          "resistance_upper","trigger_price","stop_reference","volume_lots",
          "volume_ratio","extension_pct","touches","zone_source"]


def stock_tick(price: float) -> float:
    return .01 if price < 10 else .05 if price < 50 else .1 if price < 100 else .5 if price < 500 else 1 if price < 1000 else 5


def resistance_zone(history: pd.DataFrame):
    """Cluster distinct, confirmed swing highs within 3% of each other.

    Choose nearest repeated ceiling above the previous close. Exclude the most
    recent two rows from pivot centers so a center has two completed bars on
    each side. Cluster prices rather than taking an isolated absolute high.
    """
    if len(history) < LOOKBACK:
        return None
    h = history.iloc[-LOOKBACK:].reset_index(drop=True)
    prev_close = float(h.iloc[-1].close)
    peaks = []
    for i in range(2, len(h)-2):
        price = float(h.iloc[i].high)
        if price >= float(h.iloc[i-2:i+3].high.max()):
            peaks.append((i, price))
    zones = []
    for i, price in peaks:
        matching = [z for z in zones if abs(price/z["center"]-1) <= .03]
        if matching:
            z = min(matching, key=lambda q: abs(price/q["center"]-1))
            z["points"].append((i, price))
            z["center"] = float(pd.Series([p for _,p in z["points"]]).median())
        else:
            zones.append({"center":price,"points":[(i,price)]})
    choices = []
    for z in zones:
        points = z["points"]
        if len(points) < 2 or len({i for i,_ in points}) < 2:
            continue
        lower = min(p for _,p in points)
        upper = max(p for _,p in points)
        if prev_close <= upper:  # Only a ceiling not yet cleared before today.
            choices.append((upper,lower,len(points)))
    return min(choices, key=lambda item:item[0]) if choices else None


def classify(g: pd.DataFrame):
    x = g.sort_values("date").reset_index(drop=True)
    if len(x) < LOOKBACK + 1:
        return None
    t = x.iloc[-1]
    previous = x.iloc[:-1]
    zone = resistance_zone(previous)
    if not zone:
        return None
    upper, lower, touches = zone
    close = float(t.close)
    prev_close = float(previous.iloc[-1].close)
    trigger = upper * (1 + BREAK_MARGIN)
    volume = float(t.volume)
    avg5 = float(previous.iloc[-5:].volume.mean())
    avg20_lots = float(previous.iloc[-20:].volume.mean()) / 1000
    avg20_turnover = float(previous.iloc[-20:].turnover.mean())
    ratio = volume / avg5 if avg5 > 0 else 0
    if not (prev_close <= upper and close >= trigger and float(t.high) >= trigger):
        return None
    if not (avg20_lots >= MIN_LOTS and avg20_turnover >= MIN_TURNOVER
            and volume/1000 >= MIN_LOTS and ratio >= MIN_VOL_RATIO):
        return None
    extension = close/upper - 1
    status = "當日收盤突破｜延伸過大僅觀察" if extension > MAX_EXTENSION else "當日收盤突破｜觀察"
    stop = lower * .99  # Reference only; not a guaranteed exit execution price.
    return dict(date=str(t.date)[:10],code=str(t.code),name=str(t["name"]),
                market=str(t.market),status=status,close=round(close,2),
                resistance_lower=round(lower,2),resistance_upper=round(upper,2),
                trigger_price=round(math.ceil(trigger/stock_tick(trigger))*stock_tick(trigger),2),
                stop_reference=round(stop,2),volume_lots=round(volume/1000),
                volume_ratio=round(ratio,2),extension_pct=round(extension*100,2),
                touches=touches,zone_source="前60日已完成K棒之重複波段高點")


def main():
    if not DB.exists():
        raise SystemExit(f"Missing price database: {DB}")
    with sqlite3.connect(DB) as con:
        df = pd.read_sql_query(
            "SELECT date,market,stock_id AS code,stock_name AS name,"
            "open,high,low,close,volume,turnover FROM prices ORDER BY stock_id,date",con)
    df = filter_market_frame(df)
    for field in ("open","high","low","close","volume","turnover"):
        df[field] = pd.to_numeric(df[field],errors="coerce")
    df = df.dropna(subset=["open","high","low","close","volume","turnover"])
    if df.empty:
        raise SystemExit("No usable market data")
    latest = str(df.date.max())[:10]
    rows = []
    for _, group in df.groupby("code",sort=False):
        if str(group.iloc[-1].date)[:10] != latest:
            continue
        found = classify(group)
        if found:
            rows.append(found)
    rows.sort(key=lambda r:(r["extension_pct"]>MAX_EXTENSION*100,-r["volume_ratio"],r["code"]))
    with OUTPUT.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"trade_date":latest,"resistance_breakouts":len(rows)},ensure_ascii=False))
    token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","").strip()
    if not token or not rows:
        return 0
    lines=[f"🦞 龍蝦雷達｜壓力區當日收盤突破｜{latest}",
           "獨立觀察訊號；不等回踩、不自動列入正式推薦或績效。",
           "只根據官方收盤資料判定；不是盤中即時通知。"]
    for r in rows[:10]:
        lines.append(
            f"\n{r['code']} {r['name']}｜{r['status']}"
            f"\n收盤 {r['close']}｜壓力區 {r['resistance_lower']}～{r['resistance_upper']}"
            f"\n突破門檻 {r['trigger_price']}｜成交 {r['volume_lots']}張｜5日量比 {r['volume_ratio']}x"
            f"\n距壓力上緣 +{r['extension_pct']}%｜結構停損參考 {r['stop_reference']}"
            "\n⚠️ 收盤突破仍可能是假突破；不代表隔日開盤可按收盤價買入。")
    if len(rows)>10:
        lines.append(f"另有 {len(rows)-10} 檔符合，請查看 CSV。")
    payload=json.dumps({"messages":[{"type":"text","text":"\n".join(lines)[:4900]}]},ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request("https://api.line.me/v2/bot/message/broadcast",
        data=payload,headers={"Authorization":f"Bearer {token}",
        "Content-Type":"application/json; charset=UTF-8"},method="POST")
    with urllib.request.urlopen(req,timeout=30) as response:
        print("Resistance breakout LINE status:",response.status)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
