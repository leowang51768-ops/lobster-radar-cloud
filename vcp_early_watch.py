#!/usr/bin/env python3
"""Early volatility-contraction watch only; never emits a formal buy recommendation.

Uses official TWSE/TPEx daily data already in lobster_tw_6m_prices.sqlite.
A broad 20/40-day range comparison is NOT a fully validated VCP pattern.
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

from stock_universe import filter_market_frame

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
OUTPUT = BASE / "vcp_early_watch.csv"
FULL_VCP = BASE / "vcp_candidates.csv"
FIELDS = ("date", "code", "name", "close", "near_high_20_pct",
          "range20_pct", "range40_pct", "compression_ratio",
          "volume_lots", "avg20_lots", "avg20_turnover",
          "volume_vs_avg20", "volume_note", "trend_note", "stage", "action")
MIN_LOTS = 300
MIN_TURNOVER = 30_000_000


def read_market():
    if not DB.exists():
        raise SystemExit(f"Official price database missing: {DB}")
    with sqlite3.connect(DB) as conn:
        df = pd.read_sql_query(
            "SELECT date, stock_id AS code, stock_name AS name, "
            "open, high, low, close, volume, turnover FROM prices "
            "ORDER BY stock_id, date", conn)
    df = filter_market_frame(df)
    df["date"] = pd.to_datetime(df["date"])
    for key in ("open", "high", "low", "close", "volume", "turnover"):
        df[key] = pd.to_numeric(df[key], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close", "volume", "turnover"])


def scan_one(group, trade_date):
    x = group.sort_values("date").reset_index(drop=True)
    if len(x) < 60 or x.iloc[-1].date.strftime("%Y-%m-%d") != trade_date:
        return None
    t = x.iloc[-1]
    last20 = x.iloc[-20:]
    last40 = x.iloc[-40:]
    hi20, lo20 = float(last20.high.max()), float(last20.low.min())
    hi40, lo40 = float(last40.high.max()), float(last40.low.min())
    if lo20 <= 0 or lo40 <= 0 or hi20 <= 0:
        return None
    r20 = (hi20 - lo20) / lo20
    r40 = (hi40 - lo40) / lo40
    close = float(t.close)
    if r40 <= 0 or r20 >= r40 * .60 or not hi20 * .95 <= close:
        return None
    avg20_lots = float(x.iloc[-20:].volume.mean()) / 1000
    avg20_turnover = float(x.iloc[-20:].turnover.mean())
    if avg20_lots < MIN_LOTS or avg20_turnover < MIN_TURNOVER:
        return None
    current_lots = float(t.volume) / 1000
    volume_ratio = current_lots / avg20_lots if avg20_lots > 0 else 0
    avg5_lots = float(x.iloc[-6:-1].volume.mean()) / 1000
    rising_today = current_lots > avg5_lots
    dry_today = volume_ratio < .70
    if not (dry_today or rising_today):
        return None
    ma60 = float(x.iloc[-60:].close.mean())
    stage = ("接近20日高點（未確認完整VCP）" if close <= hi20
             else "收盤高於20日高點（需核實突破）")
    return {
        "date": trade_date, "code": str(t.code), "name": str(t["name"]),
        "close": round(close, 2),
        "near_high_20_pct": round((close / hi20 - 1) * 100, 2),
        "range20_pct": round(r20 * 100, 2), "range40_pct": round(r40 * 100, 2),
        "compression_ratio": round(r20 / r40, 3),
        "volume_lots": round(current_lots),
        "avg20_lots": round(avg20_lots), "avg20_turnover": round(avg20_turnover),
        "volume_vs_avg20": round(volume_ratio, 2),
        "volume_note": "當日量縮" if dry_today else "較前5日均量增加",
        "trend_note": "收盤高於60MA" if close > ma60 else "收盤未高於60MA",
        "stage": stage,
        "action": "僅初期觀察；非完整VCP、非買點；等待獨立正式VCP階段確認",
    }


def notify(rows, trade_date):
    if not rows:
        print("No early-contraction observations; LINE skipped")
        return
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        print("LINE token missing; CSV saved, notification skipped")
        return
    # Observe max ten stocks per notification; full list remains in CSV.
    blocks = [
        f"🦞 初步波動收縮觀察｜{trade_date}\n"
        "⚠️ 不是完整VCP或正式買點，無買進推薦及績效紀錄。\n"
        f"符合初期觀察 {len(rows)} 檔，顯示前 {min(10, len(rows))} 檔。"
    ]
    for r in rows[:10]:
        blocks.append(
            f"{r['code']} {r['name']}｜收盤{r['close']}\n"
            f"20/40日振幅 {r['range20_pct']}%/{r['range40_pct']}%｜"
            f"距20日高點 {r['near_high_20_pct']}%\n"
            f"量比(20日) {r['volume_vs_avg20']}｜{r['trend_note']}\n"
            "僅觀察，等待正式型態確認"
        )
    messages, current = [], ""
    for block in blocks:
        proposal = block if not current else current + "\n\n" + block
        if len(proposal) > 4500:
            if current:
                messages.append(current)
            current = block
        else:
            current = proposal
    if current:
        messages.append(current)
    # Same broadcast interface as existing VCP scanner, max 5 messages/call.
    for offset in range(0, len(messages), 5):
        payload = json.dumps(
            {"messages": [{"type": "text", "text": m}
                          for m in messages[offset:offset + 5]]},
            ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=payload,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                print("EARLY_VCP_LINE_STATUS", response.status)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Early observation LINE failed: {exc.code}") from exc


def main():
    df = read_market()
    trade_date = df.date.max().strftime("%Y-%m-%d")
    formal_codes = set()
    if FULL_VCP.exists():
        with FULL_VCP.open(encoding="utf-8-sig", newline="") as f:
            formal_codes = {str(r["code"]) for r in csv.DictReader(f)
                            if r.get("date") == trade_date}
    rows = []
    for code, group in df.groupby("code", sort=False):
        if str(code) in formal_codes:
            continue  # Avoid duplicate LINE notifications for validated stages.
        r = scan_one(group, trade_date)
        if r:
            rows.append(r)
    rows.sort(key=lambda r: (abs(r["near_high_20_pct"]), r["compression_ratio"], r["code"]))
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"date": trade_date, "early_observations": len(rows),
                      "formal_vcp_excluded": len(formal_codes),
                      "line_displayed": min(10, len(rows))}, ensure_ascii=False))
    notify(rows, trade_date)


if __name__ == "__main__":
    main()
