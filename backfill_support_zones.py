#!/usr/bin/env python3
"""Backfill missing structural support zones for candidates/recommendations created before support persistence was added."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
STATE_FILE = BASE / "signal_state.json"
RECOMMENDATIONS = BASE / "formal_recommendations.csv"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"stocks": {}}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_prices(code: str) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT date, low, close FROM prices WHERE stock_id=? ORDER BY date",
            con,
            params=(str(code),),
        )
    finally:
        con.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["low"] = pd.to_numeric(df["low"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma30"] = df["close"].rolling(30).mean()
    return df


def row_for_date(df: pd.DataFrame, date_text: str):
    if not date_text or df.empty:
        return None
    q = df[df["date"].dt.strftime("%Y-%m-%d") == date_text]
    return None if q.empty else q.iloc[-1]


def zone_for_30ma(df: pd.DataFrame, pullback_date: str, key_date: str):
    if not pullback_date or not key_date or df.empty:
        return None
    q = df[(df["date"] >= pd.Timestamp(pullback_date)) & (df["date"] <= pd.Timestamp(key_date))]
    key = row_for_date(df, key_date)
    if q.empty or key is None or pd.isna(key.ma30):
        return None
    structural_low = float(q["low"].min())
    ma = float(key.ma30)
    return round(min(structural_low, ma), 2), round(max(structural_low, ma), 2), "30MA回踩低點＋30MA重疊支撐"


def zone_for_20ma(df: pd.DataFrame, touch_date: str, key_date: str):
    touch = row_for_date(df, touch_date)
    key = row_for_date(df, key_date)
    if touch is None or key is None or pd.isna(key.ma20):
        return None
    structural_low = min(float(touch.low), float(key.low))
    ma = float(key.ma20)
    return round(min(structural_low, ma), 2), round(max(structural_low, ma), 2), "20MA回踩低點＋20MA重疊支撐"


def apply_zone(s: dict, zone, route: str) -> bool:
    if not zone:
        return False
    lower, upper, source = zone
    changed = (
        s.get("support_lower") != lower
        or s.get("support_upper") != upper
        or s.get("support_source") != source
        or s.get("support_status") != "有效"
    )
    s["support_lower"] = lower
    s["support_upper"] = upper
    s["support_source"] = source
    s["support_route"] = route
    s["support_status"] = "有效"
    return changed


def main() -> None:
    if not DB.exists() or not STATE_FILE.exists():
        print("support backfill skipped: database/state missing")
        return

    state = load_state()
    stocks = state.setdefault("stocks", {})
    cache: dict[str, pd.DataFrame] = {}
    changed_states = 0

    for code, s in stocks.items():
        if not isinstance(s, dict) or s.get("support_lower") not in (None, ""):
            continue
        df = cache.setdefault(str(code), load_prices(str(code)))

        zone = None
        route = ""
        if s.get("key_date") and s.get("pullback_date"):
            zone = zone_for_30ma(df, str(s.get("pullback_date")), str(s.get("key_date")))
            route = "30MA回踩"
        elif s.get("strong20_key_date") and s.get("strong20_touch_date"):
            zone = zone_for_20ma(df, str(s.get("strong20_touch_date")), str(s.get("strong20_key_date")))
            route = "強勢20MA續強"

        if apply_zone(s, zone, route):
            changed_states += 1

    if changed_states:
        save_state(state)

    changed_recs = 0
    if RECOMMENDATIONS.exists():
        with RECOMMENDATIONS.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            rows = list(reader)

        needed = ["support_lower", "support_upper", "support_source"]
        for k in needed:
            if k not in fields:
                fields.append(k)

        for r in rows:
            if r.get("support_lower") not in (None, ""):
                continue
            code = str(r.get("code") or "")
            s = stocks.get(code, {})
            df = cache.setdefault(code, load_prices(code))
            route = str(r.get("signal_route") or "")
            zone = None
            if route == "30MA回踩":
                zone = zone_for_30ma(df, str(s.get("pullback_date") or ""), str(r.get("key_date") or s.get("key_date") or ""))
            elif route == "強勢20MA續強":
                zone = zone_for_20ma(df, str(s.get("strong20_touch_date") or ""), str(r.get("key_date") or s.get("strong20_key_date") or ""))
            if zone:
                r["support_lower"], r["support_upper"], r["support_source"] = zone
                changed_recs += 1

        if changed_recs:
            with RECOMMENDATIONS.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in fields})

    print(json.dumps({"backfilled_states": changed_states, "backfilled_recommendations": changed_recs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
