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
OUT_JSON = BASE / "backtest_primary_key_results.json"
OUT_CSV = BASE / "backtest_primary_key_trades.csv"

VOL_RATIO_MIN = 1.20
VOL_RATIO_MAX = 3.00
MIN_VOLUME_LOTS = 1000
MIN_TURNOVER = 30_000_000
MAX_30MA_EXTENSION = 0.08
PRIMARY_KEY_LOOKAHEAD = 3


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def load_data():
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT date, stock_id AS code, stock_name AS name, open, high, low, close, volume, turnover "
            "FROM prices ORDER BY stock_id, date",
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
    return {
        str(k) for k, v in raw.get("stocks", {}).items()
        if isinstance(v, dict) and v.get("supported")
    }


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
    ok = (
        VOL_RATIO_MIN <= ratio <= VOL_RATIO_MAX
        and lots >= MIN_VOLUME_LOTS
        and turn >= MIN_TURNOVER
    )
    return ok, ratio


def forward_stats(x, i, entry):
    out = {}
    for n in (5, 10, 20):
        j = min(i + n, len(x) - 1)
        out[f"ret_{n}d"] = None if j <= i else round((float(x.iloc[j].close) / entry - 1) * 100, 2)
    future = x.iloc[i + 1:min(i + 21, len(x))]
    if len(future):
        out["mfe_20d"] = round((float(future.high.max()) / entry - 1) * 100, 2)
        out["mae_20d"] = round((float(future.low.min()) / entry - 1) * 100, 2)
    else:
        out["mfe_20d"] = None
        out["mae_20d"] = None
    return out


def primary_key_after_confirmation(x, confirm_i):
    end = min(len(x) - 1, confirm_i + PRIMARY_KEY_LOOKAHEAD)
    for j in range(confirm_i, end + 1):
        if j < 3:
            continue
        r = x.iloc[j]
        if pd.isna(r.ma30) or pd.isna(r.dif) or pd.isna(x.iloc[j - 1].dif):
            continue
        prev3_high = float(x.iloc[j - 3:j]["high"].max())
        breakout = float(r.close) > prev3_high
        above30 = float(r.close) > float(r.ma30)
        dif_ok = float(r.dif) >= float(x.iloc[j - 1].dif)
        if breakout and above30 and dif_ok:
            return j
    return None


def run_stock(x, code, name):
    trades = []
    used_primary_dates = set()

    # Find every fresh 3-close-above-30MA confirmation chronologically.
    confirmations = []
    last_confirm = -999
    for i in range(31, len(x)):
        q = x.iloc[i - 2:i + 1]
        if not q["ma30"].notna().all():
            continue
        if bool((q["close"] > q["ma30"]).all()):
            # Avoid treating every overlapping day in the same run as a new structure.
            if i - last_confirm >= 4:
                confirmations.append(i)
                last_confirm = i

    for confirm_i in confirmations:
        key_i = primary_key_after_confirmation(x, confirm_i)
        if key_i is None:
            continue
        key = x.iloc[key_i]
        key_date = key.date.strftime("%Y-%m-%d")
        if key_date in used_primary_dates:
            continue

        # Search forward until another 30 sessions; the first qualified breakout is counted.
        for i in range(key_i + 1, min(len(x), key_i + 31)):
            t, y = x.iloc[i], x.iloc[i - 1]
            if pd.isna(t.ma20) or pd.isna(t.ma30) or pd.isna(t.dif) or pd.isna(y.dif):
                continue

            # Existing four-point technical side; fundamentals are already filtered by supported_codes().
            standard = bool(t.ma20 > y.ma20 and t.close > t.ma20 and t.dif >= y.dif)
            if not standard:
                continue

            okvol, ratio = volume_ok(t)
            ext30 = float(t.close) / float(t.ma30) - 1.0 if float(t.ma30) else 999.0
            breakout = float(t.close) > float(key.high)
            if breakout and okvol and ext30 <= MAX_30MA_EXTENSION:
                entry = float(key.high)
                row = {
                    "date": t.date.strftime("%Y-%m-%d"),
                    "code": code,
                    "name": name,
                    "route": "主結構關鍵K突破",
                    "entry": round(entry, 2),
                    "close": round(float(t.close), 2),
                    "confirmation_date": x.iloc[confirm_i].date.strftime("%Y-%m-%d"),
                    "primary_key_date": key_date,
                    "primary_key_high": round(float(key.high), 2),
                    "primary_key_low": round(float(key.low), 2),
                    "volume_ratio": round(ratio, 2),
                    "extension_30ma_pct": round(ext30 * 100, 2),
                }
                row.update(forward_stats(x, i, entry))
                trades.append(row)
                used_primary_dates.add(key_date)
                break

    return trades


def rate(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(v > 0 for v in vals) / len(vals) * 100, 2) if vals else None


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def stats(trades):
    return {
        "signals": len(trades),
        "win_rate_5d_pct": rate([r["ret_5d"] for r in trades]),
        "win_rate_10d_pct": rate([r["ret_10d"] for r in trades]),
        "win_rate_20d_pct": rate([r["ret_20d"] for r in trades]),
        "avg_ret_5d_pct": avg([r["ret_5d"] for r in trades]),
        "avg_ret_10d_pct": avg([r["ret_10d"] for r in trades]),
        "avg_ret_20d_pct": avg([r["ret_20d"] for r in trades]),
        "avg_mfe_20d_pct": avg([r["mfe_20d"] for r in trades]),
        "avg_mae_20d_pct": avg([r["mae_20d"] for r in trades]),
    }


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

    fields = [
        "date", "code", "name", "route", "entry", "close",
        "confirmation_date", "primary_key_date", "primary_key_high", "primary_key_low",
        "volume_ratio", "extension_30ma_pct",
        "ret_5d", "ret_10d", "ret_20d", "mfe_20d", "mae_20d",
    ]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(trades)

    result = {
        "data_start": df["date"].min().strftime("%Y-%m-%d"),
        "data_end": df["date"].max().strftime("%Y-%m-%d"),
        "stocks_in_db": int(df["code"].astype(str).nunique()),
        "fundamental_supported_now": len(good),
        **stats(trades),
        "primary_key_rule": (
            "After a 3-closes-above-30MA confirmation, within the confirmation day plus next 3 sessions, "
            "the first candle whose close breaks the prior 3-session high, remains above 30MA, and has DIF >= prior day DIF is the confirmed primary key. "
            "A formal test signal occurs on the first later close above that primary-key high while four-point technical conditions remain valid, volume ratio is 1.2x-3.0x, liquidity passes, and close is <=8% above 30MA."
        ),
        "method_note": (
            "Uses current fundamental_support.json as a fixed eligibility proxy for the whole historical window because point-in-time fundamental snapshots are not stored. "
            "This is an isolated research backtest and does not alter the live buy_signal.py entry rules."
        ),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
