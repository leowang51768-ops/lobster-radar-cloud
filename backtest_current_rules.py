#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
FUND = BASE / "fundamental_support.json"
OUT_JSON = BASE / "backtest_results.json"
OUT_CSV = BASE / "backtest_trades.csv"

VOL_RATIO_MIN = 1.20
VOL_RATIO_MAX = 3.00
MIN_VOLUME_LOTS = 1000
MIN_TURNOVER = 30_000_000
MAX_30MA_EXTENSION = 0.08
MAX_20MA_EXTENSION = 0.08
PULLBACK_TOL = 0.01
STRONG_TOUCH_TOL = 0.01
SUPPORT_BREAK_TOL = 0.005


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def load_data():
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT date, stock_id AS code, stock_name AS name, open, high, low, close, volume, turnover FROM prices ORDER BY stock_id, date",
            con,
        )
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume_lots"] = df["volume"] / 1000.0
    return df.dropna(subset=["close"])


def supported_codes():
    raw = json.loads(FUND.read_text(encoding="utf-8"))
    return {str(k) for k, v in raw.get("stocks", {}).items() if isinstance(v, dict) and v.get("supported")}


def prep(g):
    x = g.sort_values("date").reset_index(drop=True).copy()
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma30"] = x["close"].rolling(30).mean()
    x["ema6"] = ema(x["close"], 6)
    x["ema13"] = ema(x["close"], 13)
    x["dif"] = x["ema6"] - x["ema13"]
    x["avg20_lots"] = x["volume_lots"].rolling(20).mean()
    return x


def volume_ok(r):
    avg = float(r.avg20_lots) if pd.notna(r.avg20_lots) else 0.0
    lots = float(r.volume_lots) if pd.notna(r.volume_lots) else 0.0
    ratio = lots / avg if avg else 0.0
    turn = float(r.turnover) if pd.notna(r.turnover) else 0.0
    ok = VOL_RATIO_MIN <= ratio <= VOL_RATIO_MAX and lots >= MIN_VOLUME_LOTS and turn >= MIN_TURNOVER
    return ok, ratio


def forward_stats(x, i, entry):
    out = {}
    for n in (5, 10, 20):
        j = min(i + n, len(x) - 1)
        out[f"ret_{n}d"] = None if j <= i else round((float(x.iloc[j].close) / entry - 1) * 100, 2)
    future = x.iloc[i + 1 : min(i + 21, len(x))]
    if len(future):
        out["mfe_20d"] = round((float(future.high.max()) / entry - 1) * 100, 2)
        out["mae_20d"] = round((float(future.low.min()) / entry - 1) * 100, 2)
    else:
        out["mfe_20d"] = out["mae_20d"] = None
    return out


def set_support(state, lower, upper, source, route):
    state["support_lower"] = float(min(lower, upper))
    state["support_upper"] = float(max(lower, upper))
    state["support_source"] = source
    state["support_route"] = route


def clear_state():
    return {}


def run_stock(x, code, name):
    trades = []
    state = {}
    last_signal_key = None

    for i in range(34, len(x)):
        t, y = x.iloc[i], x.iloc[i - 1]
        if pd.isna(t.ma20) or pd.isna(t.ma30):
            continue

        c1 = bool(t.ma20 > y.ma20)
        c2 = bool(t.close > t.ma20)
        c3 = bool(t.dif >= y.dif)
        standard = c1 and c2 and c3

        strong = False
        strong_touch = None
        strong_touch_i = None
        if c2 and c3 and t.ma20 > t.ma30:
            for back in (1, 2, 3):
                p = x.iloc[i - back]
                if pd.notna(p.ma20) and pd.notna(p.ma30) and p.ma20 > p.ma30 and p.low <= p.ma20 * (1 + STRONG_TOUCH_TOL) and p.close >= p.ma20:
                    strong = True
                    strong_touch = p.date.strftime("%Y-%m-%d")
                    strong_touch_i = i - back
                    break

        if not (standard or strong or bool(state)):
            continue

        # 支撐區先判定：只有正式收盤有效跌破下緣0.5%以上才淘汰。
        if state.get("support_lower") is not None:
            lower = float(state["support_lower"])
            if float(t.close) < lower * (1 - SUPPORT_BREAK_TOL):
                state = clear_state()
                last_signal_key = None
                continue

        # 原本20MA三日修復與DIF連續轉弱3日規則保留。
        q = x.iloc[i - 3 : i + 1]
        below20 = q["ma20"].notna().all() and bool((q["close"] < q["ma20"]).all())
        d = q["dif"].tolist()
        dif_weak = all(pd.notna(v) for v in d) and d[1] < d[0] and d[2] < d[1] and d[3] < d[2]
        if below20 or dif_weak:
            state = clear_state()
            last_signal_key = None
            continue

        date = t.date.strftime("%Y-%m-%d")

        # 30MA回踩路徑
        if "three_above30_date" not in state:
            for j in range(max(2, i - 15), i + 1):
                q3 = x.iloc[j - 2 : j + 1]
                if q3["ma30"].notna().all() and bool((q3["close"] > q3["ma30"]).all()):
                    state["three_above30_date"] = x.iloc[j].date.strftime("%Y-%m-%d")
                    break
        if state.get("three_above30_date") and not state.get("pullback_date") and i >= 3:
            prior3_low = float(x.iloc[i - 3 : i]["low"].min())
            if float(t.low) <= prior3_low * (1 + PULLBACK_TOL):
                state["pullback_date"] = date
                state["pullback_i"] = i
        if state.get("pullback_date") and not state.get("key_date"):
            pb_i = int(state["pullback_i"])
            elapsed = i - pb_i
            if 0 <= elapsed <= 2 and t.close > t.ma30:
                state["key_date"] = date
                state["key_high"] = float(t.high)
                state["key_low"] = float(t.low)
                pullback_low = float(x.iloc[pb_i : i + 1]["low"].min())
                set_support(state, pullback_low, float(t.ma30), "30MA回踩低點＋30MA重疊支撐", "30MA回踩")
            elif elapsed > 2:
                state = clear_state()
                last_signal_key = None
                continue

        # 強勢20MA續強路徑
        if strong and strong_touch:
            old_touch = state.get("strong_touch")
            if not state.get("strong_key_date") or (old_touch and strong_touch > old_touch):
                state["strong_touch"] = strong_touch
                state["strong_key_date"] = date
                state["strong_key_high"] = float(t.high)
                state["strong_key_low"] = float(t.low)
                touch_low = float(x.iloc[strong_touch_i].low) if strong_touch_i is not None else float(t.low)
                structural_low = min(touch_low, float(t.low))
                set_support(state, structural_low, float(t.ma20), "20MA回踩低點＋20MA重疊支撐", "強勢20MA續強")

        sig = None
        okvol, ratio = volume_ok(t)
        if state.get("strong_key_date") and date > state["strong_key_date"]:
            ext20 = float(t.close) / float(t.ma20) - 1
            if t.close > state["strong_key_high"] and okvol and t.close > t.ma20 > t.ma30 and t.dif >= y.dif and ext20 <= MAX_20MA_EXTENSION:
                sig = ("強勢20MA續強", state["strong_key_date"], state["strong_key_high"], state["strong_key_low"], ext20)
        if sig is None and state.get("key_date") and date > state["key_date"]:
            ext30 = float(t.close) / float(t.ma30) - 1
            if standard and t.close > state["key_high"] and okvol and ext30 <= MAX_30MA_EXTENSION:
                sig = ("30MA回踩", state["key_date"], state["key_high"], state["key_low"], ext30)

        if sig:
            route, key_date, key_high, key_low, ext = sig
            signal_key = (route, key_date)
            if signal_key != last_signal_key:
                entry = float(key_high)
                row = {
                    "date": date,
                    "code": code,
                    "name": name,
                    "route": route,
                    "entry": round(entry, 2),
                    "close": round(float(t.close), 2),
                    "key_date": key_date,
                    "key_high": round(float(key_high), 2),
                    "key_low": round(float(key_low), 2),
                    "support_lower": round(float(state.get("support_lower", 0)), 2),
                    "support_upper": round(float(state.get("support_upper", 0)), 2),
                    "volume_ratio": round(ratio, 2),
                    "extension_pct": round(ext * 100, 2),
                }
                row.update(forward_stats(x, i, entry))
                trades.append(row)
                last_signal_key = signal_key
    return trades


def rate(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(v > 0 for v in vals) / len(vals) * 100, 2) if vals else None


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def main():
    df = load_data()
    good = supported_codes()
    trades = []
    for code, g in df.groupby("code", sort=False):
        code = str(code)
        if code not in good:
            continue
        x = prep(g)
        name = str(x.iloc[-1].get("name", code))
        trades.extend(run_stock(x, code, name))

    fields = ["date","code","name","route","entry","close","key_date","key_high","key_low","support_lower","support_upper","volume_ratio","extension_pct","ret_5d","ret_10d","ret_20d","mfe_20d","mae_20d"]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(trades)

    def stats(rr):
        return {
            "signals": len(rr),
            "win_rate_5d_pct": rate([r["ret_5d"] for r in rr]),
            "win_rate_10d_pct": rate([r["ret_10d"] for r in rr]),
            "win_rate_20d_pct": rate([r["ret_20d"] for r in rr]),
            "avg_ret_5d_pct": avg([r["ret_5d"] for r in rr]),
            "avg_ret_10d_pct": avg([r["ret_10d"] for r in rr]),
            "avg_ret_20d_pct": avg([r["ret_20d"] for r in rr]),
            "avg_mfe_20d_pct": avg([r["mfe_20d"] for r in rr]),
            "avg_mae_20d_pct": avg([r["mae_20d"] for r in rr]),
        }

    by_route = {route: stats([r for r in trades if r["route"] == route]) for route in sorted({r["route"] for r in trades})}
    result = {
        "data_start": df["date"].min().strftime("%Y-%m-%d"),
        "data_end": df["date"].max().strftime("%Y-%m-%d"),
        "stocks_in_db": int(df["code"].astype(str).nunique()),
        "fundamental_supported_now": len(good),
        **stats(trades),
        "by_route": by_route,
        "support_zone_rule": "Key-candle setup is invalidated when official close is more than 0.5% below support-zone lower edge before formal breakout.",
        "method_note": "Uses current fundamental_support.json as a fixed eligibility proxy for the whole historical window because point-in-time monthly-revenue snapshots are not stored. Technical rules, including support-zone invalidation, are replayed chronologically. One formal signal per key candle is counted.",
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
