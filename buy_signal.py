#!/usr/bin/env python3
"""Lobster Radar buy-point engine.

Rules:
Layer 1 candidate qualification
  1) 20MA rising
  2) close > 20MA
  3) DIF(6,13) today >= yesterday
  4) fundamental support explicitly approved in fundamental_support.json

Layer 2 buy point
  1) previously closed above 30MA for 3 consecutive sessions
  2) pullback tests the prior 3-candle low zone
  3) closes back above 30MA within at most 3 candles
  4) that reclaim candle becomes the key candle
  5) later close breaks above key-candle high
  6) volume expands reasonably and price extension is not excessive
  7) institution/broker flow is bonus only (not required here)

Database volume is stored as official '成交股數' (shares). This engine converts
volume to lots by dividing by 1000 before applying all volume rules.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
FUNDAMENTALS = BASE / "fundamental_support.json"
STATE_FILE = BASE / "signal_state.json"
RECOMMENDATIONS = BASE / "formal_recommendations.csv"

# Configurable thresholds. Keep logic stable; tune these values later if desired.
PULLBACK_TOL = 0.01          # within 1% of prior 3-candle low zone
VOL_RATIO_MIN = 1.20         # breakout volume >= 1.2x 20-day average lots
VOL_RATIO_MAX = 3.00         # avoid extreme one-day blow-off volume
MAX_30MA_EXTENSION = 0.08    # close no more than 8% above 30MA at trigger
MIN_VOLUME_LOTS = 1000       # existing liquidity rule
MIN_TURNOVER = 30_000_000    # existing liquidity rule, TWD


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def classify_trend(close: float, ma20: float, ma30: float, prev20: float, prev30: float) -> str:
    if close > ma20 > ma30:
        return "A級"
    if close > ma20 and ma20 < ma30 and ma20 > prev20:
        return "B級"
    if (prev20 <= prev30 and ma20 > ma30) or abs(ma20 - ma30) / max(ma30, 1e-9) <= 0.005:
        return "轉強"
    return "其他"


def read_market() -> pd.DataFrame:
    if not DB.exists():
        raise SystemExit(f"Database missing: {DB}")
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT date, market, code, name, open, high, low, close, volume, turnover FROM prices ORDER BY code, date",
            con,
        )
    finally:
        con.close()
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    # IMPORTANT: DB stores shares, strategy works in lots.
    df["volume_lots"] = df["volume"] / 1000.0
    return df.dropna(subset=["close"])


def approved_fundamentals() -> dict:
    raw = load_json(FUNDAMENTALS, {"stocks": {}})
    return raw.get("stocks", {}) if isinstance(raw, dict) else {}


def layer1(g: pd.DataFrame, fundamental_ok: bool) -> dict:
    x = g.copy().sort_values("date").reset_index(drop=True)
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma30"] = x["close"].rolling(30).mean()
    x["ema6"] = ema(x["close"], 6)
    x["ema13"] = ema(x["close"], 13)
    x["dif"] = x["ema6"] - x["ema13"]
    x["avg20_lots"] = x["volume_lots"].rolling(20).mean()
    if len(x) < 31:
        return {"ok": False, "df": x}
    t, y = x.iloc[-1], x.iloc[-2]
    c1 = bool(t.ma20 > y.ma20)
    c2 = bool(t.close > t.ma20)
    c3 = bool(t.dif >= y.dif)
    c4 = bool(fundamental_ok)
    trend = classify_trend(float(t.close), float(t.ma20), float(t.ma30), float(y.ma20), float(y.ma30))
    return {
        "ok": c1 and c2 and c3 and c4,
        "checks": {"20MA向上": c1, "收盤站上20MA": c2, "DIF今日>=昨日": c3, "基本面支撐": c4},
        "trend": trend,
        "df": x,
    }


def detect_layer2(code: str, x: pd.DataFrame, state: dict) -> tuple[dict, dict | None]:
    """Advance deterministic state machine and return (new_state, trigger_or_none)."""
    s = dict(state or {})
    if len(x) < 35:
        return s, None

    i = len(x) - 1
    t = x.iloc[i]
    date = t.date.strftime("%Y-%m-%d")
    close, low, high, ma30 = map(float, [t.close, t.low, t.high, t.ma30])

    # Seed: any recent 3 consecutive closes above 30MA.
    if not s.get("three_above30_date"):
        for j in range(max(2, i - 15), i + 1):
            q = x.iloc[j-2:j+1]
            if q["ma30"].notna().all() and bool((q["close"] > q["ma30"]).all()):
                s["three_above30_date"] = x.iloc[j].date.strftime("%Y-%m-%d")
                s["stage"] = "等待回踩"

    # Pullback: low reaches prior three-candle low zone (+1% tolerance).
    if s.get("three_above30_date") and not s.get("pullback_date") and i >= 3:
        prior3_low = float(x.iloc[i-3:i]["low"].min())
        if low <= prior3_low * (1 + PULLBACK_TOL):
            s["pullback_date"] = date
            s["pullback_index_date"] = date
            s["stage"] = "等待3K內站回30MA"

    # Reclaim within max 3 candles from pullback (inclusive day count via row positions).
    if s.get("pullback_date") and not s.get("key_date"):
        pb_matches = x.index[x["date"].dt.strftime("%Y-%m-%d") == s["pullback_date"]].tolist()
        if pb_matches:
            pb_i = pb_matches[-1]
            elapsed = i - pb_i
            if 0 <= elapsed <= 2 and close > ma30:
                s["key_date"] = date
                s["key_high"] = high
                s["key_low"] = low
                s["stage"] = "等待突破關鍵K"
            elif elapsed > 2:
                # Pattern failed; reset but retain audit marker.
                s = {"stage": "重新等待", "last_failed_reclaim": date}
                return s, None

    # Formal breakout must occur after key candle, not on the same day.
    if s.get("key_date") and date > s["key_date"]:
        key_high = float(s["key_high"])
        avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else math.nan
        lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
        turnover = float(t.turnover) if pd.notna(t.turnover) else 0.0
        vol_ratio = lots / avg20 if avg20 and not math.isnan(avg20) else 0.0
        extension = (close / ma30 - 1.0) if ma30 else 999.0
        breakout = close > key_high
        volume_ok = VOL_RATIO_MIN <= vol_ratio <= VOL_RATIO_MAX and lots >= MIN_VOLUME_LOTS and turnover >= MIN_TURNOVER
        extension_ok = extension <= MAX_30MA_EXTENSION
        if breakout and volume_ok and extension_ok and s.get("last_trigger_date") != date:
            s["stage"] = "正式試單"
            s["last_trigger_date"] = date
            trigger = {
                "date": date,
                "code": code,
                "close": round(close, 2),
                "key_date": s["key_date"],
                "key_high": round(key_high, 2),
                "key_low": round(float(s["key_low"]), 2),
                "volume_lots": round(lots, 0),
                "volume_ratio": round(vol_ratio, 2),
                "extension_30ma_pct": round(extension * 100, 2),
            }
            return s, trigger

    return s, None


def append_recommendation(row: dict) -> None:
    fields = ["date", "code", "name", "trend", "baseline_entry", "key_date", "key_high", "key_low", "volume_lots", "volume_ratio", "extension_30ma_pct"]
    exists = RECOMMENDATIONS.exists()
    if exists:
        with RECOMMENDATIONS.open("r", encoding="utf-8-sig", newline="") as f:
            if any(r.get("date") == row["date"] and r.get("code") == row["code"] for r in csv.DictReader(f)):
                return
    with RECOMMENDATIONS.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def send_line(text: str) -> bool:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        print("LINE_CHANNEL_ACCESS_TOKEN missing; signal recorded but not pushed")
        return False
    payload = json.dumps({"messages": [{"type": "text", "text": text}]}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("LINE status", resp.status)
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        print("LINE HTTP error", e.code, e.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print("LINE error", repr(e))
    return False


def main() -> int:
    df = read_market()
    fundamentals = approved_fundamentals()
    state = load_json(STATE_FILE, {"stocks": {}, "updated_at": None})
    stocks_state = state.setdefault("stocks", {})
    latest_date = df["date"].max().strftime("%Y-%m-%d")

    layer1_candidates = []
    triggers = []
    for code, g in df.groupby("code", sort=False):
        name = str(g.iloc[-1]["name"])
        f = fundamentals.get(str(code), {})
        fundamental_ok = bool(f.get("supported", False)) if isinstance(f, dict) else bool(f)
        l1 = layer1(g, fundamental_ok)
        if not l1.get("ok"):
            continue
        layer1_candidates.append({"code": str(code), "name": name, "trend": l1["trend"]})
        new_state, trigger = detect_layer2(str(code), l1["df"], stocks_state.get(str(code), {}))
        new_state["name"] = name
        new_state["trend"] = l1["trend"]
        new_state["layer1_checks"] = l1.get("checks", {})
        stocks_state[str(code)] = new_state
        if trigger:
            trigger.update({"name": name, "trend": l1["trend"], "baseline_entry": trigger["key_high"]})
            append_recommendation(trigger)
            triggers.append(trigger)

    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["latest_trade_date"] = latest_date
    state["layer1_candidate_count"] = len(layer1_candidates)
    save_json(STATE_FILE, state)

    print(json.dumps({"latest_trade_date": latest_date, "layer1_candidates": len(layer1_candidates), "formal_buy_signals": len(triggers)}, ensure_ascii=False))

    if triggers:
        lines = [f"🦞 龍蝦雷達正式買點｜{latest_date}"]
        for r in triggers:
            lines += [
                "",
                f"🔴 {r['code']} {r['name']}｜{r['trend']}",
                "第一層：4/4通過",
                f"關鍵K：{r['key_date']}",
                f"關鍵K高：{r['key_high']}",
                f"收盤：{r['close']}",
                f"成交量：{int(r['volume_lots'])}張｜量比：{r['volume_ratio']}x",
                f"30MA乖離：{r['extension_30ma_pct']}%",
                f"績效基準試單價：{r['baseline_entry']}",
                f"初始失效參考：關鍵K低點 {r['key_low']}",
            ]
        send_line("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
