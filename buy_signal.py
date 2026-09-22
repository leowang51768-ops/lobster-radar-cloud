#!/usr/bin/env python3
"""Lobster Radar formal buy-point engine.

Only 破底翻 can create a formal recommendation or enter performance tracking.
The support zone is built from repeated closes around local lows during the 60
completed sessions before the breakdown. Prices within 1% form one cluster;
the selected zone needs at least two touches separated by at least one session.
A valid event trades at least one TW stock tick below the zone, then closes at
least one tick above the zone on the breakdown day or within the next three
sessions. The recovery candle must be bullish, close above the prior close and
finish at or above the 55th percentile of its daily range.

A break of at least 3% and a long lower shadow are quality evidence only, not
hard entry gates. Liquidity, 60MA protection, upside room, relative strength
and reward-risk remain hard risk gates. Retired 30MA, generic breakout-retest
and fakeout-reclaim strategies have been removed. VCP remains a separate
observation-only scanner.
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
FALSE_BREAK_MIN_DEPTH = 0.03
FALSE_BREAK_MIN_TOUCHES = 2
FALSE_BREAK_MIN_GAP_DAYS = 1
FALSE_BREAK_RECOVERY_DAYS = 3
FALSE_BREAK_NECKLINE_LOOKBACK = 20
FALSE_BREAK_CONFIRM_LOOKBACK = 12
FALSE_BREAK_HIGHER_LOW_TOL = 0.005
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
STRATEGY_VERSION = "破底翻-v13-文章標準ABC頸線版"
ROUTE_PRIORITY = {
    "破底翻": 1,
    "破底翻確認": 2,
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
    # Liquidity is the hard gate.  A false-break recovery does not always
    # expand volume immediately, so the 1.2x-3.0x ratio is quality evidence
    # rather than a reason to discard an otherwise valid reversal.
    ok = (
        math.isfinite(avg20)
        and avg20 >= MIN_VOLUME_LOTS
        and math.isfinite(avg20_turnover)
        and avg20_turnover >= MIN_TURNOVER
    )
    return ok, lots, turnover, ratio


def close_location(t: pd.Series) -> float:
    spread = float(t.high) - float(t.low)
    return (float(t.close) - float(t.low)) / spread if spread > 0 else 1.0


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
    if signal.get("signal_route", "").startswith("破底翻") and signal.get("b_point"):
        b_point = float(signal["b_point"])
        stop = max(0.0, b_point - tw_stock_tick(b_point))
    else:
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


def prior_neckline(x: pd.DataFrame, break_i: int) -> tuple[float, str]:
    """Find the most recent pre-break swing high used as the neckline."""
    start = max(0, break_i - FALSE_BREAK_NECKLINE_LOOKBACK)
    window = x.iloc[start:break_i]
    if window.empty:
        return math.nan, ""
    swings = []
    for pos in range(2, len(window) - 2):
        high = float(window.iloc[pos].high)
        if high >= float(window.iloc[pos - 2:pos + 3]["high"].max()):
            swings.append(pos)
    pos = swings[-1] if swings else int(window["high"].astype(float).argmax())
    row = window.iloc[pos]
    return float(row.high), row.date.strftime("%Y-%m-%d")


def detect_false_break_reversal(code: str, x: pd.DataFrame) -> tuple[dict, dict | None]:
    """Detect article-standard A/B/C reversal and its later neckline confirmation."""
    minimum = FALSE_BREAK_SUPPORT_LOOKBACK + FALSE_BREAK_RECOVERY_DAYS
    if len(x) < minimum:
        return {}, None

    i = len(x) - 1
    t = x.iloc[i]
    close = float(t.close)
    best = None
    first_break = max(FALSE_BREAK_SUPPORT_LOOKBACK, i - FALSE_BREAK_CONFIRM_LOOKBACK)

    for break_i in range(first_break, i + 1):
        zone = false_break_support_zone(x, break_i)
        if zone is None:
            continue
        support_lower, support_upper, support_touches = zone
        end_i = min(i, break_i + FALSE_BREAK_RECOVERY_DAYS)
        event = x.iloc[break_i:end_i + 1]
        b_i = int(event["low"].astype(float).idxmin())
        b_row = x.iloc[b_i]
        break_low = float(b_row.low)
        if break_low > support_lower - tw_stock_tick(support_lower) + 1e-9:
            continue

        reclaim_i = None
        reclaim_level = support_upper + tw_stock_tick(support_upper)
        for j in range(b_i, end_i + 1):
            if float(x.iloc[j].close) >= reclaim_level - 1e-9:
                reclaim_i = j
                break
        if reclaim_i is None:
            continue

        neckline, neckline_date = prior_neckline(x, break_i)
        candidate = {
            "break_i": break_i,
            "b_i": b_i,
            "b_date": b_row.date.strftime("%Y-%m-%d"),
            "reclaim_i": reclaim_i,
            "support_lower": support_lower,
            "support_upper": support_upper,
            "support_touches": support_touches,
            "break_low": break_low,
            "break_depth_3pct": break_low <= support_lower * (1.0 - FALSE_BREAK_MIN_DEPTH),
            "break_long_lower_shadow": long_lower_shadow(b_row),
            "depth": break_low / support_lower - 1.0,
            "neckline": neckline,
            "neckline_date": neckline_date,
        }
        if best is None or break_i > best["break_i"]:
            best = candidate

    if best is None:
        return {}, None

    break_i = int(best["break_i"])
    reclaim_i = int(best["reclaim_i"])
    neckline = float(best["neckline"])
    neckline_trigger = (
        neckline + tw_stock_tick(neckline) if math.isfinite(neckline) else math.nan
    )
    is_early_entry = i == reclaim_i
    # Article structure: "底底高" must be an actual later swing low, not
    # merely two arbitrary sessions whose lows happen to remain above B.
    higher_low_i = None
    for j in range(reclaim_i + 2, i - 1):
        local = x.iloc[j - 2:j + 3]["low"].astype(float)
        if (
            len(local) == 5
            and float(x.iloc[j].low) <= float(local.min())
            and float(x.iloc[j].low)
            > float(best["break_low"]) * (1.0 + FALSE_BREAK_HIGHER_LOW_TOL)
        ):
            higher_low_i = j
    higher_low = higher_low_i is not None
    neckline_broken = math.isfinite(neckline_trigger) and close >= neckline_trigger - 1e-9
    neckline_fresh = (
        neckline_broken
        and i >= 1
        and float(x.iloc[i - 1].close) < neckline_trigger - 1e-9
    )
    bullish = close > float(t.open)
    recovery_strength = i >= 1 and close > float(x.iloc[i - 1].close)
    location = close_location(t)
    volume_ok, lots, turnover, ratio = volume_gate(t)
    dif_converging = (
        i >= 1 and pd.notna(t.dif) and pd.notna(x.iloc[i - 1].dif)
        and float(t.dif) >= float(x.iloc[i - 1].dif)
    )
    trigger_level = (
        best["support_upper"] + tw_stock_tick(best["support_upper"])
        if is_early_entry else neckline_trigger
    )
    extension = close / trigger_level - 1.0 if trigger_level and math.isfinite(trigger_level) else math.inf
    entry_stage = (
        "C點站回｜早期試單"
        if is_early_entry
        else "底底高＋突破頸線｜確認加碼"
    )
    setup = {
        "pattern": "破底翻",
        "setup_date": x.iloc[break_i].date.strftime("%Y-%m-%d"),
        "confirmation_mode": entry_stage,
        "trigger_level": round(trigger_level, 2) if math.isfinite(trigger_level) else "",
        "support_lower": round(best["support_lower"], 2),
        "support_upper": round(best["support_upper"], 2),
        "support_source": "A點：破底前60日重複收盤價支撐區",
        "support_touches": int(best["support_touches"]),
        "a_point_lower": round(best["support_lower"], 2),
        "a_point_upper": round(best["support_upper"], 2),
        "b_point": round(best["break_low"], 2),
        "b_point_date": best["b_date"],
        "neckline": round(neckline, 2) if math.isfinite(neckline) else "",
        "neckline_date": best["neckline_date"],
        "neckline_status": "當日有效突破" if neckline_fresh else "尚未有效突破",
        "higher_low_status": "已確認" if higher_low else "等待回檔確認",
        "higher_low_date": (
            x.iloc[higher_low_i].date.strftime("%Y-%m-%d")
            if higher_low_i is not None else ""
        ),
        "higher_low_price": (
            round(float(x.iloc[higher_low_i].low), 2)
            if higher_low_i is not None else ""
        ),
        "entry_stage": entry_stage,
        "dif_converging": dif_converging,
        "failure_level": round(
            max(0.0, best["break_low"] - tw_stock_tick(best["break_low"])), 2
        ),
        "structure_extension_pct": round(extension * 100, 2) if math.isfinite(extension) else "",
        "close_location": round(location, 2),
        "volume_ok": volume_ok,
        "volume_quality": (
            "放量加分" if VOL_RATIO_MIN <= ratio <= VOL_RATIO_MAX
            else "爆量過熱提示" if ratio > VOL_RATIO_MAX
            else "未放量，不扣除資格"
        ),
    }

    if is_early_entry:
        confirmed = (
            bullish and recovery_strength and location >= CLOSE_LOCATION_MIN
            and volume_ok and extension <= MAX_STRUCTURE_EXTENSION
        )
        signal_route = "破底翻"
    else:
        confirmed = (
            higher_low and neckline_fresh and bullish
            and volume_ok and extension <= MAX_STRUCTURE_EXTENSION
        )
        signal_route = "破底翻確認"

    if not confirmed:
        return setup, None

    date = t.date.strftime("%Y-%m-%d")
    pattern_key = (
        f"{signal_route}:{setup['setup_date']}:"
        f"{setup['support_lower']}-{setup['support_upper']}"
    )
    signal = {
        "date": date,
        "code": code,
        "signal_route": signal_route,
        "signal_light": "🟢綠燈",
        "close": round(close, 2),
        "baseline_entry": round(trigger_level, 2),
        "key_date": setup["setup_date"],
        "key_high": round(trigger_level, 2),
        "key_low": setup["b_point"],
        "a_point_lower": setup["a_point_lower"],
        "a_point_upper": setup["a_point_upper"],
        "b_point": setup["b_point"],
        "b_point_date": setup["b_point_date"],
        "neckline": setup["neckline"],
        "neckline_date": setup["neckline_date"],
        "neckline_status": setup["neckline_status"],
        "higher_low_status": setup["higher_low_status"],
        "entry_stage": setup["entry_stage"],
        "confirmation_mode": setup["confirmation_mode"],
        "dif_converging": setup["dif_converging"],
        "failure_level": setup["failure_level"],
        "volume_lots": round(lots, 0),
        "volume_ratio": round(ratio, 2),
        "volume_quality": setup["volume_quality"],
        "turnover": round(turnover, 0),
        "support_lower": setup["support_lower"],
        "support_upper": setup["support_upper"],
        "support_source": setup["support_source"],
        "support_touches": setup["support_touches"],
        "pattern_key": pattern_key,
        "structure_extension_pct": setup["structure_extension_pct"],
    }
    evidence = ["假跌破B點", "3日內收復A點"]
    if is_early_entry:
        evidence.append("C點收紅且收盤位置≥55%")
    else:
        evidence.extend(["回檔低點高於B點", "有效突破頸線"])
    if best["break_depth_3pct"]:
        evidence.append("跌破A點≥3%")
    if best["break_long_lower_shadow"]:
        evidence.append("B點長下影")
    if VOL_RATIO_MIN <= ratio <= VOL_RATIO_MAX:
        evidence.append("量能配合")
    elif ratio > VOL_RATIO_MAX:
        evidence.append("爆量過熱提示")
    add_four_layer_evidence(
        signal, setup, x, i,
        [(name, True) for name in evidence] + [("風報比≥1.5", True)],
    )
    rr_ok = float(signal["risk_reward"]) >= MIN_RISK_REWARD
    if rr_ok:
        evidence.append("風報比≥1.5")
    signal["evidence_count"] = len(evidence)
    signal["evidence_notes"] = "、".join(evidence)
    setup["evidence_count"] = signal["evidence_count"]
    setup["evidence_notes"] = signal["evidence_notes"]
    return setup, signal

def recommendation_fields() -> list[str]:
    return [
        "date", "code", "name", "trend", "strategy_source", "signal_route", "signal_light",
        "baseline_entry", "primary_key_date", "primary_key_high", "primary_key_low",
        "key_date", "key_high", "key_low",
        "a_point_lower", "a_point_upper", "b_point", "b_point_date",
        "neckline", "neckline_date", "neckline_status", "higher_low_status",
        "entry_stage", "dif_converging", "failure_level",
        "volume_lots", "volume_ratio",
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
                        "龍蝦核心" if route.startswith("破底翻") else ""
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
        "a_point_lower", "a_point_upper", "b_point", "b_point_date",
        "neckline", "neckline_date", "neckline_status", "higher_low_status",
        "entry_stage", "dif_converging", "failure_level",
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
        "a_point_lower": setup.get("a_point_lower", ""),
        "a_point_upper": setup.get("a_point_upper", ""),
        "b_point": setup.get("b_point", ""),
        "b_point_date": setup.get("b_point_date", ""),
        "neckline": setup.get("neckline", ""),
        "neckline_date": setup.get("neckline_date", ""),
        "neckline_status": setup.get("neckline_status", ""),
        "higher_low_status": setup.get("higher_low_status", ""),
        "entry_stage": setup.get("entry_stage", ""),
        "dif_converging": setup.get("dif_converging", ""),
        "failure_level": setup.get("failure_level", ""),
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
        route = str(rec.get("signal_route", ""))
        if route.startswith("破底翻"):
            # B is the article's invalidation reference.  Prefer the stored
            # failure level; fall back to the recorded B/key low for old rows.
            stop = float(
                rec.get("failure_level") or rec.get("b_point")
                or rec.get("key_low") or 0.0
            )
            if stop > 0 and not rec.get("failure_level"):
                stop = max(0.0, stop - tw_stock_tick(stop))
        else:
            stop = float(rec.get("stop_price") or 0.0)
            if stop <= 0:
                stop = support * (1.0 - SUPPORT_BREAK_TOL)
        prior_volume = float(x.iloc[max(0, len(x) - 6):-1]["volume_lots"].mean())
        volume_ratio = float(t.volume_lots) / prior_volume if prior_volume > 0 else 0.0
        # Only current break-bottom recommendations receive exit warnings.
        if not route.startswith("破底翻"):
            continue

        warning_type = ""
        reason = ""
        if stop > 0 and close < stop:
            warning_type = "B點失守｜型態失敗"
            reason = f"收盤{close:.2f}再破B點停損{stop:.2f}"
        elif route.startswith("破底翻") and support > 0 and close < support:
            warning_type = "A點重新失守"
            reason = f"收盤{close:.2f}跌回A點支撐{support:.2f}下方"
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
                "strategy_source": "龍蝦核心",
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

    force_notify = os.environ.get("BUY_FORCE_NOTIFY", "").strip() == "1"
    if triggers or exit_warnings or force_notify:
        lines = [
            f"🦞 龍蝦雷達正式破底翻｜{latest_date}",
            "僅破底翻可建立正式推薦與績效；VCP由獨立觀察雷達通知。",
        ]
        if not triggers and not exit_warnings:
            lines.append("✅ LINE測試成功｜目前無正式破底翻候選")
        for row in triggers:
            lines += [
                "",
                f"🟢 {row['code']} {row['name']}｜{row['signal_route']}",
                f"策略來源：{row['strategy_source']}",
                f"階段：{row.get('entry_stage', 'C點站回｜早期試單')}｜收盤：{row.get('close', '')}",
                f"A點支撐：{row.get('a_point_lower', row['support_lower'])}～{row.get('a_point_upper', row['support_upper'])}",
                f"B點低點：{row.get('b_point', row['key_low'])}（{row.get('b_point_date', row['key_date'])}）",
                f"頸線：{row.get('neckline') or '未形成'}｜{row.get('neckline_status', '')}",
                f"底底高：{row.get('higher_low_status', '等待確認')}",
                f"支撐來源：{row['support_source']}",
                f"成交量：{int(row['volume_lots'])}張｜量比：{row['volume_ratio']}x",
                f"績效基準試單價：{row['baseline_entry']}",
                f"失效／停損：再破B點，價格低於 {row.get('failure_level', row.get('stop_price'))}",
                f"型態證據：{row.get('evidence_count', '')}項｜{row.get('evidence_notes', '')}",
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
