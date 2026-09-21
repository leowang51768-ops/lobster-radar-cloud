#!/usr/bin/env python3
"""Scan the official TWSE/TPEx database for VCP stages.

This module is observation-only.  It writes vcp_candidates.csv and never
changes formal_recommendations.csv.  States are mutually exclusive:
1. 接近突破: valid contraction, 0-5% below pivot, volume drying up.
2. 當日突破: closes >0.3% above pivot with efficient price/volume expansion.
3. 突破後回踩: 2-5 sessions after breakout, volume contracts and pivot holds.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
OUTPUT = BASE / "vcp_candidates.csv"

MIN_HISTORY = 70
BASE_LOOKBACK = 325
PIVOT_LOOKBACK = 60
MIN_CONTRACTIONS = 2
MAX_CONTRACTIONS = 6
DEPTH_RATIO_MIN = 0.35
DEPTH_RATIO_MAX = 0.70
PIVOT_CLUSTER_TOL = 0.01
PIVOT_MIN_TOUCHES = 2
PIVOT_MIN_GAP_DAYS = 1
MIN_VOLUME_LOTS = 300
MIN_AVG_TURNOVER = 30_000_000
NEAR_PIVOT_PCT = 0.05
BREAKOUT_BUFFER = 0.003
PIVOT_STOP_BUFFER = 0.01
BREAKOUT_VOLUME_RATIO = 1.20
RETEST_MIN_DAYS = 2
RETEST_MAX_DAYS = 5
RETEST_LOW_TOL = 0.01
RETEST_CLOSE_TOL = 0.005
MA60_MAX_BELOW = 0.10
MA60_MAX_5D_DECLINE = 0.02
VCP_MAX_LEG_GAP_DAYS = 10
VCP_MAX_LAST_TROUGH_AGE = 10
VCP_MAX_FINAL_PEAK_DISTANCE = 0.05
VCP_MAX_PEAK_DISTANCE_WORSENING = 0.015
VCP_MAX_LAST_LEG_VOLUME_RATIO = 0.90

FIELDS = [
    "date", "code", "name", "market", "vcp_stage", "stage_explanation",
    "close", "pivot", "distance_to_pivot_pct", "upper_pivot",
    "upside_to_upper_pivot_pct", "reward_risk_ratio", "support_lower",
    "support_upper", "stop_price", "contraction_count", "base_sessions",
    "contraction_depths_pct", "volume_dry_ratio", "volume_ratio",
    "avg20_volume_lots", "avg20_turnover", "ma60", "ma60_5d_change_pct",
    "quality_score", "action", "invalidation",
]


def read_market() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT date, market, stock_id AS code, stock_name AS name, "
            "open, high, low, close, volume, turnover "
            "FROM prices ORDER BY stock_id, date", con
        )
    finally:
        con.close()
    for col in ("open", "high", "low", "close", "volume", "turnover"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df.dropna(subset=["open", "high", "low", "close"])


def prepare(group: pd.DataFrame) -> pd.DataFrame:
    x = group.sort_values("date").reset_index(drop=True).copy()
    x["volume_lots"] = x["volume"] / 1000.0
    x["avg5_lots"] = x["volume_lots"].rolling(5).mean()
    x["avg20_lots"] = x["volume_lots"].rolling(20).mean()
    x["avg20_turnover"] = x["turnover"].rolling(20).mean()
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma50"] = x["close"].rolling(50).mean()
    x["ma60"] = x["close"].rolling(60).mean()
    x["ma150"] = x["close"].rolling(150).mean()
    x["ma200"] = x["close"].rolling(200).mean()
    return x


def trend_and_liquidity_ok(x: pd.DataFrame, i: int) -> bool:
    """Require the article's SEPA stage-2 trend before accepting a VCP."""
    if i < 64:
        return False
    row = x.iloc[i]
    liquid = bool(
        math.isfinite(float(row.avg20_lots))
        and float(row.avg20_lots) >= MIN_VOLUME_LOTS
        and math.isfinite(float(row.avg20_turnover))
        and float(row.avg20_turnover) >= MIN_AVG_TURNOVER
    )
    if not liquid:
        return False

    # Use the complete SEPA test when at least 200 sessions are available.
    if i >= 204 and pd.notna(row.ma200) and pd.notna(row.ma150):
        old_ma200 = float(x.iloc[i - 20].ma200)
        return bool(
            float(row.close) > float(row.ma200)
            and float(row.ma200) > old_ma200
            and float(row.ma150) > float(row.ma200)
            and float(row.ma50) > float(row.ma150)
        )

    # The official six-month database cannot produce a genuine 200MA.  Use a
    # conservative, explicit proxy rather than silently treating 60MA as 200MA.
    old_ma60 = float(x.iloc[i - 20].ma60)
    recent = x.iloc[max(0, i - 40):i + 1]
    first, last = recent.iloc[:20], recent.iloc[-20:]
    higher_structure = bool(
        float(last.high.max()) >= float(first.high.max())
        and float(last.low.min()) >= float(first.low.min()) * 0.98
    )
    return bool(
        pd.notna(row.ma20) and pd.notna(row.ma50) and pd.notna(row.ma60)
        and float(row.close) > float(row.ma60)
        and float(row.ma20) > float(row.ma50) > float(row.ma60)
        and float(row.ma60) > old_ma60
        and higher_structure
    )


def contraction_profile(x: pd.DataFrame, end_i: int, pivot: float) -> dict | None:
    """Return a recent, continuous 2-6 leg VCP converging on one pivot.

    The former implementation could combine unrelated swings scattered across
    the 60-session window.  This version requires consecutive legs, a fresh
    final contraction, progressive convergence toward the selected pivot, and
    independent volume contraction inside the final leg.
    """
    start = max(0, end_i - BASE_LOOKBACK + 1)
    base = x.iloc[start:end_i + 1].reset_index(drop=True)
    if len(base) < 15 or pivot is None or pivot <= 0:
        return None

    highs, lows = [], []
    for j in range(2, len(base) - 2):
        if float(base.iloc[j].high) >= float(base.iloc[j - 2:j + 3].high.max()):
            highs.append(j)
        if float(base.iloc[j].low) <= float(base.iloc[j - 2:j + 3].low.min()):
            lows.append(j)

    legs = []
    for hi in highs:
        next_lows = [lo for lo in lows if hi < lo <= hi + 15]
        if not next_lows:
            continue
        lo = next_lows[0]
        peak, trough = float(base.iloc[hi].high), float(base.iloc[lo].low)
        depth = (peak - trough) / peak if peak > 0 else 0.0
        if 0.025 <= depth <= 0.40:
            legs.append((hi, lo, depth, peak, trough))

    clean = []
    for leg in legs:
        if clean and leg[0] <= clean[-1][1]:
            if leg[2] < clean[-1][2]:
                clean[-1] = leg
            continue
        clean.append(leg)
    # A 65-session base can contain older unrelated swings. Select the longest
    # valid ending sequence. Each pullback must contract to roughly 35%-70% of
    # the preceding pullback, matching the article's "about half" principle.
    chosen = None
    for count in range(min(MAX_CONTRACTIONS, len(clean)), MIN_CONTRACTIONS - 1, -1):
        candidate = clean[-count:]
        gaps = [b[0] - a[1] for a, b in zip(candidate, candidate[1:])]
        depths = [leg[2] for leg in candidate]
        ratios = [b / a for a, b in zip(depths, depths[1:]) if a > 0]
        if (
            ratios
            and all(1 <= gap <= VCP_MAX_LEG_GAP_DAYS for gap in gaps)
            and all(DEPTH_RATIO_MIN <= ratio <= DEPTH_RATIO_MAX for ratio in ratios)
            and depths[-1] <= 0.15
        ):
            chosen = candidate
            break
    if chosen is None:
        return None
    clean = chosen
    depths = [leg[2] for leg in clean]
    # The article defines a healthy base as roughly 3-65 weeks.
    base_sessions = len(base) - clean[0][0]
    if not (15 <= base_sessions <= 325):
        return None
    last_trough_age = len(base) - 1 - clean[-1][1]
    if last_trough_age > VCP_MAX_LAST_TROUGH_AGE:
        return None

    # A VCP is a continuation base beneath one ceiling.  If a contraction
    # high already cleared that ceiling, the selected level is merely an
    # internal price cluster rather than the true VCP pivot.
    if any(leg[3] > pivot * (1.0 + PIVOT_CLUSTER_TOL) for leg in clean):
        return None

    # Unlike the reversal scanner, VCP must form in an established rising
    # structure: price above 60MA and 20MA not below 60MA at the setup end.
    setup_row = x.iloc[end_i]
    setup_ma20 = float(setup_row.ma20)
    setup_ma60 = float(setup_row.ma60)
    if not (
        math.isfinite(setup_ma20)
        and math.isfinite(setup_ma60)
        and float(setup_row.close) >= setup_ma60
        and setup_ma20 >= setup_ma60
    ):
        return None

    # Contraction highs must progressively converge on the same breakout pivot.
    peak_distances = [abs(leg[3] / pivot - 1.0) for leg in clean]
    if peak_distances[-1] > VCP_MAX_FINAL_PEAK_DISTANCE:
        return None
    if any(
        later > earlier + VCP_MAX_PEAK_DISTANCE_WORSENING
        for earlier, later in zip(peak_distances, peak_distances[1:])
    ):
        return None

    recent5 = float(base.iloc[-5:].volume_lots.mean())
    prior20 = float(base.iloc[-20:].volume_lots.mean())
    dry_ratio = recent5 / prior20 if prior20 > 0 else math.inf
    if dry_ratio > 0.85:
        return None

    last_hi, last_lo = clean[-1][0], clean[-1][1]
    prior_start = max(0, last_hi - 20)
    prior_leg_volume = float(base.iloc[prior_start:last_hi].volume_lots.mean())
    last_leg_volume = float(base.iloc[last_hi:last_lo + 1].volume_lots.mean())
    last_leg_volume_ratio = (
        last_leg_volume / prior_leg_volume if prior_leg_volume > 0 else math.inf
    )
    if last_leg_volume_ratio > VCP_MAX_LAST_LEG_VOLUME_RATIO:
        return None

    return {
        "count": len(clean),
        "depths": depths,
        "dry_ratio": dry_ratio,
        "support": clean[-1][4],
        "last_trough_age": last_trough_age,
        "final_peak_distance": peak_distances[-1],
        "last_leg_volume_ratio": last_leg_volume_ratio,
        "base_sessions": base_sessions,
    }

def pivot_before(x: pd.DataFrame, i: int) -> float | None:
    """Return the most frequently retested resistance price in the prior 60 sessions.

    Only local swing highs are counted. Highs within 1% form one price cluster,
    and touches must be at least one session apart. A single isolated high
    is never used as a VCP pivot.
    """
    start = max(0, i - PIVOT_LOOKBACK)
    window = x.iloc[start:i]
    if len(window) < 5:
        return None

    peaks: list[tuple[int, float]] = []
    for pos in range(2, len(window) - 2):
        price = float(window.iloc[pos].high)
        local = window.iloc[pos - 2:pos + 3]["high"]
        if price >= float(local.max()):
            absolute_i = start + pos
            if not peaks or absolute_i - peaks[-1][0] >= PIVOT_MIN_GAP_DAYS:
                peaks.append((absolute_i, price))
            elif price > peaks[-1][1]:
                peaks[-1] = (absolute_i, price)

    clusters: list[dict] = []
    for peak_i, price in peaks:
        matching = [
            cluster for cluster in clusters
            if abs(price / float(cluster["center"]) - 1.0) <= PIVOT_CLUSTER_TOL
        ]
        if matching:
            cluster = min(
                matching,
                key=lambda item: abs(price / float(item["center"]) - 1.0),
            )
            cluster["touches"].append((peak_i, price))
            cluster["center"] = float(
                pd.Series([p for _, p in cluster["touches"]]).median()
            )
        else:
            clusters.append({"center": price, "touches": [(peak_i, price)]})

    repeated = [
        cluster for cluster in clusters
        if len(cluster["touches"]) >= PIVOT_MIN_TOUCHES
    ]
    if not repeated:
        return None

    # Most touches wins; ties prefer the cluster touched most recently.
    winner = max(
        repeated,
        key=lambda cluster: (
            len(cluster["touches"]),
            max(idx for idx, _ in cluster["touches"]),
        ),
    )
    return float(winner["center"])


def upper_pivot_before(x: pd.DataFrame, i: int, pivot: float, close: float) -> float | None:
    """Return the nearest repeated resistance cluster above both price and pivot."""
    start = max(0, i - PIVOT_LOOKBACK)
    window = x.iloc[start:i]
    if len(window) < 5:
        return None

    peaks: list[tuple[int, float]] = []
    for pos in range(2, len(window) - 2):
        price = float(window.iloc[pos].high)
        local = window.iloc[pos - 2:pos + 3]["high"]
        if price >= float(local.max()):
            absolute_i = start + pos
            if not peaks or absolute_i - peaks[-1][0] >= PIVOT_MIN_GAP_DAYS:
                peaks.append((absolute_i, price))
            elif price > peaks[-1][1]:
                peaks[-1] = (absolute_i, price)

    clusters: list[dict] = []
    for peak_i, price in peaks:
        matching = [
            cluster for cluster in clusters
            if abs(price / float(cluster["center"]) - 1.0) <= PIVOT_CLUSTER_TOL
        ]
        if matching:
            cluster = min(
                matching,
                key=lambda item: abs(price / float(item["center"]) - 1.0),
            )
            cluster["touches"].append((peak_i, price))
            cluster["center"] = float(
                pd.Series([p for _, p in cluster["touches"]]).median()
            )
        else:
            clusters.append({"center": price, "touches": [(peak_i, price)]})

    floor = max(pivot * (1.0 + PIVOT_CLUSTER_TOL), close)
    valid = [
        float(cluster["center"]) for cluster in clusters
        if len(cluster["touches"]) >= PIVOT_MIN_TOUCHES
        and float(cluster["center"]) > floor
    ]
    return min(valid) if valid else None


def support_zone_before(x: pd.DataFrame, i: int) -> tuple[float, float] | None:
    """Return the most frequently retested support band in the prior 60 sessions."""
    start = max(0, i - PIVOT_LOOKBACK + 1)
    window = x.iloc[start:i + 1]
    if len(window) < 5:
        return None

    troughs: list[tuple[int, float]] = []
    for pos in range(2, len(window) - 2):
        price = float(window.iloc[pos].low)
        local = window.iloc[pos - 2:pos + 3]["low"]
        if price <= float(local.min()):
            absolute_i = start + pos
            if not troughs or absolute_i - troughs[-1][0] >= PIVOT_MIN_GAP_DAYS:
                troughs.append((absolute_i, price))
            elif price < troughs[-1][1]:
                troughs[-1] = (absolute_i, price)

    clusters: list[dict] = []
    for trough_i, price in troughs:
        matching = [
            cluster for cluster in clusters
            if abs(price / float(cluster["center"]) - 1.0) <= PIVOT_CLUSTER_TOL
        ]
        if matching:
            cluster = min(
                matching,
                key=lambda item: abs(price / float(item["center"]) - 1.0),
            )
            cluster["touches"].append((trough_i, price))
            cluster["center"] = float(
                pd.Series([p for _, p in cluster["touches"]]).median()
            )
        else:
            clusters.append({"center": price, "touches": [(trough_i, price)]})

    close = float(x.iloc[i].close)
    repeated = [
        cluster for cluster in clusters
        if len(cluster["touches"]) >= PIVOT_MIN_TOUCHES
        and float(cluster["center"]) < close
    ]
    if not repeated:
        return None

    # Most touches wins; ties choose the nearest valid support below the close.
    winner = max(
        repeated,
        key=lambda cluster: (
            len(cluster["touches"]),
            float(cluster["center"]),
        ),
    )
    prices = [price for _, price in winner["touches"]]
    return float(min(prices)), float(max(prices))


def breakout_quality(x: pd.DataFrame, i: int, pivot: float) -> tuple[bool, float]:
    if i < 5:
        return False, 0.0
    row = x.iloc[i]
    spread = float(row.high) - float(row.low)
    location = (float(row.close) - float(row.low)) / spread if spread > 0 else 1.0
    body = max(0.0, float(row.close) - float(row.open)) / spread if spread > 0 else 0.0
    prior5 = float(x.iloc[i - 5:i].volume_lots.mean())
    ratio = float(row.volume_lots) / prior5 if prior5 > 0 else 0.0
    crossed = (
        float(row.close) > pivot * (1.0 + BREAKOUT_BUFFER)
        and float(x.iloc[i - 1].close) <= pivot * (1.0 + BREAKOUT_BUFFER)
    )
    return bool(crossed and ratio >= BREAKOUT_VOLUME_RATIO and location >= 0.75 and body >= 0.70), ratio


def make_row(x: pd.DataFrame, i: int, profile: dict, stage: str,
             pivot: float, volume_ratio: float, breakout_i: int | None = None) -> dict | None:
    row = x.iloc[i]
    close = float(row.close)
    # Article risk control is anchored to the final contraction's lower edge,
    # not an arbitrary one percent below the breakout pivot.
    contraction_floor = float(profile["support"])
    support_lower = contraction_floor
    support_upper = contraction_floor
    stop_price = contraction_floor * (1.0 - PIVOT_STOP_BUFFER)
    stop_distance = close / stop_price - 1.0 if stop_price > 0 else math.inf
    if stop_distance > 0.10:
        return None
    upper_pivot = upper_pivot_before(x, i, pivot, close)
    upside_pct = None
    reward_risk = None
    if upper_pivot is not None:
        risk = close - stop_price
        reward = upper_pivot - close
        if risk <= 0 or reward <= 0:
            return None
        upside_pct = reward / close * 100.0
        reward_risk = reward / risk
        if reward_risk < 1.5:
            return None
    ma60 = float(row.ma60)
    ma60_old = float(x.iloc[i - 5].ma60)
    ma_change = ma60 / ma60_old - 1.0 if ma60_old > 0 else 0.0
    distance = close / pivot - 1.0
    if stage == "接近突破":
        explanation = "符合第二階段上升趨勢，VCP回檔約逐次減半且末端量縮，收盤距樞紐價0～5%，尚未突破"
        action = "列入觀察；不得提前追價，等待帶量突破或突破後回踩"
    elif stage == "當日突破":
        explanation = "今日收盤有效突破樞紐價，量能≥前5日均量1.2倍且K棒效率合格"
        action = "確認為突破日；先觀察，不直接列正式試單，等待2～5日量縮回踩"
    else:
        explanation = f"突破後第{i - int(breakout_i)}日量縮回踩，收盤守住樞紐支撐"
        action = "VCP回踩確認；列高優先觀察，正式採用前仍須完成獨立回測"
    # Quality now rewards verified structural completeness, not merely the
    # number of contractions.  A 100 score therefore requires continuity,
    # pivot convergence and final-leg volume contraction to all be strong.
    quality = min(100, int(
        25 + profile["count"] * 8
        + max(0.0, 0.85 - profile["dry_ratio"]) * 35
        + max(0.0, 0.90 - profile["last_leg_volume_ratio"]) * 30
        + max(0.0, 0.05 - profile["final_peak_distance"]) * 200
        + max(0, VCP_MAX_LAST_TROUGH_AGE - profile["last_trough_age"])
        + (10 if stage == "突破後回踩" else 5 if stage == "當日突破" else 0)
    ))
    return {
        "date": row.date.strftime("%Y-%m-%d"),
        "code": str(row.code), "name": str(row["name"]), "market": str(row.market),
        "vcp_stage": stage, "stage_explanation": explanation,
        "close": round(close, 2), "pivot": round(pivot, 2),
        "distance_to_pivot_pct": round(distance * 100, 2),
        "upper_pivot": round(upper_pivot, 2) if upper_pivot is not None else "",
        "upside_to_upper_pivot_pct": round(upside_pct, 2) if upside_pct is not None else "",
        "reward_risk_ratio": round(reward_risk, 2) if reward_risk is not None else "",
        "support_lower": round(support_lower, 2),
        "support_upper": round(support_upper, 2),
        "stop_price": round(stop_price, 2),
        "contraction_count": profile["count"],
        "base_sessions": profile["base_sessions"],
        "contraction_depths_pct": "/".join(f"{d * 100:.1f}" for d in profile["depths"]),
        "volume_dry_ratio": round(profile["dry_ratio"], 2),
        "volume_ratio": round(volume_ratio, 2),
        "avg20_volume_lots": round(float(row.avg20_lots), 0),
        "avg20_turnover": round(float(row.avg20_turnover), 0),
        "ma60": round(ma60, 2), "ma60_5d_change_pct": round(ma_change * 100, 2),
        "quality_score": quality, "action": action,
        "invalidation": f"收盤跌破最後收縮下緣（{stop_price:.2f}）即失效",
    }


def classify_latest(group: pd.DataFrame) -> dict | None:
    x = prepare(group)
    i = len(x) - 1
    if len(x) < MIN_HISTORY or not trend_and_liquidity_ok(x, i):
        return None

    # Highest priority: a valid 2-5 day post-breakout contraction retest.
    for break_i in range(i - RETEST_MIN_DAYS, i - RETEST_MAX_DAYS - 1, -1):
        if break_i < 20:
            continue
        pivot = pivot_before(x, break_i)
        profile = contraction_profile(x, break_i - 1, pivot)
        if pivot is None or profile is None:
            continue
        broke, break_ratio = breakout_quality(x, break_i, pivot)
        if not broke:
            continue
        row = x.iloc[i]
        pre5 = float(x.iloc[break_i - 5:break_i].volume_lots.mean())
        volume_contracts = (
            float(row.volume_lots) < float(x.iloc[break_i].volume_lots)
            and float(row.volume_lots) < pre5
        )
        touched = float(row.low) <= pivot * 1.03 and float(row.low) >= pivot * (1.0 - RETEST_LOW_TOL)
        held = float(row.close) >= pivot * (1.0 - RETEST_CLOSE_TOL)
        turned_up = float(row.close) > float(row.open) or float(row.close) > float(x.iloc[i - 1].close)
        if volume_contracts and touched and held and turned_up:
            return make_row(x, i, profile, "突破後回踩", pivot, break_ratio, break_i)

    pivot = pivot_before(x, i)
    profile = contraction_profile(x, i - 1, pivot)
    if pivot is None or profile is None:
        return None
    broke, ratio = breakout_quality(x, i, pivot)
    if broke:
        return make_row(x, i, profile, "當日突破", pivot, ratio)

    distance = float(x.iloc[i].close) / pivot - 1.0
    if -NEAR_PIVOT_PCT <= distance <= 0.0 and float(x.iloc[i].close) >= float(x.iloc[i].open) * 0.98:
        prior5 = float(x.iloc[i - 5:i].volume_lots.mean())
        ratio = float(x.iloc[i].volume_lots) / prior5 if prior5 > 0 else 0.0
        return make_row(x, i, profile, "接近突破", pivot, ratio)
    return None


def send_line_summary(rows: list[dict], trade_date: str) -> None:
    """Send the overall top 10 VCP candidates ranked by reward-risk."""
    force_notify = os.environ.get("VCP_FORCE_NOTIFY", "").strip() == "1"
    if not rows and not force_notify:
        print("No VCP candidates; LINE notification skipped")
        return
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        print("LINE token missing; VCP results were written to CSV only")
        return

    blocks = [
        f"🦞 VCP三階段雷達（市場先生文章版）｜{trade_date}\n"
        "條件：第二階段上升趨勢＋2～6次價量收縮＋樞紐點；依風報比排序。"
    ]
    if not rows:
        blocks.append("✅ LINE測試成功｜目前資料庫無VCP候選")
    else:
        shown = min(len(rows), 10)
        blocks.append(f"📊 風報比總榜（顯示{shown}檔／共{len(rows)}檔）")
        for rank, row in enumerate(rows[:10], start=1):
            if row["upper_pivot"] != "":
                upside_line = (
                    f"上方樞紐{row['upper_pivot']}｜上方空間{row['upside_to_upper_pivot_pct']}%"
                    f"｜風報比{row['reward_risk_ratio']}"
                )
            else:
                upside_line = "60日內無明確上方樞紐｜風報比暫無法估算"
            blocks.append(
                f"{rank}. {row['code']} {row['name']}｜{row['vcp_stage']}\n"
                f"收{row['close']}｜突破樞紐{row['pivot']}｜品質{row['quality_score']}分\n"
                f"{upside_line}\n"
                f"最後收縮下緣{row['support_lower']}｜停損{row['stop_price']}\n"
                f"行動：{row['action']}"
            )

    # Keep every stock block intact and stay below LINE's 5,000-character limit.
    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= 4800:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = block
    if current:
        messages.append(current)

    total = len(messages)
    if total > 1:
        messages = [
            f"第{index}/{total}則\n{text}"
            for index, text in enumerate(messages, start=1)
        ]

    # LINE accepts at most five message objects per broadcast request.
    for offset in range(0, len(messages), 5):
        batch = messages[offset:offset + 5]
        payload = json.dumps(
            {"messages": [{"type": "text", "text": text} for text in batch]},
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
            with urllib.request.urlopen(request, timeout=20) as response:
                print(
                    "VCP LINE status:",
                    response.status,
                    f"batch={offset // 5 + 1}",
                    f"messages={len(batch)}",
                )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"VCP LINE failed: {exc.code} {detail}") from exc


def main() -> int:
    if not DB.exists():
        raise SystemExit(f"Database missing: {DB}")
    market = read_market()
    rows = []
    for _, group in market.groupby("code", sort=False):
        result = classify_latest(group)
        if result:
            rows.append(result)
    rows.sort(key=lambda r: (
        r["reward_risk_ratio"] == "",
        -float(r["reward_risk_ratio"]) if r["reward_risk_ratio"] != "" else 0.0,
        -r["quality_score"],
        r["code"],
    ))
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "latest_trade_date": market.date.max().strftime("%Y-%m-%d"),
        "total": len(rows),
        "stages": {stage: sum(r["vcp_stage"] == stage for r in rows)
                   for stage in ("接近突破", "當日突破", "突破後回踩")},
        "formal_recommendations_changed": False,
    }
    print(json.dumps(summary, ensure_ascii=False))
    send_line_summary(rows, summary["latest_trade_date"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
