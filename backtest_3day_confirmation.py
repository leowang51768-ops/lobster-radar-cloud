#!/usr/bin/env python3
"""What-if backtest: shorten breakout confirmation window from 5 to 3 sessions."""
from __future__ import annotations

import json
from pathlib import Path

import backtest_new_rules as backtest
import buy_signal as engine

BASE = Path(__file__).resolve().parent
engine.BREAKOUT_CONFIRM_DAYS = 3
backtest.OUT_JSON = BASE / "backtest_3day_confirmation_results.json"
backtest.OUT_MD = BASE / "backtest_3day_confirmation_report.md"

backtest.main()

result = json.loads(backtest.OUT_JSON.read_text(encoding="utf-8"))
result["breakout_confirmation_days"] = 3
result["comparison_target"] = "live rule uses 5 sessions"
backtest.OUT_JSON.write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

report = backtest.OUT_MD.read_text(encoding="utf-8")
report = report.replace(
    "# 破底翻＋突破後站穩／回踩不破｜六個月走勢回測",
    "# 破底翻＋突破確認｜3個交易日方案回測",
    1,
)
lines = report.splitlines()
insert_at = 2 if len(lines) >= 2 else len(lines)
lines.insert(insert_at, "- 突破確認期限：3個交易日（正式系統目前仍為5日）")
backtest.OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
