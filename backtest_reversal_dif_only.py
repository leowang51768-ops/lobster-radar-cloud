#!/usr/bin/env python3
"""What-if backtest: apply DIF convergence only to 破底翻."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import backtest_new_rules as backtest

BASE = Path(__file__).resolve().parent
ORIGINAL_COLLECT = backtest.collect_signals


def collect_with_reversal_dif_only(market):
    signals = ORIGINAL_COLLECT(market)
    kept = []
    for signal in signals:
        if signal["signal_route"] != "破底翻":
            kept.append(signal)
            continue

        i = int(signal["signal_i"])
        closes = pd.Series([float(value) for value in signal["closes"]])
        dif = closes.ewm(span=6, adjust=False).mean() - closes.ewm(span=13, adjust=False).mean()
        if i < 1 or pd.isna(dif.iloc[i]) or pd.isna(dif.iloc[i - 1]):
            continue
        if float(dif.iloc[i]) >= float(dif.iloc[i - 1]):
            signal["dif_at_signal"] = round(float(dif.iloc[i]), 6)
            signal["prev_dif_at_signal"] = round(float(dif.iloc[i - 1]), 6)
            kept.append(signal)
    return kept


backtest.collect_signals = collect_with_reversal_dif_only
backtest.OUT_JSON = BASE / "backtest_reversal_dif_only_results.json"
backtest.OUT_MD = BASE / "backtest_reversal_dif_only_report.md"
backtest.main()

result = json.loads(backtest.OUT_JSON.read_text(encoding="utf-8"))
result["extra_protection_scope"] = "僅破底翻"
result["extra_protection"] = [
    "破底翻訊號日DIF大於或等於前一日DIF（收斂、持平或轉升）",
]
result["unchanged_routes"] = ["突破後站穩", "突破回踩不破"]
result["formal_system_changed"] = False
backtest.OUT_JSON.write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

report = backtest.OUT_MD.read_text(encoding="utf-8")
report = report.replace(
    "# 破底翻＋突破後站穩／回踩不破｜六個月走勢回測",
    "# 僅破底翻加入DIF收斂｜六個月比較回測",
    1,
)
lines = report.splitlines()
insert_at = 2 if len(lines) >= 2 else len(lines)
lines.insert(insert_at, "- 額外條件只套用破底翻：當日DIF大於或等於前一日DIF")
lines.insert(insert_at + 1, "- DIF可位於零軸下，不要求翻正或黃金交叉")
lines.insert(insert_at + 2, "- 突破後站穩、突破回踩不破：維持原條件")
lines.insert(insert_at + 3, "- 正式系統：尚未套用，本報告僅為比較回測")
backtest.OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
