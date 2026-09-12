#!/usr/bin/env python3
"""Lobster Radar buy-point engine.

Candidate routes
A) Original four-point rule.
B) Strong 20MA continuation: pullback tests 20MA without closing below it,
   then DIF turns stronger while the strong structure remains intact.

Formal buy routes
A) 30MA pullback -> reclaim -> key candle -> later breakout with qualified volume.
B) Strong 20MA continuation -> DIF recovery key candle -> later breakout with qualified volume.

Tracking invalidation
- after the first close below 20MA, allow the next 3 trading sessions to reclaim 20MA;
  invalidate only if all 3 grace sessions also close below 20MA;
- DIF weakens for 3 consecutive day-over-day steps.

Key-candle policy
- notify LINE immediately when a 30MA or strong-20MA key candle is formed;
- do not auto-invalidate merely because a key candle has not broken out within 3 days;
- formal breakout rules remain as a separate confirmation signal.
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
STRONG_20MA_TOUCH_TOL = 0.01
STRONG_20MA_LOOKBACK = 3
VOL_RATIO_MIN = 1.20
VOL_RATIO_MAX = 3.00
MAX_30MA_EXTENSION = 0.08
MAX_20MA_EXTENSION = 0.08
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
        return {"ok": False, "standard_ok": False, "strong20_ok": False, "route": "", "df": x}

    t, y = x.iloc[-1], x.iloc[-2]
    c1 = bool(t.ma20 > y.ma20)
    c2 = bool(t.close > t.ma20)
    c3 = bool(t.dif >= y.dif)
    c4 = bool(fundamental_ok)
    standard_ok = c1 and c2 and c3 and c4

    strong20_touch_date = ""
    strong20_ok = False
    if len(x) >= 34 and c2 and c3 and c4 and bool(t.ma20 > t.ma30):
        for back in range(1, STRONG_20MA_LOOKBACK + 1):
            p = x.iloc[-1 - back]
            if pd.isna(p.ma20) or pd.isna(p.ma30):
                continue
            strong_structure = bool(p.ma20 > p.ma30)
            touched_20ma = bool(float(p.low) <= float(p.ma20) * (1 + STRONG_20MA_TOUCH_TOL))
            held_20ma_close = bool(float(p.close) >= float(p.ma20))
            if strong_structure and touched_20ma and held_20ma_close:
                strong20_touch_date = p.date.strftime("%Y-%m-%d")
                strong20_ok = True
                break

    route = "四要點" if standard_ok else ("強勢20MA續強" if strong20_ok else "")
    return {
        "ok": standard_ok or strong20_ok,
        "standard_ok": standard_ok,
        "strong20_ok": strong20_ok,
        "route": route,
        "strong20_touch_date": strong20_touch_date,
        "checks": {
            "20MA向上": c1,
            "收盤站上20MA": c2,
            "DIF今日>=昨日": c3,
            "基本面支撐": c4,
            "強勢20MA回測守住": strong20_ok,
        },
        "trend": classify_trend(float(t.close), float(t.ma20), float(t.ma30), float(y.ma20), float(y.ma30)),
        "df": x,
    }


def volume_gate(t: pd.Series) -> tuple[bool, float, float, float]:
    avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else math.nan
    lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
    turnover = float(t.turnover) if pd.notna(t.turnover) else 0.0
    vol_ratio = lots / avg20 if avg20 and not math.isnan(avg20) else 0.0
    ok = VOL_RATIO_MIN <= vol_ratio <= VOL_RATIO_MAX and lots >= MIN_VOLUME_LOTS and turnover >= MIN_TURNOVER
    return ok, lots, turnover, vol_ratio


def invalid_reason(x: pd.DataFrame, state: dict) -> str:
    if len(x) >= 4:
        q = x.iloc[-4:]
        if q["ma20"].notna().all() and bool((q["close"] < q["ma20"]).all()):
            return "跌破20MA後3個交易日仍未站回"

    if len(x) >= 4:
        d = x.iloc[-4:]["dif"].tolist()
        if all(pd.notna(v) for v in d) and d[1] < d[0] and d[2] < d[1] and d[3] < d[2]:
            return "DIF連續轉弱3日"

    return ""


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
                s["stage"] = "重新等待"
                s["last_failed_reclaim"] = date
                for k in ["three_above30_date", "pullback_date", "key_date", "key_high", "key_low"]:
                    s.pop(k, None)
                return s, None

    if s.get("key_date") and date > s["key_date"]:
        key_high = float(s["key_high"])
        volume_ok, lots, _turnover, vol_ratio = volume_gate(t)
        extension = (close / ma30 - 1.0) if ma30 else 999.0
        if allow_trigger and close > key_high and volume_ok and extension <= MAX_30MA_EXTENSION and s.get("last_trigger_date") != date:
            s["stage"] = "正式試單"
            s["last_trigger_date"] = date
            return s, {
                "date": date,
                "code": code,
                "signal_route": "30MA回踩",
                "close": round(close, 2),
                "key_date": s["key_date"],
                "key_high": round(key_high, 2),
                "key_low": round(float(s["key_low"]), 2),
                "volume_lots": round(lots, 0),
                "volume_ratio": round(vol_ratio, 2),
                "extension_30ma_pct": round(extension * 100, 2),
                "extension_20ma_pct": "",
            }
    return s, None


def detect_strong20_buy(code: str, x: pd.DataFrame, state: dict, l1: dict, fundamental_ok: bool) -> tuple[dict, dict | None]:
    s = dict(state or {})
    if len(x) < 34:
        return s, None

    i = len(x) - 1
    t, y = x.iloc[i], x.iloc[i - 1]
    date = t.date.strftime("%Y-%m-%d")
    close, high, low = float(t.close), float(t.high), float(t.low)
    ma20, ma30 = float(t.ma20), float(t.ma30)
    dif_turning_up = bool(t.dif >= y.dif)
    strong_now = bool(close > ma20 > ma30)
    touch_date = str(l1.get("strong20_touch_date") or "")

    if bool(l1.get("strong20_ok")) and touch_date:
        old_touch = str(s.get("strong20_touch_date") or "")
        old_key = str(s.get("strong20_key_date") or "")
        if not old_key or (old_touch and touch_date > old_touch):
            s["strong20_touch_date"] = touch_date
            s["strong20_key_date"] = date
            s["strong20_key_high"] = high
            s["strong20_key_low"] = low
            s["strong20_stage"] = "等待突破20MA關鍵K"

    key_date = str(s.get("strong20_key_date") or "")
    if not key_date:
        s.setdefault("strong20_stage", "等待20MA回測")
        return s, None

    if date > key_date and s.get("strong20_key_high") is not None:
        key_high = float(s["strong20_key_high"])
        key_low = float(s["strong20_key_low"])
        volume_ok, lots, _turnover, vol_ratio = volume_gate(t)
        ext20 = close / ma20 - 1.0 if ma20 else 999.0
        if close > key_high and volume_ok and strong_now and dif_turning_up and fundamental_ok and ext20 <= MAX_20MA_EXTENSION and s.get("strong20_last_trigger_date") != date:
            s["strong20_stage"] = "正式試單"
            s["strong20_last_trigger_date"] = date
            return s, {
                "date": date,
                "code": code,
                "signal_route": "強勢20MA續強",
                "close": round(close, 2),
                "key_date": key_date,
                "key_high": round(key_high, 2),
                "key_low": round(key_low, 2),
                "volume_lots": round(lots, 0),
                "volume_ratio": round(vol_ratio, 2),
                "extension_30ma_pct": "",
                "extension_20ma_pct": round(ext20 * 100, 2),
            }
    return s, None


def recommendation_fields() -> list[str]:
    return [
        "date", "code", "name", "trend", "signal_route", "baseline_entry",
        "key_date", "key_high", "key_low", "volume_lots", "volume_ratio",
        "extension_30ma_pct", "extension_20ma_pct",
    ]


def append_recommendation(row: dict) -> None:
    fields = recommendation_fields()
    existing_rows = []
    old_fields = []
    if RECOMMENDATIONS.exists():
        with RECOMMENDATIONS.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            old_fields = reader.fieldnames or []
            existing_rows = list(reader)
        if any(r.get("date") == row["date"] and r.get("code") == row["code"] for r in existing_rows):
            return

    if existing_rows and old_fields != fields:
        with RECOMMENDATIONS.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for old in existing_rows:
                w.writerow({k: old.get(k, "") for k in fields})

    exists = RECOMMENDATIONS.exists() and RECOMMENDATIONS.stat().st_size > 0
    with RECOMMENDATIONS.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def write_candidate_status(rows: list[dict]) -> None:
    fields = [
        "date", "code", "name", "trend", "stage", "strong20_stage",
        "layer1_current", "layer1_route", "strong20_touch_date", "strong20_key_date",
        "strong20_key_high", "strong20_key_low", "close", "ma20", "ma30", "dif",
        "volume_lots", "avg20_volume_lots", "volume_ratio", "turnover",
        "three_above30_date", "pullback_date", "key_date", "key_high", "key_low", "fundamental_reason",
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
    key_notices = []
    current_layer1_count = 0
    invalidated = []

    for code, g in df.groupby("code", sort=False):
        code = str(code)
        name = str(g.iloc[-1]["name"])
        f = fundamentals.get(code, {})
        fundamental_ok = bool(f.get("supported", False)) if isinstance(f, dict) else bool(f)
        l1 = layer1(g, fundamental_ok)
        l1_ok = bool(l1.get("ok"))
        previous_state = stocks_state.get(code, {})

        was_candidate = bool(previous_state.get("candidate_since") or previous_state.get("layer1_checks"))
        strong20_tracking = bool(previous_state.get("strong20_key_date"))
        if not l1_ok and not was_candidate and not strong20_tracking:
            continue

        x = l1["df"]
        if len(x) < 31:
            continue

        if was_candidate or strong20_tracking:
            reason = invalid_reason(x, previous_state)
            if reason:
                invalidated.append({"code": code, "name": name, "reason": reason})
                stocks_state.pop(code, None)
                continue

        if l1_ok:
            current_layer1_count += 1

        t = x.iloc[-1]
        avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else 0.0
        lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
        vol_ratio = lots / avg20 if avg20 else 0.0

        state_after_30, trigger30 = detect_layer2(
            code, x, previous_state, allow_trigger=bool(l1.get("standard_ok"))
        )
        new_state, trigger20 = detect_strong20_buy(code, x, state_after_30, l1, fundamental_ok)

        if new_state.get("key_date") and new_state.get("key_date") != previous_state.get("key_date"):
            key_notices.append({
                "code": code,
                "name": name,
                "trend": l1.get("trend", previous_state.get("trend", "其他")),
                "route": "30MA回踩",
                "key_date": new_state.get("key_date"),
                "key_high": new_state.get("key_high"),
                "key_low": new_state.get("key_low"),
                "close": round(float(t.close), 2),
                "ma20": round(float(t.ma20), 2),
                "ma30": round(float(t.ma30), 2),
                "dif": round(float(t.dif), 4),
            })

        if new_state.get("strong20_key_date") and new_state.get("strong20_key_date") != previous_state.get("strong20_key_date"):
            key_notices.append({
                "code": code,
                "name": name,
                "trend": l1.get("trend", previous_state.get("trend", "其他")),
                "route": "強勢20MA續強",
                "key_date": new_state.get("strong20_key_date"),
                "key_high": new_state.get("strong20_key_high"),
                "key_low": new_state.get("strong20_key_low"),
                "close": round(float(t.close), 2),
                "ma20": round(float(t.ma20), 2),
                "ma30": round(float(t.ma30), 2),
                "dif": round(float(t.dif), 4),
            })

        new_state["candidate_since"] = previous_state.get("candidate_since", latest_date)
        new_state["name"] = name
        new_state["trend"] = l1.get("trend", previous_state.get("trend", "其他"))
        new_state["layer1_current"] = l1_ok
        new_state["layer1_route"] = l1.get("route", "") if l1_ok else previous_state.get("layer1_route", "")
        new_state["layer1_checks"] = l1.get("checks", {})
        stocks_state[code] = new_state

        candidate_rows.append({
            "date": latest_date,
            "code": code,
            "name": name,
            "trend": new_state["trend"],
            "stage": new_state.get("stage", "等待30MA結構"),
            "strong20_stage": new_state.get("strong20_stage", "等待20MA回測"),
            "layer1_current": "符合" if l1_ok else "追蹤中",
            "layer1_route": new_state.get("layer1_route", ""),
            "strong20_touch_date": new_state.get("strong20_touch_date", ""),
            "strong20_key_date": new_state.get("strong20_key_date", ""),
            "strong20_key_high": new_state.get("strong20_key_high", ""),
            "strong20_key_low": new_state.get("strong20_key_low", ""),
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

        trigger = trigger20 or trigger30
        if trigger:
            trigger.update({"name": name, "trend": new_state["trend"], "baseline_entry": trigger["key_high"]})
            append_recommendation(trigger)
            triggers.append(trigger)

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
    state["invalidated_count"] = len(invalidated)
    state["last_invalidated"] = invalidated
    state["key_notice_count"] = len(key_notices)
    state["layer1_route_counts"] = dict(Counter(r["layer1_route"] for r in candidate_rows if r.get("layer1_route")))
    state["stage_counts"] = dict(Counter(r["stage"] for r in candidate_rows))
    state["trend_counts"] = dict(Counter(r["trend"] for r in candidate_rows))
    save_json(STATE_FILE, state)

    summary = {
        "latest_trade_date": latest_date,
        "layer1_candidates": current_layer1_count,
        "tracked_candidates": len(candidate_rows),
        "invalidated": len(invalidated),
        "key_candle_notices": len(key_notices),
        "formal_buy_signals": len(triggers),
        "layer1_route_counts": dict(Counter(r["layer1_route"] for r in candidate_rows if r.get("layer1_route"))),
        "trend_counts": dict(Counter(r["trend"] for r in candidate_rows)),
        "stage_counts": dict(Counter(r["stage"] for r in candidate_rows)),
    }
    print(json.dumps(summary, ensure_ascii=False))

    if key_notices:
        lines = [f"🦞 關鍵K形成通知｜{latest_date}", "⚠️ 觀察通知，是否進場由你自行判斷"]
        for r in key_notices:
            lines += [
                "",
                f"🟠 {r['code']} {r['name']}｜{r['trend']}",
                f"型態：{r['route']}",
                f"關鍵K日期：{r['key_date']}",
                f"關鍵K高：{r['key_high']}｜低：{r['key_low']}",
                f"收盤：{r['close']}",
                f"20MA：{r['ma20']}｜30MA：{r['ma30']}",
                f"DIF：{r['dif']}",
            ]
        send_line("\n".join(lines))

    if triggers:
        lines = [f"🦞 龍蝦雷達正式買點｜{latest_date}"]
        for r in triggers:
            lines += [
                "",
                f"🔴 {r['code']} {r['name']}｜{r['trend']}",
                f"買點路徑：{r['signal_route']}",
                f"關鍵K：{r['key_date']}",
                f"關鍵K高：{r['key_high']}",
                f"收盤：{r['close']}",
                f"成交量：{int(r['volume_lots'])}張｜量比：{r['volume_ratio']}x",
                f"績效基準試單價：{r['baseline_entry']}",
                f"初始失效參考：關鍵K低點 {r['key_low']}",
            ]
        send_line("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
