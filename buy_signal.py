#!/usr/bin/env python3
"""Lobster Radar buy-point engine.

The former four-point screen has been retired.

Formal buy route
1. 破底翻:
   - a session breaks the preceding 20-session swing low by at least 0.5%;
   - the breakdown is recovered within three sessions;
   - the trigger close is back above that old swing low, is a bullish recovery
     and closes in the upper 35% of its daily range.
Only 破底翻 can create a formal recommendation or enter performance tracking.
Generic breakout-retest and 30MA routes are disabled. VCP remains a separate
observation-only scanner. 60MA is retained solely as a broad risk guard; it is
not a buy pattern or entry trigger.
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
FALSE_BREAK_SUPPORT_LOOKBACK = 60
FALSE_BREAK_SUPPORT_TOL = 0.01
FALSE_BREAK_MIN_TOUCHES = 2
FALSE_BREAK_MIN_GAP_DAYS = 1
FALSE_BREAK_RECOVERY_DAYS = 3
FAKEOUT_EVENT_LOOKBACK = 45
FAKEOUT_PRE_WINDOW = 20
FAKEOUT_SUPPORT_BAND = 0.03
FAKEOUT_MIN_SUPPORT_TOUCHES = 2
FAKEOUT_RECOVERY_MIN_DAYS = 3
FAKEOUT_RECOVERY_MAX_DAYS = 10
FAKEOUT_SUPPORT_BREAK_MIN = 0.01
FAKEOUT_ACUTE_DROP_MIN = 0.08
FAKEOUT_NEW_LOW_TOL = 0.005
FAKEOUT_BREAKOUT_MIN = 0.003
BREAKOUT_MIN = 0.003
BREAKOUT_STAND_DAYS = 3
BREAKOUT_RETEST_DAYS = 5
BREAKOUT_MIN_RETEST_DAYS = 2
BREAKOUT_MAX_ENTRY_EXTENSION = 0.03
BREAKOUT_MIN_VOLUME_5D_RATIO = 1.50
BREAKOUT_MIN_BODY_EFFICIENCY = 0.70
BREAKOUT_MIN_CLOSE_LOCATION = 0.75
BREAKOUT_MAX_ATR_COMPRESSION = 0.80
BREAKOUT_MAX_PREBREAK_VOLUME_RATIO = 0.85
BREAKOUT_CONFIRM_DAYS = max(BREAKOUT_STAND_DAYS, BREAKOUT_RETEST_DAYS)
BREAKOUT_HOLD_TOL = 0.005
BREAKOUT_RETEST_TOL = 0.01
PLATFORM_TOUCH_TOL = 0.03
MIN_PLATFORM_TOUCHES = 2
CLOSE_LOCATION_MIN = 0.65
VOL_RATIO_MIN = 1.20
VOL_RATIO_MAX = 3.00
MAX_STRUCTURE_EXTENSION = 0.08
MIN_VOLUME_LOTS = 300
MIN_TURNOVER = 30_000_000
SUPPORT_BREAK_TOL = 0.005
MIN_RISK_REWARD = 1.50
MIN_UPSIDE_ROOM = 0.08
MIN_RELATIVE_STRENGTH_20D = 0.03
MA60_MAX_BELOW = 0.10
MA60_MAX_5D_DECLINE = 0.02
EXIT_WARNING_DAYS = 2
STALLED_BOUNDARY_TOL = 0.01
EXIT_VOLUME_5D_MIN = 1.20

# Existing 70-stock 30MA key-candle route, now evaluated from the same
# official TWSE/TPEx SQLite database as the core Lobster routes.
MA30_KEY_WATCHLIST = {
    "2330", "2454", "2303", "3711", "3131", "6187", "3583", "6223",
    "6257", "2449", "3264", "3034", "2379", "3035", "3661", "3443",
    "3653", "3017", "2421", "3324", "6230", "2317", "2382", "3231",
    "2356", "6669", "2376", "2357", "3706", "2383", "6213", "8358",
    "3037", "3189", "8046", "2368", "3044", "4958", "2313", "6153",
    "2327", "2456", "2308", "6282", "2408", "3006", "2451", "3260",
    "1519", "1503", "1513", "1514", "2359", "4562", "2354", "2059",
    "6176", "3376", "2404", "6414", "3533", "6409", "3081", "3529",
    "5269", "6415", "5274", "8454", "9910", "2204", "2201",
}
MA30_RETEST_LOOKAHEAD = 15
MA30_RECLAIM_DAYS = 3
STRATEGY_VERSION = "破底翻唯一正式買點-v12取消突破回踩與30MA"
ROUTE_PRIORITY = {
    "破底翻": 1,
}


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
    x["ma60"] = x["close"].rolling(60).mean()
    x["ema6"] = ema(x["close"], 6)
    x["ema13"] = ema(x["close"], 13)
    x["dif"] = x["ema6"] - x["ema13"]
    x["avg20_lots"] = x["volume_lots"].rolling(20).mean()
    x["avg20_turnover"] = x["turnover"].rolling(20).mean()
    x["avg5_lots"] = x["volume_lots"].rolling(5).mean()
    previous_close = x["close"].shift(1)
    x["true_range"] = pd.concat([
        x["high"] - x["low"],
        (x["high"] - previous_close).abs(),
        (x["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    return x


def volume_gate(t: pd.Series) -> tuple[bool, float, float, float]:
    avg20 = float(t.avg20_lots) if pd.notna(t.avg20_lots) else math.nan
    lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
    turnover = float(t.turnover) if pd.notna(t.turnover) else 0.0
    avg20_turnover = (
        float(t.avg20_turnover) if pd.notna(t.avg20_turnover) else math.nan
    )
    ratio = lots / avg20 if avg20 and not math.isnan(avg20) else 0.0
    ok = (
        VOL_RATIO_MIN <= ratio <= VOL_RATIO_MAX
        and math.isfinite(avg20)
        and avg20 >= MIN_VOLUME_LOTS
        and math.isfinite(avg20_turnover)
        and avg20_turnover >= MIN_TURNOVER
    )
    return ok, lots, turnover, ratio


def close_location(t: pd.Series) -> float:
    spread = float(t.high) - float(t.low)
    return (float(t.close) - float(t.low)) / spread if spread > 0 else 1.0


def body_efficiency(t: pd.Series) -> float:
    spread = float(t.high) - float(t.low)
    return max(0.0, float(t.close) - float(t.open)) / spread if spread > 0 else 0.0


def bullish_engulfing(x: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    p, t = x.iloc[i - 1], x.iloc[i]
    return bool(
        float(p.close) < float(p.open)
        and float(t.close) > float(t.open)
        and float(t.open) <= float(p.close)
        and float(t.close) >= float(p.open)
    )


def long_lower_shadow(t: pd.Series) -> bool:
    spread = float(t.high) - float(t.low)
    lower = min(float(t.open), float(t.close)) - float(t.low)
    return spread > 0 and lower / spread >= 0.50


def add_four_layer_evidence(
    signal: dict,
    setup: dict,
    x: pd.DataFrame,
    signal_i: int,
    evidence: list[tuple[str, bool]],
) -> None:
    """Add evidence and risk planning without turning them into extra hard gates."""
    entry = float(signal["baseline_entry"])
    support = float(signal["support_lower"])
    stop = support * (1.0 - SUPPORT_BREAK_TOL)
    older = x.iloc[max(0, signal_i - 120):max(0, signal_i - LOOKBACK)]
    overhead = sorted({
        float(v) for v in older["high"].dropna() if float(v) > entry * 1.005
    })
    if overhead:
        target, target_source = overhead[0], "前方歷史壓力"
    else:
        width = max(float(signal.get("key_high") or entry) - support, entry * 0.01)
        target, target_source = entry + width, "結構等幅量測"
    risk = entry - stop
    rr = (target - entry) / risk if risk > 0 else 0.0
    passed = [name for name, ok in evidence if ok]
    signal.update({
        "stop_price": round(stop, 2),
        "target_price": round(target, 2),
        "target_source": target_source,
        "risk_reward": round(rr, 2),
        "evidence_count": len(passed),
        "evidence_notes": "、".join(passed),
        "risk_reward_status": "合格" if rr >= MIN_RISK_REWARD else "不足，縮小試單/不追價",
    })
    setup.update({key: signal[key] for key in (
        "stop_price", "target_price", "target_source", "risk_reward",
        "evidence_count", "evidence_notes", "risk_reward_status",
    )})


def tw_stock_tick(price: float) -> float:
    """Return the TW stock tick size for a positive reference price."""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.10
    if price < 500:
        return 0.50
    if price < 1000:
        return 1.00
    return 5.00


def false_break_support_zone(x: pd.DataFrame, break_i: int) -> tuple[float, float, int] | None:
    """Build the strongest repeated-close support zone before the breakdown."""
    start = max(0, break_i - FALSE_BREAK_SUPPORT_LOOKBACK)
    window = x.iloc[start:break_i]
    if len(window) < 5:
        return None

    touches: list[tuple[int, float]] = []
    for pos in range(2, len(window) - 2):
        row = window.iloc[pos]
        if float(row.low) <= float(window.iloc[pos - 2:pos + 3]["low"].min()):
            absolute_i = start + pos
            close_price = float(row.close)
            if not touches or absolute_i - touches[-1][0] >= FALSE_BREAK_MIN_GAP_DAYS:
                touches.append((absolute_i, close_price))
            elif close_price < touches[-1][1]:
                touches[-1] = (absolute_i, close_price)

    clusters: list[dict] = []
    for touch_i, price in touches:
        matching = [
            cluster for cluster in clusters
            if abs(price / float(cluster["center"]) - 1.0) <= FALSE_BREAK_SUPPORT_TOL
        ]
        if matching:
            cluster = min(
                matching,
                key=lambda item: abs(price / float(item["center"]) - 1.0),
            )
            cluster["touches"].append((touch_i, price))
            cluster["center"] = float(
                pd.Series([p for _, p in cluster["touches"]]).median()
            )
        else:
            clusters.append({"center": price, "touches": [(touch_i, price)]})

    pre_break_close = float(x.iloc[break_i - 1].close)
    repeated = [
        cluster for cluster in clusters
        if len(cluster["touches"]) >= FALSE_BREAK_MIN_TOUCHES
        and float(cluster["center"]) < pre_break_close
    ]
    if not repeated:
        return None

    # Most touches wins; ties choose the nearest support below pre-break price.
    winner = max(
        repeated,
        key=lambda cluster: (
            len(cluster["touches"]),
            float(cluster["center"]),
        ),
    )
    prices = [price for _, price in winner["touches"]]
    return float(min(prices)), float(max(prices)), len(prices)


def detect_false_break_reversal(code: str, x: pd.DataFrame) -> tuple[dict, dict | None]:
    """Detect a break of 60-day repeated-close support and reclaim within 3 sessions."""
    minimum = FALSE_BREAK_SUPPORT_LOOKBACK + FALSE_BREAK_RECOVERY_DAYS
    if len(x) < minimum:
        return {}, None

    i = len(x) - 1
    t = x.iloc[i]
    close = float(t.close)
    best = None

    first_break = max(FALSE_BREAK_SUPPORT_LOOKBACK, i - FALSE_BREAK_RECOVERY_DAYS)
    for break_i in range(first_break, i + 1):
        zone = false_break_support_zone(x, break_i)
        if zone is None:
            continue
        support_lower, support_upper, support_touches = zone
        tick = tw_stock_tick(support_lower)
        break_row = x.iloc[break_i]
        break_low = float(break_row.low)
        broke_floor = break_low <= support_lower - tick + 1e-9
        recovered = close >= support_upper + tw_stock_tick(support_upper) - 1e-9
        if not (broke_floor and recovered):
            continue
        depth = break_low / support_lower - 1.0
        candidate = {
            "break_i": break_i,
            "support_lower": support_lower,
            "support_upper": support_upper,
            "support_touches": support_touches,
            "break_low": break_low,
            "depth": depth,
        }
        if best is None or depth < best["depth"]:
            best = candidate

    if best is None:
        return {}, None

    bullish = close > float(t.open)
    recovery_strength = close > float(x.iloc[i - 1].close)
    location = close_location(t)
    volume_ok, lots, turnover, ratio = volume_gate(t)
    trigger_level = best["support_upper"] + tw_stock_tick(best["support_upper"])
    extension = close / trigger_level - 1.0
    break_i = int(best["break_i"])
    exhaustion = False
    if break_i >= 2:
        recent = x.iloc[break_i - 2:break_i + 1]
        ranges = (recent["high"] - recent["low"]).astype(float).tolist()
        volumes = recent["volume_lots"].astype(float).tolist()
        exhaustion = ranges[2] < ranges[1] < ranges[0] and volumes[2] > volumes[1] > volumes[0]
    accelerated_reclaim = i - break_i <= 2
    reversal_candle = bullish_engulfing(x, i) or long_lower_shadow(t)
    setup = {
        "pattern": "破底翻",
        "setup_date": x.iloc[break_i].date.strftime("%Y-%m-%d"),
        "trigger_level": round(trigger_level, 2),
        "support_lower": round(best["support_lower"], 2),
        "support_upper": round(best["support_upper"], 2),
        "support_source": "破底前60日重複收盤價支撐區",
        "support_touches": int(best["support_touches"]),
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
    pattern_key = (
        f"破底翻:{setup['setup_date']}:"
        f"{setup['support_lower']}-{setup['support_upper']}"
    )
    signal = {
        "date": date,
        "code": code,
        "signal_route": "破底翻",
        "signal_light": "🟢綠燈",
        "close": round(close, 2),
        "baseline_entry": round(trigger_level, 2),
        "key_date": setup["setup_date"],
        "key_high": round(best["support_upper"], 2),
        "key_low": round(best["break_low"], 2),
        "volume_lots": round(lots, 0),
        "volume_ratio": round(ratio, 2),
        "turnover": round(turnover, 0),
        "support_lower": setup["support_lower"],
        "support_upper": setup["support_upper"],
        "support_source": setup["support_source"],
        "support_touches": setup["support_touches"],
        "pattern_key": pattern_key,
        "structure_extension_pct": setup["structure_extension_pct"],
    }
    add_four_layer_evidence(signal, setup, x, i, [
        ("收復60日支撐區", True),
        ("破底後3日內收復", i - break_i <= FALSE_BREAK_RECOVERY_DAYS),
        ("吞噬/長下影", reversal_candle),
        ("風報比≥1.5", True),
    ])
    rr_ok = float(signal["risk_reward"]) >= MIN_RISK_REWARD
    evidence = ["收復60日支撐區", "破底後3日內收復"]
    if exhaustion or accelerated_reclaim:
        evidence.append("快速掃低收復")
    if reversal_candle:
        evidence.append("吞噬/長下影")
    if rr_ok:
        evidence.append("風報比≥1.5")
    signal["evidence_count"] = len(evidence)
    signal["evidence_notes"] = "、".join(evidence)
    setup["evidence_count"] = signal["evidence_count"]
    setup["evidence_notes"] = signal["evidence_notes"]
    return setup, signal

def detect_fakeout_recovery(code: str, x: pd.DataFrame) -> tuple[dict, dict | None]:
    """Detect an acute washout, fast reclaim and fresh range breakout.

    A moving-average breach alone is never enough. The route requires all four
    structural stages: washout, 3-10 session reclaim, no lower low, and a new
    breakout above the pre-washout range.
    """
    if len(x) < FAKEOUT_PRE_WINDOW + FAKEOUT_RECOVERY_MAX_DAYS + 2:
        return {}, None

    i = len(x) - 1
    t = x.iloc[i]
    close = float(t.close)
    selected = None
    first_break = max(FAKEOUT_PRE_WINDOW, i - FAKEOUT_EVENT_LOOKBACK)

    for break_i in range(first_break, i - 1):
        prior = x.iloc[break_i - FAKEOUT_PRE_WINDOW:break_i]
        if len(prior) < FAKEOUT_PRE_WINDOW:
            continue
        break_row = x.iloc[break_i]
        prior_support = float(prior["low"].min())
        prior_resistance = float(prior["high"].max())
        recent_peak = float(prior.tail(10)["high"].max())
        break_low = float(break_row.low)
        support_touches = int(
            (prior["low"] <= prior_support * (1.0 + FAKEOUT_SUPPORT_BAND)).sum()
        )
        broke_support = break_low < prior_support * (1.0 - FAKEOUT_SUPPORT_BREAK_MIN)
        acute_drop = break_low <= recent_peak * (1.0 - FAKEOUT_ACUTE_DROP_MIN)
        if not (
            acute_drop
            and broke_support
            and support_touches >= FAKEOUT_MIN_SUPPORT_TOUCHES
        ):
            continue

        reclaim_i = None
        reclaim_end = min(i, break_i + FAKEOUT_RECOVERY_MAX_DAYS)
        for j in range(break_i + FAKEOUT_RECOVERY_MIN_DAYS, reclaim_end + 1):
            if float(x.iloc[j].close) >= prior_support:
                reclaim_i = j
                break
        if reclaim_i is None:
            continue

        post_washout = x.iloc[break_i:i + 1]
        no_lower_low = float(post_washout["low"].min()) >= break_low * (
            1.0 - FAKEOUT_NEW_LOW_TOL
        )
        if not no_lower_low:
            continue

        candidate = {
            "break_i": break_i,
            "reclaim_i": reclaim_i,
            "prior_support": prior_support,
            "prior_resistance": prior_resistance,
            "break_low": break_low,
            "washout_pct": break_low / recent_peak - 1.0,
            "support_touches": support_touches,
        }
        if selected is None or reclaim_i > selected["reclaim_i"]:
            selected = candidate

    if selected is None:
        return {}, None

    break_i = int(selected["break_i"])
    reclaim_i = int(selected["reclaim_i"])
    support = float(selected["prior_support"])
    resistance = float(selected["prior_resistance"])
    break_low = float(selected["break_low"])
    location = close_location(t)
    volume_ok, lots, turnover, ratio = volume_gate(t)
    extension = close / resistance - 1.0
    previous_close = float(x.iloc[i - 1].close)
    fresh_breakout = (
        close > resistance * (1.0 + FAKEOUT_BREAKOUT_MIN)
        and previous_close <= resistance * (1.0 + FAKEOUT_BREAKOUT_MIN)
    )
    setup = {
        "pattern": "假摔收復確認",
        "setup_date": x.iloc[break_i].date.strftime("%Y-%m-%d"),
        "breakout_date": t.date.strftime("%Y-%m-%d") if fresh_breakout else "",
        "confirmation_mode": "突破原整理壓力" if fresh_breakout else "等待突破原整理壓力",
        "trigger_level": round(resistance, 2),
        "support_lower": round(support, 2),
        "support_upper": round(support * 1.02, 2),
        "support_source": "假摔後收復的原整理支撐",
        "structure_extension_pct": round(extension * 100, 2),
        "close_location": round(location, 2),
        "volume_ok": volume_ok,
        "washout_low": round(break_low, 2),
        "washout_pct": round(float(selected["washout_pct"]) * 100, 2),
        "reclaim_date": x.iloc[reclaim_i].date.strftime("%Y-%m-%d"),
        "recovery_days": reclaim_i - break_i,
        "support_touches": int(selected["support_touches"]),
    }
    confirmed = (
        fresh_breakout
        and close > float(t.open)
        and location >= CLOSE_LOCATION_MIN
        and volume_ok
        and extension <= MAX_STRUCTURE_EXTENSION
    )
    if not confirmed:
        return setup, None

    date = t.date.strftime("%Y-%m-%d")
    signal = {
        "date": date,
        "code": code,
        "signal_route": "假摔收復確認",
        "signal_light": "🟢綠燈",
        "close": round(close, 2),
        "baseline_entry": round(resistance * (1.0 + FAKEOUT_BREAKOUT_MIN), 2),
        "breakout_date": date,
        "confirmation_mode": "突破原整理壓力",
        "key_date": date,
        "key_high": round(resistance, 2),
        "key_low": round(break_low, 2),
        "volume_lots": round(lots, 0),
        "volume_ratio": round(ratio, 2),
        "turnover": round(turnover, 0),
        "support_lower": setup["support_lower"],
        "support_upper": setup["support_upper"],
        "support_source": setup["support_source"],
        "pattern_key": (
            f"假摔收復確認:{setup['setup_date']}:{resistance:.2f}"
        ),
        "structure_extension_pct": setup["structure_extension_pct"],
    }
    add_four_layer_evidence(signal, setup, x, i, [
        ("急跌洗盤", True),
        ("10日內收復", True),
        ("未再破低", True),
        ("突破原壓力", True),
    ])
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
        prior5_volume = float(x.iloc[max(0, break_i - 5):break_i]["volume_lots"].mean())
        volume_5d_ratio = (
            float(breakout_row.volume_lots) / prior5_volume
            if prior5_volume > 0 else 0.0
        )
        approach = x.iloc[max(0, break_i - 13):break_i]
        atr_ratio = math.inf
        prebreak_volume_ratio = math.inf
        if len(approach) >= 13:
            atr_short = float(approach.tail(3)["true_range"].mean())
            atr_base = float(approach.head(10)["true_range"].mean())
            recent_vol = float(approach.tail(3)["volume_lots"].mean())
            base_vol = float(approach.head(10)["volume_lots"].mean())
            atr_ratio = atr_short / atr_base if atr_base > 0 else math.inf
            prebreak_volume_ratio = recent_vol / base_vol if base_vol > 0 else math.inf
        efficient_breakout = (
            volume_5d_ratio >= BREAKOUT_MIN_VOLUME_5D_RATIO
            and body_efficiency(breakout_row) >= BREAKOUT_MIN_BODY_EFFICIENCY
            and close_location(breakout_row) >= BREAKOUT_MIN_CLOSE_LOCATION
        )
        compressed_approach = (
            atr_ratio <= BREAKOUT_MAX_ATR_COMPRESSION
            and prebreak_volume_ratio <= BREAKOUT_MAX_PREBREAK_VOLUME_RATIO
        )
        valid_breakout = (
            touches >= MIN_PLATFORM_TOUCHES
            and breakout_close > platform_high * (1.0 + BREAKOUT_MIN)
            and breakout_close > float(breakout_row.open)
            and volume_ok
            and extension <= BREAKOUT_MAX_ENTRY_EXTENSION
            and efficient_breakout
        )
        if valid_breakout:
            selected = {
                "break_i": break_i,
                "platform_high": platform_high,
                "touches": touches,
                "breakout_lots": breakout_lots,
                "breakout_ratio": breakout_ratio,
                "extension": extension,
                "volume_5d_ratio": volume_5d_ratio,
                "atr_ratio": atr_ratio,
                "prebreak_volume_ratio": prebreak_volume_ratio,
                "body_efficiency": body_efficiency(breakout_row),
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
    prev5_avg_lots = float(x.iloc[i - 5:i]["volume_lots"].mean())
    retest_volume_contracted = (
        lots < float(selected["breakout_lots"])
        and lots < prev5_avg_lots
    )

    retested = float(t.low) <= platform_high * (1.0 + BREAKOUT_RETEST_TOL)
    elapsed = i - break_i
    reversal_confirmation = close > float(t.open) or long_lower_shadow(t)
    retest_hold = (
        BREAKOUT_MIN_RETEST_DAYS <= elapsed <= BREAKOUT_RETEST_DAYS
        and held_structure
        and retested
        and retest_volume_contracted
        and close >= platform_high
        and location >= 0.50
        and reversal_confirmation
        and liquid_today
        and extension <= BREAKOUT_MAX_ENTRY_EXTENSION
    )

    mode = "等待高品質突破後量縮回踩"
    route = ""
    if retest_hold:
        mode = "高品質突破後量縮回踩不破"
        route = "突破回踩不破"

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
        "breakout_volume_lots": round(float(selected["breakout_lots"]), 0),
        "prev5_avg_volume_lots": round(prev5_avg_lots, 0),
        "retest_volume_contracted": retest_volume_contracted,
    }
    if not route:
        return setup, None

    date = t.date.strftime("%Y-%m-%d")
    baseline = platform_high
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
    breakout_row = x.iloc[break_i]
    atr_ratio = float(selected["atr_ratio"])
    prebreak_volume_ratio = float(selected["prebreak_volume_ratio"])
    volume_5d_ratio = float(selected["volume_5d_ratio"])
    efficient_breakout = True
    tight_approach = True
    add_four_layer_evidence(signal, setup, x, i, [
        ("ATR與量能收縮靠近", tight_approach),
        ("1.5倍量高效率突破", efficient_breakout),
        ("量縮回踩確認", True),
        ("風報比≥1.5", True),
    ])
    rr_ok = float(signal["risk_reward"]) >= MIN_RISK_REWARD
    evidence = []
    if tight_approach:
        evidence.append("ATR與量能收縮靠近")
    if efficient_breakout:
        evidence.append("1.5倍量高效率突破")
    evidence.append("量縮回踩確認")
    if rr_ok:
        evidence.append("風報比≥1.5")
    signal["evidence_count"] = len(evidence)
    signal["evidence_notes"] = "、".join(evidence)
    signal["breakout_efficiency"] = round(body_efficiency(breakout_row), 2)
    signal["atr_compression_ratio"] = round(atr_ratio, 2) if math.isfinite(atr_ratio) else ""
    setup["evidence_count"] = signal["evidence_count"]
    setup["evidence_notes"] = signal["evidence_notes"]
    setup["breakout_efficiency"] = signal["breakout_efficiency"]
    setup["atr_compression_ratio"] = signal["atr_compression_ratio"]
    return setup, signal

def detect_ma30_key_retest(code: str, x: pd.DataFrame) -> tuple[dict, dict | None]:
    """Detect the existing 30MA retest/key-candle route on the official database.

    A setup first records three consecutive closes above 30MA. Within the next
    15 sessions price must retest the lowest low of those three sessions
    (1 percent tolerance). A key candle must reclaim 30MA within three
    sessions of that retest. The formal trigger is the following session
    closing above the key-candle high.
    """
    if code not in MA30_KEY_WATCHLIST or len(x) < 35:
        return {}, None

    latest_i = len(x) - 1
    latest = x.iloc[latest_i]
    latest_close = float(latest.close)
    best = None

    for stand_i in range(30, latest_i):
        stand = x.iloc[stand_i - 2:stand_i + 1]
        if len(stand) != 3 or not (stand["close"] > stand["ma30"]).all():
            continue
        base_low = float(stand["low"].min())

        for retest_i in range(stand_i + 1, min(stand_i + MA30_RETEST_LOOKAHEAD, latest_i)):
            if float(x.iloc[retest_i].low) > base_low * 1.01:
                continue
            for key_i in range(retest_i, min(retest_i + MA30_RECLAIM_DAYS, latest_i)):
                key = x.iloc[key_i]
                if float(key.close) <= float(key.ma30):
                    continue
                if key_i == latest_i - 1 and latest_close > float(key.high):
                    candidate = {
                        "stand_i": stand_i,
                        "retest_i": retest_i,
                        "key_i": key_i,
                        "base_low": base_low,
                        "key_high": float(key.high),
                        "key_low": float(key.low),
                    }
                    if best is None or candidate["key_i"] > best["key_i"]:
                        best = candidate
                break
            break

    if best is None:
        return {}, None

    key = x.iloc[int(best["key_i"])]
    key_date = key.date.strftime("%Y-%m-%d")
    ma30 = float(latest.ma30)
    lots = float(latest.volume_lots) if pd.notna(latest.volume_lots) else 0.0
    turnover = float(latest.turnover) if pd.notna(latest.turnover) else 0.0
    avg20 = float(latest.avg20_lots) if pd.notna(latest.avg20_lots) else 0.0
    ratio = lots / avg20 if avg20 else 0.0
    support_lower = float(best["key_low"])
    support_upper = max(support_lower, min(ma30, float(best["key_high"])))
    extension = latest_close / float(best["key_high"]) - 1.0

    setup = {
        "pattern": "30MA關鍵K",
        "setup_date": x.iloc[int(best["stand_i"])].date.strftime("%Y-%m-%d"),
        "confirmation_mode": "回測後突破關鍵K",
        "trigger_level": round(float(best["key_high"]), 2),
        "support_lower": round(support_lower, 2),
        "support_upper": round(support_upper, 2),
        "support_source": "30MA回測關鍵K低點＋30MA",
        "structure_extension_pct": round(extension * 100, 2),
        "close_location": round(close_location(latest), 2),
        "volume_ok": lots >= MIN_VOLUME_LOTS and turnover >= MIN_TURNOVER,
    }
    signal = {
        "date": latest.date.strftime("%Y-%m-%d"),
        "code": code,
        "signal_route": "30MA關鍵K",
        "signal_light": "🟢綠燈",
        "close": round(latest_close, 2),
        "baseline_entry": round(float(best["key_high"]), 2),
        "key_date": key_date,
        "key_high": round(float(best["key_high"]), 2),
        "key_low": round(float(best["key_low"]), 2),
        "volume_lots": round(lots, 0),
        "volume_ratio": round(ratio, 2),
        "turnover": round(turnover, 0),
        "support_lower": setup["support_lower"],
        "support_upper": setup["support_upper"],
        "support_source": setup["support_source"],
        "pattern_key": f"30MA關鍵K:{key_date}:{float(best['key_high']):.2f}",
        "structure_extension_pct": setup["structure_extension_pct"],
        "confirmation_mode": setup["confirmation_mode"],
        "stop_price": round(support_lower * (1.0 - SUPPORT_BREAK_TOL), 2),
        "target_price": "",
        "target_source": "",
        "risk_reward": "",
        "risk_reward_status": "30MA獨立規則未套用",
        "evidence_count": 2,
        "evidence_notes": "30MA回測、關鍵K突破",
        "breakout_efficiency": "",
        "atr_compression_ratio": "",
    }
    return setup, signal


def recommendation_fields() -> list[str]:
    return [
        "date", "code", "name", "trend", "strategy_source", "signal_route", "signal_light",
        "baseline_entry", "primary_key_date", "primary_key_high", "primary_key_low",
        "key_date", "key_high", "key_low", "volume_lots", "volume_ratio",
        "extension_30ma_pct", "extension_20ma_pct",
        "support_lower", "support_upper", "support_source",
        "pattern_key", "structure_extension_pct", "breakout_date", "confirmation_mode",
        "stop_price", "target_price", "target_source", "risk_reward", "risk_reward_status",
        "evidence_count", "evidence_notes", "breakout_efficiency", "atr_compression_ratio",
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
                migrated = {key: old.get(key, "") for key in fields}
                if not migrated.get("strategy_source"):
                    route = old.get("signal_route", "")
                    migrated["strategy_source"] = (
                        "龍蝦核心" if route in {"破底翻", "突破回踩不破"}
                        else "龍蝦舊版30MA" if route == "30MA回踩"
                        else ""
                    )
                writer.writerow(migrated)

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
        "stop_price", "target_price", "target_source", "risk_reward", "risk_reward_status",
        "evidence_count", "evidence_notes", "breakout_efficiency", "atr_compression_ratio",
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
        "stop_price": setup.get("stop_price", ""),
        "target_price": setup.get("target_price", ""),
        "target_source": setup.get("target_source", ""),
        "risk_reward": setup.get("risk_reward", ""),
        "risk_reward_status": setup.get("risk_reward_status", ""),
        "evidence_count": setup.get("evidence_count", ""),
        "evidence_notes": setup.get("evidence_notes", ""),
        "breakout_efficiency": setup.get("breakout_efficiency", ""),
        "atr_compression_ratio": setup.get("atr_compression_ratio", ""),
    }


def detect_exit_warnings(market: pd.DataFrame, latest_date: str, state: dict) -> list[dict]:
    """Warn on structural failure or a high-volume rejection within two sessions."""
    if not RECOMMENDATIONS.exists():
        return []
    with RECOMMENDATIONS.open("r", encoding="utf-8-sig", newline="") as handle:
        recommendations = list(csv.DictReader(handle))
    prepared = {str(code): prepare(group) for code, group in market.groupby("code", sort=False)}
    sent = state.setdefault("exit_warning_keys", {})
    warnings = []
    for rec in recommendations:
        code = str(rec.get("code", ""))
        rec_date = str(rec.get("date", ""))
        if not code or not rec_date or code not in prepared or rec_date >= latest_date:
            continue
        x = prepared[code]
        dates = x["date"].dt.strftime("%Y-%m-%d")
        indexes = x.index[dates == rec_date].tolist()
        if not indexes:
            continue
        elapsed = len(x) - 1 - int(indexes[-1])
        if elapsed <= 0:
            continue
        t = x.iloc[-1]
        close = float(t.close)
        support = float(rec.get("support_lower") or 0.0)
        trigger = float(rec.get("support_upper") or rec.get("key_high") or 0.0)
        stop = support * (1.0 - SUPPORT_BREAK_TOL)
        prior_volume = float(x.iloc[max(0, len(x) - 6):-1]["volume_lots"].mean())
        volume_ratio = float(t.volume_lots) / prior_volume if prior_volume > 0 else 0.0
        route = str(rec.get("signal_route", ""))

        warning_type = ""
        reason = ""
        if stop > 0 and close < stop:
            warning_type = "結構失效"
            reason = f"收盤{close:.2f}跌破結構停損{stop:.2f}"
        elif elapsed <= EXIT_WARNING_DAYS and route in {"突破後站穩", "突破回踩不破"}:
            if trigger > 0 and close < trigger and volume_ratio >= EXIT_VOLUME_5D_MIN:
                warning_type = "假突破觀察"
                reason = f"{elapsed}日內放量跌回突破區，5日量比{volume_ratio:.2f}x"
            elif trigger > 0 and close <= trigger * (1.0 + STALLED_BOUNDARY_TOL) and volume_ratio >= EXIT_VOLUME_5D_MIN:
                warning_type = "走不開觀察"
                reason = f"{elapsed}日內放量仍黏在突破邊界，5日量比{volume_ratio:.2f}x"
        if not warning_type:
            continue
        pattern_key = rec.get("pattern_key") or f"{rec_date}:{code}:{route}"
        event_key = f"{pattern_key}:{warning_type}"
        if sent.get(event_key):
            continue
        sent[event_key] = latest_date
        warnings.append({
            "code": code, "name": rec.get("name", ""), "route": route,
            "warning_type": warning_type, "reason": reason, "close": round(close, 2),
        })
    return warnings


def latest_market_median_return20(market: pd.DataFrame) -> float:
    returns = []
    for _code, group in market.groupby("code", sort=False):
        x = group.sort_values("date")
        if len(x) >= 21:
            old = float(x.iloc[-21].close)
            new = float(x.iloc[-1].close)
            if old > 0:
                returns.append(new / old - 1.0)
    return float(pd.Series(returns).median()) if returns else 0.0


def apply_new_plan_gate(signal: dict | None, setup: dict, x: pd.DataFrame, market_return20: float) -> dict | None:
    """Hard gate formal buys by upside room, relative strength and 60MA protection."""
    if signal is None:
        return None
    i = len(x) - 1
    close = float(x.iloc[i].close)
    stock_return20 = close / float(x.iloc[i - 20].close) - 1.0 if i >= 20 else math.nan
    rs20 = stock_return20 - market_return20 if math.isfinite(stock_return20) else math.nan
    older = x.iloc[max(0, i - 120):max(0, i - 20)]
    prior_high = float(older["high"].max()) if len(older) else math.nan
    upside_room = math.inf if not math.isfinite(prior_high) or close >= prior_high else prior_high / close - 1.0

    # 60MA is a broad anti-downtrend guard, not a requirement to hug or remain
    # above the average. A valid reversal may sit up to 10% below 60MA, provided
    # the 60MA itself has not fallen more than 2% during the latest five sessions.
    t = x.iloc[i]
    ma60_now = float(t.ma60) if pd.notna(t.ma60) else math.nan
    ma60_5d = float(x.iloc[i - 5].ma60) if i >= 5 and pd.notna(x.iloc[i - 5].ma60) else math.nan
    ma60_price_ok = math.isfinite(ma60_now) and close >= ma60_now * (1.0 - MA60_MAX_BELOW)
    ma60_slope_ok = (
        math.isfinite(ma60_now)
        and math.isfinite(ma60_5d)
        and ma60_now >= ma60_5d * (1.0 - MA60_MAX_5D_DECLINE)
    )
    trend_ok = bool(ma60_price_ok and ma60_slope_ok)
    risk_reward = float(signal.get("risk_reward") or 0.0)
    passed = bool(
        upside_room >= MIN_UPSIDE_ROOM
        and rs20 >= MIN_RELATIVE_STRENGTH_20D
        and risk_reward >= MIN_RISK_REWARD
        and trend_ok
    )
    details = {
        "upside_room_pct": 999.0 if math.isinf(upside_room) else round(upside_room * 100, 2),
        "relative_strength_20d_pct": round(rs20 * 100, 2) if math.isfinite(rs20) else "",
        "ma60": round(ma60_now, 2) if math.isfinite(ma60_now) else "",
        "ma60_5d_change_pct": (
            round((ma60_now / ma60_5d - 1.0) * 100, 2)
            if math.isfinite(ma60_now) and math.isfinite(ma60_5d) and ma60_5d > 0
            else ""
        ),
        "ma60_protection": "通過" if trend_ok else "未通過",
        "new_plan_gate": "通過" if passed else "未通過",
    }
    signal.update(details)
    setup.update(details)
    return signal if passed else None


def main() -> int:
    market = read_market()
    state = load_json(STATE_FILE, {"stocks": {}})
    if not isinstance(state, dict):
        state = {"stocks": {}}
    # Preserve prior signal keys during the v4 rule migration to avoid duplicate alerts.
    state["strategy_version"] = STRATEGY_VERSION
    stocks_state = state.setdefault("stocks", {})
    latest_date = market["date"].max().strftime("%Y-%m-%d")
    market_return20 = latest_market_median_return20(market)

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
        false_signal = apply_new_plan_gate(false_signal, false_setup, x, market_return20)
        setups = [
            (false_setup, false_signal),
        ]

        fresh_signals = []
        for _setup, signal in setups:
            if signal is None:
                continue
            previous_key = str(stocks_state.get(code, {}).get(signal["signal_route"], ""))
            if previous_key != signal["pattern_key"]:
                fresh_signals.append(signal)

        selected_signal = (
            min(fresh_signals, key=lambda item: ROUTE_PRIORITY.get(item["signal_route"], 99))
            if fresh_signals else None
        )

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

            if signal is selected_signal:
                status = "正式買點"
            elif signal is not None:
                status = "同日重複訊號"
            else:
                status = "型態觀察"
            candidate_rows.append(candidate_row(latest_date, code, name, x, setup, status))
            route_counts[setup["pattern"]] += 1

        # Mark every fresh route as seen, but create only one formal sample and
        # one LINE item per stock per day. This prevents duplicate recommendations.
        for signal in fresh_signals:
            stocks_state.setdefault(code, {})[signal["signal_route"]] = signal["pattern_key"]

        if selected_signal is not None:
            signal = selected_signal
            signal.update({
                "name": name,
                "trend": signal["signal_route"],
                "strategy_source": (
                    "龍蝦30MA關鍵K" if signal["signal_route"] == "30MA關鍵K"
                    else "龍蝦核心"
                ),
                "primary_key_date": "",
                "primary_key_high": "",
                "primary_key_low": "",
                "extension_30ma_pct": "",
                "extension_20ma_pct": "",
            })
            append_recommendation(signal)
            triggers.append(signal)
            stocks_state.setdefault(code, {})["name"] = name
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

    exit_warnings = detect_exit_warnings(market, latest_date, state)

    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["latest_trade_date"] = latest_date
    state["strategy_version"] = STRATEGY_VERSION
    state["four_point_rule"] = "已取消"
    state["candidate_count"] = len(candidate_rows)
    state["formal_buy_signal_count"] = len(triggers)
    state["route_counts"] = dict(route_counts)
    save_json(STATE_FILE, state)

    summary = {
        "latest_trade_date": latest_date,
        "strategy": STRATEGY_VERSION,
        "four_point_rule": "retired",
        "candidates": len(candidate_rows),
        "formal_buy_signals": len(triggers),
        "exit_warnings": len(exit_warnings),
        "route_counts": dict(route_counts),
    }
    print(json.dumps(summary, ensure_ascii=False))

    if triggers or exit_warnings:
        lines = [
            f"🦞 龍蝦雷達買點建議｜{latest_date}",
            "新版：僅破底翻可正式試單；突破回踩與30MA只列觀察",
        ]
        for row in triggers:
            lines += [
                "",
                f"🟢 {row['code']} {row['name']}｜{row['signal_route']}",
                f"策略來源：{row['strategy_source']}",
                f"觸發價：{row['key_high']}｜收盤：{row.get('close', '')}",
                f"支撐區：{row['support_lower']}～{row['support_upper']}",
                f"支撐來源：{row['support_source']}",
                f"成交量：{int(row['volume_lots'])}張｜量比：{row['volume_ratio']}x",
                f"績效基準試單價：{row['baseline_entry']}",
                f"失效：收盤有效跌破 {row['support_lower']}（容許0.5%誤差）",
                f"四層證據：{row.get('evidence_count', '')}/4｜{row.get('evidence_notes', '')}",
                f"風報比：{row.get('risk_reward') or '未計算'}｜{row.get('risk_reward_status', '')}",
            ]
        for row in exit_warnings:
            lines += [
                "",
                f"⚠️ {row['code']} {row['name']}｜{row['warning_type']}",
                f"原買點：{row['route']}｜今日收盤：{row['close']}",
                f"原因：{row['reason']}",
                "處置：先提高警覺；回測不支持單憑此訊號賣出，仍以結構停損為準。",
            ]
        send_line("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
