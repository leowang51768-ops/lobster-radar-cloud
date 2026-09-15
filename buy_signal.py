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
   - standing confirmation must occur within three sessions;
   - a successful platform retest may occur within five sessions;
   - a retest must contract below both breakout-day volume and the prior
     five-session average volume;
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
BREAKOUT_STAND_DAYS = 3
BREAKOUT_RETEST_DAYS = 5
BREAKOUT_CONFIRM_DAYS = max(BREAKOUT_STAND_DAYS, BREAKOUT_RETEST_DAYS)
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

# Four-layer price-action evidence.  These thresholds translate the visual
# language (tight approach, efficient breakout and structural risk) into
# reproducible end-of-day rules.
VCP_DAYS = 5
ATR_SHORT_DAYS = 3
ATR_BASE_DAYS = 10
ATR_COMPRESSION_MAX = 0.80
PREBREAK_VOLUME_MAX = 0.85
BREAKOUT_VOLUME_5D_MIN = 1.50
BREAKOUT_BODY_EFFICIENCY_MIN = 0.70
BREAKOUT_CLOSE_LOCATION_MIN = 0.75
MIN_RISK_REWARD = 1.50
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
STRATEGY_VERSION = "龍蝦四層證據-v5+30MA關鍵K"
ROUTE_PRIORITY = {
    "真突破VCP": 1,
    "突破回踩不破": 2,
    "破底翻": 3,
    "突破後站穩": 4,
    "30MA關鍵K": 5,
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
    x["ema6"] = ema(x["close"], 6)
    x["ema13"] = ema(x["close"], 13)
    x["dif"] = x["ema6"] - x["ema13"]
    x["avg20_lots"] = x["volume_lots"].rolling(20).mean()
    x["avg5_lots"] = x["volume_lots"].rolling(5).mean()
    previous_close = x["close"].shift(1)
    x["true_range"] = pd.concat(
        [
            x["high"] - x["low"],
            (x["high"] - previous_close).abs(),
            (x["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["atr3"] = x["true_range"].rolling(ATR_SHORT_DAYS).mean()
    x["atr10"] = x["true_range"].rolling(ATR_BASE_DAYS).mean()
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


def body_efficiency(t: pd.Series) -> float:
    """Bullish real-body displacement divided by the full daily range."""
    spread = float(t.high) - float(t.low)
    return max(0.0, float(t.close) - float(t.open)) / spread if spread > 0 else 0.0


def bullish_engulfing(x: pd.DataFrame, i: int) -> bool:
    if i < 1:
        return False
    previous = x.iloc[i - 1]
    current = x.iloc[i]
    return bool(
        float(previous.close) < float(previous.open)
        and float(current.close) > float(current.open)
        and float(current.open) <= float(previous.close)
        and float(current.close) >= float(previous.open)
    )


def long_lower_shadow(t: pd.Series) -> bool:
    spread = float(t.high) - float(t.low)
    if spread <= 0:
        return False
    lower_shadow = min(float(t.open), float(t.close)) - float(t.low)
    return lower_shadow / spread >= 0.50


def next_resistance_target(
    x: pd.DataFrame,
    signal_i: int,
    entry: float,
    support: float,
    structure_high: float,
) -> tuple[float, str]:
    """Use the nearest older overhead high; otherwise use a measured move."""
    older = x.iloc[max(0, signal_i - 120):max(0, signal_i - LOOKBACK)]
    overhead = sorted(
        {float(v) for v in older["high"].dropna() if float(v) > entry * 1.005}
    )
    if overhead:
        return overhead[0], "前方歷史壓力"
    width = max(structure_high - support, entry * 0.01)
    return structure_high + width, "整理區等幅量測"


def attach_risk_plan(
    signal: dict,
    setup: dict,
    x: pd.DataFrame,
    signal_i: int,
    structure_high: float,
) -> bool:
    entry = float(signal["baseline_entry"])
    support = float(signal["support_lower"])
    stop = support * (1.0 - SUPPORT_BREAK_TOL)
    risk = entry - stop
    target, target_source = next_resistance_target(
        x, signal_i, entry, support, structure_high
    )
    reward = target - entry
    rr = reward / risk if risk > 0 else 0.0
    signal.update({
        "stop_price": round(stop, 2),
        "target_price": round(target, 2),
        "target_source": target_source,
        "risk_reward": round(rr, 2),
    })
    setup.update({
        "stop_price": signal["stop_price"],
        "target_price": signal["target_price"],
        "target_source": target_source,
        "risk_reward": signal["risk_reward"],
    })
    return rr >= MIN_RISK_REWARD


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
    break_i = int(best["break_i"])
    range_contracting = False
    volume_expanding = False
    if break_i >= 2:
        ranges = (
            x.iloc[break_i - 2:break_i + 1]["high"]
            - x.iloc[break_i - 2:break_i + 1]["low"]
        ).astype(float).tolist()
        volumes = x.iloc[break_i - 2:break_i + 1]["volume_lots"].astype(float).tolist()
        range_contracting = ranges[2] < ranges[1] < ranges[0]
        volume_expanding = volumes[2] > volumes[1] > volumes[0]
    volume_price_divergence = range_contracting and volume_expanding
    accelerated_reclaim = (
        best["depth"] <= -0.015
        and i - break_i <= 2
    )
    engulfing = bullish_engulfing(x, i)
    lower_shadow = long_lower_shadow(t)
    reversal_candle = engulfing or lower_shadow
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
        "volume_price_divergence": volume_price_divergence,
        "accelerated_reclaim": accelerated_reclaim,
        "bullish_engulfing": engulfing,
        "long_lower_shadow": lower_shadow,
        "evidence_count": sum([
            True,
            volume_price_divergence or accelerated_reclaim,
            reversal_candle,
            volume_ok,
        ]),
    }
    confirmed = (
        bullish
        and recovery_strength
        and location >= CLOSE_LOCATION_MIN
        and volume_ok
        and extension <= MAX_STRUCTURE_EXTENSION
        and (volume_price_divergence or accelerated_reclaim)
        and reversal_candle
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
        "evidence_count": setup["evidence_count"],
        "evidence_notes": "收復支撐＋量價背離/加速掃低＋吞噬/長下影＋流動性",
        "breakout_efficiency": "",
        "atr_compression_ratio": "",
    }
    if not attach_risk_plan(signal, setup, x, i, best["prior_low"]):
        setup["confirmation_mode"] = "風報比不足"
        return setup, None
    return setup, signal


def detect_vcp_true_breakout(code: str, x: pd.DataFrame) -> tuple[dict, dict | None]:
    """Four-layer VCP/price-efficiency breakout evaluated after the close."""
    minimum = max(LOOKBACK + 1, ATR_BASE_DAYS + VCP_DAYS + 1)
    if len(x) < minimum:
        return {}, None

    i = len(x) - 1
    t = x.iloc[i]
    prior = x.iloc[i - LOOKBACK:i]
    compression = x.iloc[i - VCP_DAYS:i]
    earlier = x.iloc[i - VCP_DAYS - ATR_BASE_DAYS:i - VCP_DAYS]
    platform_high = float(prior["high"].max())
    consolidation_low = float(compression["low"].min())
    touches = int((prior["high"] >= platform_high * (1.0 - PLATFORM_TOUCH_TOL)).sum())

    atr_short = float(compression.tail(ATR_SHORT_DAYS)["true_range"].mean())
    atr_base = float(earlier["true_range"].mean())
    atr_ratio = atr_short / atr_base if atr_base > 0 else math.inf
    recent_volume = float(compression.tail(3)["volume_lots"].mean())
    base_volume = float(earlier["volume_lots"].mean())
    prebreak_volume_ratio = recent_volume / base_volume if base_volume > 0 else math.inf
    breakout_lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
    prior5_volume = float(x.iloc[i - 5:i]["volume_lots"].mean())
    breakout_volume_ratio = breakout_lots / prior5_volume if prior5_volume > 0 else 0.0
    location = close_location(t)
    efficiency = body_efficiency(t)
    close = float(t.close)
    trend_ok = bool(
        pd.notna(t.ma20)
        and pd.notna(x.iloc[i - 1].ma20)
        and close > float(t.ma20)
        and float(t.ma20) >= float(x.iloc[i - 1].ma20)
    )
    liquidity_ok = (
        breakout_lots >= MIN_VOLUME_LOTS
        and float(t.turnover) >= MIN_TURNOVER
    )
    extension = close / platform_high - 1.0
    setup = {
        "pattern": "真突破VCP",
        "setup_date": t.date.strftime("%Y-%m-%d"),
        "breakout_date": t.date.strftime("%Y-%m-%d"),
        "confirmation_mode": "收盤確認，次交易日試單",
        "trigger_level": round(platform_high, 2),
        "support_lower": round(consolidation_low, 2),
        "support_upper": round(platform_high, 2),
        "support_source": "突破前5日窄幅整理區",
        "platform_touches": touches,
        "structure_extension_pct": round(extension * 100, 2),
        "close_location": round(location, 2),
        "breakout_efficiency": round(efficiency, 2),
        "atr_compression_ratio": round(atr_ratio, 2),
        "prebreak_volume_ratio": round(prebreak_volume_ratio, 2),
        "breakout_volume_5d_ratio": round(breakout_volume_ratio, 2),
        "volume_ok": liquidity_ok,
    }
    four_layers = {
        "trend_and_approach": trend_ok and atr_ratio <= ATR_COMPRESSION_MAX
            and prebreak_volume_ratio <= PREBREAK_VOLUME_MAX,
        "price_breakout": touches >= MIN_PLATFORM_TOUCHES
            and close > platform_high * (1.0 + BREAKOUT_MIN),
        "volume_price_efficiency": breakout_volume_ratio >= BREAKOUT_VOLUME_5D_MIN
            and efficiency >= BREAKOUT_BODY_EFFICIENCY_MIN
            and location >= BREAKOUT_CLOSE_LOCATION_MIN,
        "liquidity_and_extension": liquidity_ok
            and extension <= MAX_STRUCTURE_EXTENSION,
    }
    setup["evidence_count"] = sum(four_layers.values())
    if not all(four_layers.values()):
        return setup, None

    signal = {
        "date": t.date.strftime("%Y-%m-%d"),
        "code": code,
        "signal_route": "真突破VCP",
        "signal_light": "🟢綠燈",
        "close": round(close, 2),
        "baseline_entry": round(platform_high, 2),
        "breakout_date": t.date.strftime("%Y-%m-%d"),
        "confirmation_mode": setup["confirmation_mode"],
        "key_date": t.date.strftime("%Y-%m-%d"),
        "key_high": round(platform_high, 2),
        "key_low": round(consolidation_low, 2),
        "volume_lots": round(breakout_lots, 0),
        "volume_ratio": round(breakout_volume_ratio, 2),
        "turnover": round(float(t.turnover), 0),
        "support_lower": setup["support_lower"],
        "support_upper": setup["support_upper"],
        "support_source": setup["support_source"],
        "pattern_key": f"真突破VCP:{t.date.strftime('%Y-%m-%d')}:{platform_high:.2f}",
        "structure_extension_pct": setup["structure_extension_pct"],
        "evidence_count": 4,
        "evidence_notes": "20MA趨勢＋ATR/量縮靠近＋1.5倍量高效率突破＋結構風報比",
        "breakout_efficiency": setup["breakout_efficiency"],
        "atr_compression_ratio": setup["atr_compression_ratio"],
    }
    if not attach_risk_plan(signal, setup, x, i, platform_high):
        setup["confirmation_mode"] = "風報比不足"
        return setup, None
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
                "consolidation_low": float(prior.tail(VCP_DAYS)["low"].min()),
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
    retest_hold = (
        elapsed <= BREAKOUT_RETEST_DAYS
        and held_structure
        and retested
        and retest_volume_contracted
        and close >= platform_high
        and location >= 0.50
        and liquid_today
        and extension <= MAX_STRUCTURE_EXTENSION
    )
    stand_confirmed = (
        elapsed <= BREAKOUT_STAND_DAYS
        and held_structure
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

    structure_support = (
        float(t.low)
        if route == "突破回踩不破"
        else float(selected["consolidation_low"])
    )
    atr_compression_ratio = ""
    if break_i >= 13:
        short_atr = float(x.iloc[break_i - 3:break_i]["true_range"].mean())
        base_atr = float(x.iloc[break_i - 13:break_i - 3]["true_range"].mean())
        if base_atr > 0:
            atr_compression_ratio = round(short_atr / base_atr, 2)

    setup = {
        "pattern": "突破後確認",
        "setup_date": breakout_date,
        "breakout_date": breakout_date,
        "confirmation_mode": mode,
        "trigger_level": round(platform_high, 2),
        "support_lower": round(structure_support, 2),
        "support_upper": round(platform_high, 2),
        "support_source": (
            "回撤測試低點" if route == "突破回踩不破"
            else "突破前5日窄幅整理區低點"
        ),
        "platform_touches": int(selected["touches"]),
        "structure_extension_pct": round(extension * 100, 2),
        "close_location": round(location, 2),
        "volume_ok": True,
        "breakout_volume_lots": round(float(selected["breakout_lots"]), 0),
        "prev5_avg_volume_lots": round(prev5_avg_lots, 0),
        "retest_volume_contracted": retest_volume_contracted,
        "breakout_efficiency": round(body_efficiency(x.iloc[break_i]), 2),
        "atr_compression_ratio": atr_compression_ratio,
        "evidence_count": 3,
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
        "evidence_count": setup["evidence_count"],
        "evidence_notes": "平台突破＋站穩/量縮回踩＋結構停損與風報比",
        "breakout_efficiency": setup["breakout_efficiency"],
        "atr_compression_ratio": setup["atr_compression_ratio"],
    }
    if not attach_risk_plan(signal, setup, x, i, platform_high):
        setup["confirmation_mode"] = "風報比不足"
        return setup, None
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
        "evidence_count": 2,
        "evidence_notes": "30MA回測＋關鍵K突破（獨立舊規則）",
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
        "stop_price", "target_price", "target_source", "risk_reward",
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
                        "龍蝦核心" if route in {"破底翻", "突破回踩不破", "突破後站穩"}
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
        "stop_price", "target_price", "target_source", "risk_reward",
        "evidence_count", "breakout_efficiency", "atr_compression_ratio",
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
        "evidence_count": setup.get("evidence_count", ""),
        "breakout_efficiency": setup.get("breakout_efficiency", ""),
        "atr_compression_ratio": setup.get("atr_compression_ratio", ""),
    }


def detect_exit_warnings(market: pd.DataFrame, latest_date: str, state: dict) -> list[dict]:
    """Monitor immutable formal recommendations for structural/early failure."""
    if not RECOMMENDATIONS.exists():
        return []
    with RECOMMENDATIONS.open("r", encoding="utf-8-sig", newline="") as handle:
        recommendations = list(csv.DictReader(handle))

    prepared = {
        str(code): prepare(group)
        for code, group in market.groupby("code", sort=False)
    }
    sent = state.setdefault("exit_warning_keys", {})
    warnings = []
    for rec in recommendations:
        code = str(rec.get("code", ""))
        route = str(rec.get("signal_route", ""))
        rec_date = str(rec.get("date", ""))
        if not code or not rec_date or code not in prepared or rec_date >= latest_date:
            continue
        x = prepared[code]
        dates = x["date"].dt.strftime("%Y-%m-%d")
        earlier_indexes = x.index[dates == rec_date].tolist()
        if not earlier_indexes:
            continue
        signal_i = int(earlier_indexes[-1])
        latest_i = len(x) - 1
        elapsed = latest_i - signal_i
        if elapsed <= 0:
            continue

        t = x.iloc[latest_i]
        close = float(t.close)
        trigger = float(rec.get("support_upper") or rec.get("key_high") or 0.0)
        support = float(rec.get("support_lower") or 0.0)
        stop_text = rec.get("stop_price", "")
        stop = float(stop_text) if stop_text not in (None, "") else support * (1.0 - SUPPORT_BREAK_TOL)
        previous_volume = float(x.iloc[max(0, latest_i - 5):latest_i]["volume_lots"].mean())
        volume_ratio_5d = float(t.volume_lots) / previous_volume if previous_volume > 0 else 0.0

        warning_type = ""
        reason = ""
        if stop > 0 and close < stop:
            warning_type = "結構失效"
            reason = f"收盤{close:.2f}跌破結構停損{stop:.2f}"
        elif elapsed <= EXIT_WARNING_DAYS and route in {
            "真突破VCP", "突破後站穩", "突破回踩不破"
        }:
            if trigger > 0 and close < trigger and volume_ratio_5d >= EXIT_VOLUME_5D_MIN:
                warning_type = "假突破"
                reason = f"{elapsed}日內放量跌回突破區，量比{volume_ratio_5d:.2f}x"
            elif (
                trigger > 0
                and close <= trigger * (1.0 + STALLED_BOUNDARY_TOL)
                and volume_ratio_5d >= EXIT_VOLUME_5D_MIN
            ):
                warning_type = "走不開"
                reason = f"{elapsed}日內放量仍黏在突破邊界，量比{volume_ratio_5d:.2f}x"

        if not warning_type:
            continue
        pattern_key = rec.get("pattern_key") or f"{rec_date}:{code}:{route}"
        event_key = f"{pattern_key}:{warning_type}"
        if sent.get(event_key):
            continue
        sent[event_key] = latest_date
        warnings.append({
            "code": code,
            "name": rec.get("name", ""),
            "route": route,
            "warning_type": warning_type,
            "reason": reason,
            "close": round(close, 2),
        })
    return warnings


def main() -> int:
    market = read_market()
    state = load_json(STATE_FILE, {"stocks": {}})
    if not isinstance(state, dict):
        state = {"stocks": {}}
    # Preserve prior signal keys during the v4 rule migration to avoid duplicate alerts.
    state["strategy_version"] = STRATEGY_VERSION
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
        vcp_setup, vcp_signal = detect_vcp_true_breakout(code, x)
        breakout_setup, breakout_signal = detect_true_breakout(code, x)
        ma30_setup, ma30_signal = detect_ma30_key_retest(code, x)
        setups = [
            (vcp_setup, vcp_signal),
            (false_setup, false_signal),
            (breakout_setup, breakout_signal),
            (ma30_setup, ma30_signal),
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
                setup["pattern"] in {"破底翻", "30MA關鍵K"}
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
                    else "龍蝦四層證據" if signal["signal_route"] == "真突破VCP"
                    else "龍蝦核心補強"
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
            "四層證據版：真突破VCP＋破底翻＋站穩/量縮回踩＋30MA關鍵K",
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
                f"結構停損：{row.get('stop_price') or round(float(row['support_lower']) * (1.0 - SUPPORT_BREAK_TOL), 2)}",
                f"目標壓力：{row.get('target_price') or '待觀察'}｜風報比：{row.get('risk_reward') or '未計算'}",
                f"四層證據：{row.get('evidence_count', '')}/4",
            ]
        for row in exit_warnings:
            lines += [
                "",
                f"⚠️ {row['code']} {row['name']}｜{row['warning_type']}",
                f"原買點：{row['route']}｜今日收盤：{row['close']}",
                f"原因：{row['reason']}",
                "處置：屬出場／避險警示；是否實際賣出仍以持股狀態確認。",
            ]
        send_line("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
