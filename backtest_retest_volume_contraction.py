#!/usr/bin/env python3
"""Backtest: require volume contraction only for 突破回踩不破."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import buy_signal as engine
import backtest_new_rules as backtest

BASE = Path(__file__).resolve().parent
ORIGINAL_DETECT = engine.detect_true_breakout


def detect_true_breakout_with_retest_volume(code, x):
    setup, signal = ORIGINAL_DETECT(code, x)
    if signal is None or signal.get("signal_route") != "突破回踩不破":
        return setup, signal

    i = len(x) - 1
    breakout_date = pd.Timestamp(signal["breakout_date"])
    matched = x.index[x["date"] == breakout_date].tolist()
    if not matched or i < 5:
        return setup, None

    break_i = int(matched[-1])
    current_lots = float(x.iloc[i].volume_lots)
    breakout_lots = float(x.iloc[break_i].volume_lots)
    prev5_avg_lots = float(x.iloc[i - 5:i]["volume_lots"].mean())
    volume_contracted = (
        current_lots < breakout_lots
        and current_lots < prev5_avg_lots
    )

    setup["retest_volume_lots"] = round(current_lots, 0)
    setup["breakout_volume_lots"] = round(breakout_lots, 0)
    setup["prev5_avg_volume_lots"] = round(prev5_avg_lots, 0)
    setup["retest_volume_contracted"] = volume_contracted

    if volume_contracted:
        signal["retest_volume_lots"] = round(current_lots, 0)
        signal["breakout_volume_lots"] = round(breakout_lots, 0)
        signal["prev5_avg_volume_lots"] = round(prev5_avg_lots, 0)
        signal["retest_volume_contracted"] = True
        return setup, signal

    # Keep the original 突破後站穩 route unchanged. If this same session
    # independently satisfies it, reclassify instead of discarding it.
    prior = x.iloc[break_i - engine.LOOKBACK:break_i]
    platform_high = float(prior["high"].max())
    post = x.iloc[break_i + 1:i + 1]
    held_structure = bool(
        len(post) >= 1
        and (post["close"] >= platform_high * (1.0 - engine.BREAKOUT_HOLD_TOL)).all()
    )
    t = x.iloc[i]
    close = float(t.close)
    spread = float(t.high) - float(t.low)
    location = (close - float(t.low)) / spread if spread > 0 else 1.0
    lots = float(t.volume_lots) if pd.notna(t.volume_lots) else 0.0
    turnover = float(t.turnover) if pd.notna(t.turnover) else 0.0
    liquid_today = lots >= engine.MIN_VOLUME_LOTS and turnover >= engine.MIN_TURNOVER
    extension = close / platform_high - 1.0
    elapsed = i - break_i
    stand_confirmed = (
        elapsed <= engine.BREAKOUT_STAND_DAYS
        and held_structure
        and close >= platform_high * (1.0 + engine.BREAKOUT_MIN)
        and close > float(t.open)
        and location >= engine.CLOSE_LOCATION_MIN
        and liquid_today
        and extension <= engine.MAX_STRUCTURE_EXTENSION
    )
    if not stand_confirmed:
        return setup, None

    signal["signal_route"] = "突破後站穩"
    signal["confirmation_mode"] = "突破後站穩"
    signal["baseline_entry"] = round(platform_high * (1.0 + engine.BREAKOUT_MIN), 2)
    signal["pattern_key"] = (
        f"突破後站穩:{signal['breakout_date']}:{platform_high:.2f}"
    )
    return setup, signal


engine.detect_true_breakout = detect_true_breakout_with_retest_volume
backtest.OUT_JSON = BASE / "backtest_retest_volume_contraction_results.json"
backtest.OUT_MD = BASE / "backtest_retest_volume_contraction_report.md"
backtest.main()

result = json.loads(backtest.OUT_JSON.read_text(encoding="utf-8"))
result["new_rule_scope"] = "僅突破回踩不破"
result["new_rule"] = [
    "回踩確認日成交量低於突破日成交量",
    "回踩確認日成交量低於前5個交易日平均量",
]
result["unchanged_routes"] = ["破底翻", "突破後站穩"]
result["formal_system_changed"] = False
backtest.OUT_JSON.write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

report = backtest.OUT_MD.read_text(encoding="utf-8")
report = report.replace(
    "# 破底翻＋突破後站穩／回踩不破｜六個月走勢回測",
    "# 突破回踩加入量縮確認｜六個月比較回測",
    1,
)
lines = report.splitlines()
insert_at = 2 if len(lines) >= 2 else len(lines)
lines.insert(insert_at, "- 新條件只套用突破回踩不破：回踩量低於突破日量及前5日均量")
lines.insert(insert_at + 1, "- 破底翻、突破後站穩：維持原條件")
lines.insert(insert_at + 2, "- 正式系統：先不套用，待勝率比較")
backtest.OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
