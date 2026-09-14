#!/usr/bin/env python3
"""What-if backtest: current patterns plus two 20MA trend protections."""
from __future__ import annotations

import json
from pathlib import Path

import backtest_new_rules as backtest

BASE = Path(__file__).resolve().parent
ORIGINAL_COLLECT = backtest.collect_signals


def collect_with_20ma(market):
    signals = ORIGINAL_COLLECT(market)
    kept = []
    for signal in signals:
        i = int(signal["signal_i"])
        closes = [float(value) for value in signal["closes"]]
        if i < 20:
            continue
        ma20 = sum(closes[i - 19:i + 1]) / 20.0
        prev_ma20 = sum(closes[i - 20:i]) / 20.0
        close = closes[i]
        if close > ma20 and ma20 >= prev_ma20:
            signal["ma20_at_signal"] = round(ma20, 4)
            signal["prev_ma20_at_signal"] = round(prev_ma20, 4)
            kept.append(signal)
    return kept


backtest.collect_signals = collect_with_20ma
backtest.OUT_JSON = BASE / "backtest_20ma_protection_results.json"
backtest.OUT_MD = BASE / "backtest_20ma_protection_report.md"
backtest.main()

result = json.loads(backtest.OUT_JSON.read_text(encoding="utf-8"))
result["extra_protection"] = [
    "訊號日收盤價高於20MA",
    "訊號日20MA大於或等於前一日20MA",
]
result["formal_system_changed"] = False
backtest.OUT_JSON.write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

report = backtest.OUT_MD.read_text(encoding="utf-8")
report = report.replace(
    "# 破底翻＋突破後站穩／回踩不破｜六個月走勢回測",
    "# 新型態＋兩個20MA保護｜六個月比較回測",
    1,
)
lines = report.splitlines()
insert_at = 2 if len(lines) >= 2 else len(lines)
lines.insert(insert_at, "- 額外保護：收盤高於20MA，且20MA不低於前一日")
lines.insert(insert_at + 1, "- DIF與基本面：不作硬性條件")
lines.insert(insert_at + 2, "- 正式系統：尚未套用，本報告僅為比較回測")
backtest.OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
