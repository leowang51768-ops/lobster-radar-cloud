#!/usr/bin/env python3
"""Lobster Radar buy-point engine.

The former four-point screen has been retired.

Formal buy routes
1. 破底翻:
   - a session breaks the preceding 20-session swing low by at least 0.5%;
   - the breakdown is recovered within three sessions;
   - the trigger close is back above that old swing low, is a bullish recovery
     and closes in the upper 35% of its daily range.
2. 突破後確認:
   - the breakout day only creates a setup and never triggers an immediate buy;
   - within the next five sessions, either price confirms it can hold above the
     platform or it pulls back to the platform and closes back above support;
   - only the confirmation/retest session can create a formal buy signal.

Both routes retain the existing liquidity gate, breakout-volume confirmation,
8% anti-chase limit and structure-support invalidation. 20MA, DIF and
fundamentals remain informational only and are not entry prerequisites.
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
STATE_FILE = BASE / "signal_state.json"
RECOMMENDATIONS = BASE / "formal_recommendations.csv"
CANDIDATES = BASE / "candidate_status.csv"

LOOKBACK = 20
FALSE_BREAK_MIN = 0.005
FALSE_BREAK_RECOVERY_DAYS = 3
BREAKOUT_MIN = 0.003
BREAKOUT_CONFIRM_DAYS = 5
BREAKOUT_HOLD_TOL = 0.005
BREAKOUT_RETEST_TOL = 0.01
PLATFORM_TOUCH_TOL = 0.03
MIN_PLATFORM_TOUCHES = 2
CLOSE_LOCATION_MIN = 0.65
VOL_RATIO_MIN = 1.20
VOL_RATIO_MAX = 3.00
MAX_STRUCTURE_EXTENSION = 0.08
MIN_VOLUME_LOTS = 1000
MIN_TURNOVER = 30_000_000
SUPPORT_BREAK_TOL = 0.005


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
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df["volume_lots"] = df["volume"] / 1000.0
    return df.dropna(subset=["open", "high", "low", "close"])


def prepare(g: pd.DataFrame) -> pd.DataFrame:
    x = g.copy().sort_values("date").reset_index(drop=True)
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma30"] = x["close"].rolling(30).mean()
    x["ema6"] = ema(x["close"], 6)
    x["ema13"] = ema(x["close"], 13)
    x["dif"] = x["ema6"] - x["ema13"]
    x["avg20_lots"] = x["volume_lots"].rolling(20).mean()
    return x


def volume_gate(t: pd.Series) -> tuple[bool, float, float, float]:
    avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else math.nan
    lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
    turnover = float(t.turnover) if pd.notna(t.turnover) else 0.0
    ratio = lots / avg20 if avg20 and not math.isnan(avg20) else 0.0
    ok = (
        VOL_RATIO_MIN <= ratio <= VOL_RATIO_MAX
        and lots >= MIN_VOLUME_LOTS
        and turnover >= MIN_TURNOVER
    )
    return ok, lots, turnover, ratio


def close_location(t: pd.Series) -> float:
    spread = float(t.high) - float(t.low)
    return (float(t.close) - float(t.low)) / spread if spread > 0 else 1.0


def detect_false_break_reversal(code: str, x: pd.DataFrame) -> tuple[dict, dict | None]:
    """Detect 破底翻: break a prior swing low, then reclaim it within 3 sessions."""
    if len(x) < LOOKBACK + FALSE_BREAK_RECOVERY_DAYS:
        return {}, None

    i = len(x) - 1
    t = x.iloc[i]
    close = float(t.close)
    best = None

    for break_i in range(max(LOOKBACK, i - FALSE_BREAK_RECOVERY_DAYS + 1), i + 1):
        reference = x.iloc[break_i - LOOKBACK:break_i]
        prior_low = float(reference["low"].min())
        break_row = x.iloc[break_i]
        break_low = float(break_row.low)
        broke_floor = break_low < prior_low * (1.0 - FALSE_BREAK_MIN)
        recovered = close > prior_low
        if not (broke_floor and recovered):
            continue
        depth = break_low / prior_low - 1.0
        if best is None or depth < best["depth"]:
            best = {
                "break_i": break_i,
                "prior_low": prior_low,
                "break_low": break_low,
                "depth": depth,
            }

    if best is None:
        return {}, None

    bullish = close > float(t.open)
    recovery_strength = close > float(x.iloc[i - 1].close)
    location = close_location(t)
    volume_ok, lots, turnover, ratio = volume_gate(t)
    extension = close / best["prior_low"] - 1.0
    setup = {
        "pattern": "破底翻",
        "setup_date": x.iloc[best["break_i"]].date.strftime("%Y-%m-%d"),
        "trigger_level": round(best["prior_low"], 2),
        "support_lower": round(best["break_low"], 2),
        "support_upper": round(best["prior_low"], 2),
        "support_source": "破底低點至收復之前波低點",
        "structure_extension_pct": round(extension * 100, 2),
        "close_location": round(location, 2),
        "volume_ok": volume_ok,
    }
    confirmed = (
        bullish
        and recovery_strength
        and location >= CLOSE_LOCATION_MIN
        and volume_ok
        and extension <= MAX_STRUCTURE_EXTENSION
    )
    if not confirmed:
        return setup, None

    date = t.date.strftime("%Y-%m-%d")
    pattern_key = f"破底翻:{setup['setup_date']}:{setup['trigger_level']}"
    signal = {
        "date": date,
        "code": code,
        "signal_route": "破底翻",
        "signal_light": "🟢綠燈",
        "close": round(close, 2),
        "baseline_entry": round(best["prior_low"], 2),
        "key_date": setup["setup_date"],
        "key_high": round(best["prior_low"], 2),
        "key_low": round(best["break_low"], 2),
        "volume_lots": round(lots, 0),
        "volume_ratio": round(ratio, 2),
        "turnover": round(turnover, 0),
        "support_lower": setup["support_lower"],
        "support_upper": setup["support_upper"],
        "support_source": setup["support_source"],
        "pattern_key": pattern_key,
        "structure_extension_pct": setup["structure_extension_pct"],
    }
    return setup, signal


def detect_true_breakout(code: str, x: pd.DataFrame) -> tuple[dict, dict | None]:
    """Require post-breakout holding confirmation or a successful platform retest."""
    if len(x) < LOOKBACK + 2:
        return {}, None

    i = len(x) - 1
    t = x.iloc[i]
    close = float(t.close)
    selected = None

    # The breakout itself is only a setup. Search the previous five sessions
    # for the most recent valid, volume-confirmed platform breakout.
    for break_i in range(i - 1, max(LOOKBACK - 1, i - BREAKOUT_CONFIRM_DAYS - 1), -1):
        prior = x.iloc[break_i - LOOKBACK:break_i]
        if len(prior) < LOOKBACK:
            continue
        breakout_row = x.iloc[break_i]
        platform_high = float(prior["high"].max())
        touch_floor = platform_high * (1.0 - PLATFORM_TOUCH_TOL)
        touches = int((prior["high"] >= touch_floor).sum())
        breakout_close = float(breakout_row.close)
        volume_ok, breakout_lots, _turnover, breakout_ratio = volume_gate(breakout_row)
        extension = breakout_close / platform_high - 1.0
        valid_breakout = (
            touches >= MIN_PLATFORM_TOUCHES
            and breakout_close > platform_high * (1.0 + BREAKOUT_MIN)
            and breakout_close > float(breakout_row.open)
            and close_location(breakout_row) >= CLOSE_LOCATION_MIN
            and volume_ok
            and extension <= MAX_STRUCTURE_EXTENSION
        )
        if valid_breakout:
            selected = {
                "break_i": break_i,
                "platform_high": platform_high,
                "touches": touches,
                "breakout_lots": breakout_lots,
                "breakout_ratio": breakout_ratio,
                "extension": extension,
            }
            break

    # With no completed breakout yet, retain only a near-platform observation.
    if selected is None:
        prior = x.iloc[-1 - LOOKBACK:-1]
        platform_high = float(prior["high"].max())
        touches = int((prior["high"] >= platform_high * (1.0 - PLATFORM_TOUCH_TOL)).sum())
        extension = close / platform_high - 1.0
        setup = {
            "pattern": "突破後確認",
            "setup_date": "",
            "breakout_date": "",
            "confirmation_mode": "等待有效突破",
            "trigger_level": round(platform_high, 2),
            "support_lower": round(platform_high * (1.0 - 0.01), 2),
            "support_upper": round(platform_high, 2),
            "support_source": "20日整理平台上緣轉支撐",
            "platform_touches": touches,
            "structure_extension_pct": round(extension * 100, 2),
            "close_location": round(close_location(t), 2),
            "volume_ok": False,
        }
        return setup, None

    break_i = int(selected["break_i"])
    platform_high = float(selected["platform_high"])
    breakout_date = x.iloc[break_i].date.strftime("%Y-%m-%d")
    post = x.iloc[break_i + 1:i + 1]
    held_structure = bool(
        len(post) >= 1
        and (post["close"] >= platform_high * (1.0 - BREAKOUT_HOLD_TOL)).all()
    )
    extension = close / platform_high - 1.0
    location = close_location(t)
    lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
    turnover = float(t.turnover) if pd.notna(t.turnover) else 0.0
    avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else 0.0
    ratio = lots / avg20 if avg20 else 0.0
    liquid_today = lots >= MIN_VOLUME_LOTS and turnover >= MIN_TURNOVER

    retested = float(t.low) <= platform_high * (1.0 + BREAKOUT_RETEST_TOL)
    retest_hold = (
        held_structure
        and retested
        and close >= platform_high
        and location >= 0.50
        and liquid_today
        and extension <= MAX_STRUCTURE_EXTENSION
    )
    stand_confirmed = (
        held_structure
        and close >= platform_high * (1.0 + BREAKOUT_MIN)
        and close > float(t.open)
        and location >= CLOSE_LOCATION_MIN
        and liquid_today
        and extension <= MAX_STRUCTURE_EXTENSION
    )

    mode = "等待站穩／回踩確認"
    route = ""
    if retest_hold:
        mode = "回踩平台不破"
        route = "突破回踩不破"
    elif stand_confirmed:
        mode = "突破後站穩"
        route = "突破後站穩"

    setup = {
        "pattern": "突破後確認",
        "setup_date": breakout_date,
        "breakout_date": breakout_date,
        "confirmation_mode": mode,
        "trigger_level": round(platform_high, 2),
        "support_lower": round(platform_high * (1.0 - 0.01), 2),
        "support_upper": round(platform_high, 2),
        "support_source": "突破平台上緣轉支撐",
        "platform_touches": int(selected["touches"]),
        "structure_extension_pct": round(extension * 100, 2),
        "close_location": round(location, 2),
        "volume_ok": True,
    }
    if not route:
        return setup, None

    date = t.date.strftime("%Y-%m-%d")
    baseline = (
        platform_high
        if route == "突破回踩不破"
        else platform_high * (1.0 + BREAKOUT_MIN)
    )
    pattern_key = f"{route}:{breakout_date}:{platform_high:.2f}"
    signal = {
        "date": date,
        "code": code,
        "signal_route": route,
        "signal_light": "🟢綠燈",
        "close": round(close, 2),
        "baseline_entry": round(baseline, 2),
        "breakout_date": breakout_date,
        "confirmation_mode": mode,
        "key_date": date,
        "key_high": round(platform_high, 2),
        "key_low": round(float(t.low), 2),
        "volume_lots": round(lots, 0),
        "volume_ratio": round(ratio, 2),
        "turnover": round(turnover, 0),
        "support_lower": setup["support_lower"],
        "support_upper": setup["support_upper"],
        "support_source": setup["support_source"],
        "pattern_key": pattern_key,
        "structure_extension_pct": setup["structure_extension_pct"],
    }
    return setup, signal

def recommendation_fields() -> list[str]:
    return [
        "date", "code", "name", "trend", "signal_route", "signal_light",
        "baseline_entry", "primary_key_date", "primary_key_high", "primary_key_low",
        "key_date", "key_high", "key_low", "volume_lots", "volume_ratio",
        "extension_30ma_pct", "extension_20ma_pct",
        "support_lower", "support_upper", "support_source",
        "pattern_key", "structure_extension_pct", "breakout_date", "confirmation_mode",
    ]


def append_recommendation(row: dict) -> None:
    fields = recommendation_fields()
    existing_rows = []
    old_fields = []
    if RECOMMENDATIONS.exists():
        with RECOMMENDATIONS.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            old_fields = reader.fieldnames or []
            existing_rows = list(reader)
        if any(
            old.get("date") == row["date"]
            and old.get("code") == row["code"]
            and old.get("signal_route") == row["signal_route"]
            for old in existing_rows
        ):
            return

    if existing_rows and old_fields != fields:
        with RECOMMENDATIONS.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for old in existing_rows:
                writer.writerow({key: old.get(key, "") for key in fields})

    exists = RECOMMENDATIONS.exists() and RECOMMENDATIONS.stat().st_size > 0
    with RECOMMENDATIONS.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fields})


def write_candidate_status(rows: list[dict]) -> None:
    fields = [
        "date", "code", "name", "pattern", "status", "setup_date",
        "breakout_date", "confirmation_mode", "trigger_level", "close", "ma20", "ma30", "dif",
        "volume_lots", "avg20_volume_lots", "volume_ratio", "turnover",
        "platform_touches", "close_location", "structure_extension_pct",
        "support_lower", "support_upper", "support_source", "support_status",
    ]
    with CANDIDATES.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def send_line(text: str) -> bool:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        print("LINE_CHANNEL_ACCESS_TOKEN missing; signal recorded but not pushed")
        return False
    payload = json.dumps(
        {"messages": [{"type": "text", "text": text}]},
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
            print("LINE status", response.status)
            return 200 <= response.status < 300
    except urllib.error.HTTPError as error:
        print("LINE HTTP error", error.code, error.read().decode("utf-8", errors="replace"))
    except Exception as error:
        print("LINE error", repr(error))
    return False


def candidate_row(latest_date: str, code: str, name: str, x: pd.DataFrame, setup: dict, status: str) -> dict:
    t = x.iloc[-1]
    avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else 0.0
    lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
    ratio = lots / avg20 if avg20 else 0.0
    close = float(t.close)
    lower = setup.get("support_lower", "")
    support_status = ""
    if lower != "":
        support_status = "失效" if close < float(lower) * (1.0 - SUPPORT_BREAK_TOL) else "有效"
    return {
        "date": latest_date,
        "code": code,
        "name": name,
        "pattern": setup.get("pattern", ""),
        "status": status,
        "setup_date": setup.get("setup_date", ""),
        "breakout_date": setup.get("breakout_date", ""),
        "confirmation_mode": setup.get("confirmation_mode", ""),
        "trigger_level": setup.get("trigger_level", ""),
        "close": round(close, 2),
        "ma20": round(float(t.ma20), 2) if pd.notna(t.ma20) else "",
        "ma30": round(float(t.ma30), 2) if pd.notna(t.ma30) else "",
        "dif": round(float(t.dif), 4) if pd.notna(t.dif) else "",
        "volume_lots": round(lots, 0),
        "avg20_volume_lots": round(avg20, 0),
        "volume_ratio": round(ratio, 2),
        "turnover": round(float(t.turnover), 0) if pd.notna(t.turnover) else 0,
        "platform_touches": setup.get("platform_touches", ""),
        "close_location": setup.get("close_location", ""),
        "structure_extension_pct": setup.get("structure_extension_pct", ""),
        "support_lower": lower,
        "support_upper": setup.get("support_upper", ""),
        "support_source": setup.get("support_source", ""),
        "support_status": support_status,
    }


def main() -> int:
    market = read_market()
    state = load_json(STATE_FILE, {"stocks": {}})
    if state.get("strategy_version") != "破底翻+突破確認-v2":
        state = {"stocks": {}, "strategy_version": "破底翻+突破確認-v2"}
    stocks_state = state.setdefault("stocks", {})
    latest_date = market["date"].max().strftime("%Y-%m-%d")

    candidate_rows = []
    triggers = []
    route_counts = Counter()

    for raw_code, group in market.groupby("code", sort=False):
        code = str(raw_code)
        name = str(group.iloc[-1]["name"])
        x = prepare(group)
        if len(x) < LOOKBACK + FALSE_BREAK_RECOVERY_DAYS:
            continue

        false_setup, false_signal = detect_false_break_reversal(code, x)
        breakout_setup, breakout_signal = detect_true_breakout(code, x)
        setups = [(false_setup, false_signal), (breakout_setup, breakout_signal)]

        for setup, signal in setups:
            if not setup:
                continue
            close = float(x.iloc[-1].close)
            level = float(setup.get("trigger_level") or 0.0)
            near_setup = (
                setup["pattern"] == "破底翻"
                or (level > 0 and close >= level * 0.97)
            )
            if not near_setup and signal is None:
                continue

            status = "正式買點" if signal else "型態觀察"
            candidate_rows.append(candidate_row(latest_date, code, name, x, setup, status))
            route_counts[setup["pattern"]] += 1

            if signal is None:
                continue
            previous_key = str(stocks_state.get(code, {}).get(signal["signal_route"], ""))
            if previous_key == signal["pattern_key"]:
                continue

            signal.update({
                "name": name,
                "trend": signal["signal_route"],
                "primary_key_date": "",
                "primary_key_high": "",
                "primary_key_low": "",
                "extension_30ma_pct": "",
                "extension_20ma_pct": "",
            })
            append_recommendation(signal)
            triggers.append(signal)
            stocks_state.setdefault(code, {})[signal["signal_route"]] = signal["pattern_key"]
            stocks_state[code]["name"] = name
            stocks_state[code]["support_lower"] = signal["support_lower"]
            stocks_state[code]["support_upper"] = signal["support_upper"]
            stocks_state[code]["support_source"] = signal["support_source"]

    candidate_rows.sort(
        key=lambda row: (
            0 if row["status"] == "正式買點" else 1,
            0 if row["pattern"] == "破底翻" else 1,
            row["code"],
        )
    )
    write_candidate_status(candidate_rows)

    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["latest_trade_date"] = latest_date
    state["strategy_version"] = "破底翻+突破確認-v2"
    state["four_point_rule"] = "已取消"
    state["candidate_count"] = len(candidate_rows)
    state["formal_buy_signal_count"] = len(triggers)
    state["route_counts"] = dict(route_counts)
    save_json(STATE_FILE, state)

    summary = {
        "latest_trade_date": latest_date,
        "strategy": "破底翻+突破確認",
        "four_point_rule": "retired",
        "candidates": len(candidate_rows),
        "formal_buy_signals": len(triggers),
        "route_counts": dict(route_counts),
    }
    print(json.dumps(summary, ensure_ascii=False))

    if triggers:
        lines = [f"🦞 龍蝦雷達正式買點｜{latest_date}", "新制：破底翻＋突破後站穩／回踩不破"]
        for row in triggers:
            lines += [
                "",
                f"🟢 {row['code']} {row['name']}｜{row['signal_route']}",
                f"觸發價：{row['key_high']}｜收盤：{row.get('close', '')}",
                f"支撐區：{row['support_lower']}～{row['support_upper']}",
                f"支撐來源：{row['support_source']}",
                f"成交量：{int(row['volume_lots'])}張｜量比：{row['volume_ratio']}x",
                f"績效基準試單價：{row['baseline_entry']}",
                f"失效：收盤有效跌破 {row['support_lower']}（容許0.5%誤差）",
            ]
        send_line("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
