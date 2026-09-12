#!/usr/bin/env python3
"""Lobster Radar buy-point engine.

Layer 1 (candidate qualification)
  1) 20MA rising
  2) close > 20MA
  3) DIF(6,13) today >= yesterday
  4) fundamental support approved in fundamental_support.json

Layer 2 (buy point)
  1) previously closed above 30MA for 3 consecutive sessions
  2) pullback tests the prior 3-candle low zone
  3) closes back above 30MA within at most 3 candles
  4) reclaim candle becomes the key candle
  5) later close breaks above key-candle high
  6) volume expands reasonably and price extension is not excessive

Once a stock has entered the candidate pool, Layer 2 keeps tracking its pullback /
reclaim structure even if Layer 1 temporarily weakens. A formal buy signal still
requires Layer 1 to be 4/4 on the trigger day.

Database volume is stored as shares; strategy displays and evaluates lots (張).
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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
FUNDAMENTALS = BASE / "fundamental_support.json"
STATE_FILE = BASE / "signal_state.json"
RECOMMENDATIONS = BASE / "formal_recommendations.csv"
CANDIDATES = BASE / "candidate_status.csv"

PULLBACK_TOL = 0.01
VOL_RATIO_MIN = 1.20
VOL_RATIO_MAX = 3.00
MAX_30MA_EXTENSION = 0.08
MIN_VOLUME_LOTS = 1000
MIN_TURNOVER = 30_000_000


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
    # 轉強優先辨識，避免剛上穿30MA當天被直接歸入A級。
    if (prev20 <= prev30 and ma20 > ma30) or abs(ma20 - ma30) / max(ma30, 1e-9) <= 0.005:
        return "轉強"
    if close > ma20 > ma30:
        return "A級"
    if close > ma20 and ma20 < ma30 and ma20 > prev20:
        return "B級"
    return "其他"


def read_market() -> pd.DataFrame:
    if not DB.exists():
        raise SystemExit(f"Database missing: {DB}")
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT date, market, stock_id AS code, stock_name AS name, open, high, low, close, volume, turnover "
            "FROM prices ORDER BY stock_id, date",
            con,
        )
    finally:
        con.close()
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
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
    return {
        "ok": c1 and c2 and c3 and c4,
        "checks": {"20MA向上": c1, "收盤站上20MA": c2, "DIF今日>=昨日": c3, "基本面支撐": c4},
        "trend": classify_trend(float(t.close), float(t.ma20), float(t.ma30), float(y.ma20), float(y.ma30)),
        "df": x,
    }


def detect_layer2(code: str, x: pd.DataFrame, state: dict, allow_trigger: bool = True) -> tuple[dict, dict | None]:
    s = dict(state or {})
    if len(x) < 35:
        s.setdefault("stage", "等待30MA結構")
        return s, None

    i = len(x) - 1
    t = x.iloc[i]
    date = t.date.strftime("%Y-%m-%d")
    close, low, high, ma30 = map(float, [t.close, t.low, t.high, t.ma30])

    if not s.get("three_above30_date"):
        for j in range(max(2, i - 15), i + 1):
            q = x.iloc[j-2:j+1]
            if q["ma30"].notna().all() and bool((q["close"] > q["ma30"]).all()):
                s["three_above30_date"] = x.iloc[j].date.strftime("%Y-%m-%d")
                s["stage"] = "等待回踩"
                break
        if not s.get("three_above30_date"):
            s["stage"] = "等待30MA結構"

    if s.get("three_above30_date") and not s.get("pullback_date") and i >= 3:
        prior3_low = float(x.iloc[i-3:i]["low"].min())
        if low <= prior3_low * (1 + PULLBACK_TOL):
            s["pullback_date"] = date
            s["stage"] = "等待3K內站回30MA"

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
                # 這輪型態失敗，重新等待新的30MA結構。
                return {"stage": "重新等待", "last_failed_reclaim": date}, None

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
        if allow_trigger and breakout and volume_ok and extension_ok and s.get("last_trigger_date") != date:
            s["stage"] = "正式試單"
            s["last_trigger_date"] = date
            return s, {
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


def write_candidate_status(rows: list[dict]) -> None:
    fields = [
        "date", "code", "name", "trend", "stage", "layer1_current", "close", "ma20", "ma30", "dif",
        "volume_lots", "avg20_volume_lots", "volume_ratio", "turnover",
        "three_above30_date", "pullback_date", "key_date", "key_high", "key_low",
        "fundamental_reason",
    ]
    with CANDIDATES.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
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

    candidate_rows = []
    triggers = []
    current_layer1_count = 0

    for code, g in df.groupby("code", sort=False):
        code = str(code)
        name = str(g.iloc[-1]["name"])
        f = fundamentals.get(code, {})
        fundamental_ok = bool(f.get("supported", False)) if isinstance(f, dict) else bool(f)
        l1 = layer1(g, fundamental_ok)
        l1_ok = bool(l1.get("ok"))
        previous_state = stocks_state.get(code, {})

        # 舊版本的候選狀態已有 layer1_checks；新版本則額外記 candidate_since。
        # 一旦進入候選池，即使當日 Layer 1 暫時轉弱，也繼續追蹤 Layer 2。
        was_candidate = bool(previous_state.get("candidate_since") or previous_state.get("layer1_checks"))
        if not l1_ok and not was_candidate:
            continue
        if l1_ok:
            current_layer1_count += 1

        x = l1["df"]
        if len(x) < 31:
            continue
        t = x.iloc[-1]
        avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else 0.0
        lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
        vol_ratio = lots / avg20 if avg20 else 0.0

        new_state, trigger = detect_layer2(code, x, previous_state, allow_trigger=l1_ok)
        new_state["candidate_since"] = previous_state.get("candidate_since", latest_date)
        new_state["name"] = name
        new_state["trend"] = l1.get("trend", previous_state.get("trend", "其他"))
        new_state["layer1_current"] = l1_ok
        new_state["layer1_checks"] = l1.get("checks", {})
        stocks_state[code] = new_state

        candidate_rows.append({
            "date": latest_date,
            "code": code,
            "name": name,
            "trend": new_state["trend"],
            "stage": new_state.get("stage", "等待30MA結構"),
            "layer1_current": "4/4" if l1_ok else "追蹤中",
            "close": round(float(t.close), 2),
            "ma20": round(float(t.ma20), 2),
            "ma30": round(float(t.ma30), 2),
            "dif": round(float(t.dif), 4),
            "volume_lots": round(lots, 0),
            "avg20_volume_lots": round(avg20, 0),
            "volume_ratio": round(vol_ratio, 2),
            "turnover": round(float(t.turnover), 0) if pd.notna(t.turnover) else 0,
            "three_above30_date": new_state.get("three_above30_date", ""),
            "pullback_date": new_state.get("pullback_date", ""),
            "key_date": new_state.get("key_date", ""),
            "key_high": new_state.get("key_high", ""),
            "key_low": new_state.get("key_low", ""),
            "fundamental_reason": f.get("reason", "") if isinstance(f, dict) else "",
        })

        if trigger:
            trigger.update({"name": name, "trend": new_state["trend"], "baseline_entry": trigger["key_high"]})
            append_recommendation(trigger)
            triggers.append(trigger)

    # 讓最接近買點的股票排在前面，方便人工查看。
    stage_order = {
        "正式試單": 0,
        "等待突破關鍵K": 1,
        "等待3K內站回30MA": 2,
        "等待回踩": 3,
        "等待30MA結構": 4,
        "重新等待": 5,
    }
    trend_order = {"轉強": 0, "B級": 1, "A級": 2, "其他": 3}
    candidate_rows.sort(key=lambda r: (stage_order.get(r["stage"], 9), trend_order.get(r["trend"], 9), r["code"]))
    write_candidate_status(candidate_rows)

    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["latest_trade_date"] = latest_date
    state["layer1_candidate_count"] = current_layer1_count
    state["tracked_candidate_count"] = len(candidate_rows)
    state["stage_counts"] = dict(Counter(r["stage"] for r in candidate_rows))
    state["trend_counts"] = dict(Counter(r["trend"] for r in candidate_rows))
    save_json(STATE_FILE, state)

    summary = {
        "latest_trade_date": latest_date,
        "layer1_candidates": current_layer1_count,
        "tracked_candidates": len(candidate_rows),
        "formal_buy_signals": len(triggers),
        "trend_counts": dict(Counter(r["trend"] for r in candidate_rows)),
        "stage_counts": dict(Counter(r["stage"] for r in candidate_rows)),
    }
    print(json.dumps(summary, ensure_ascii=False))

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