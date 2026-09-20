#!/usr/bin/env python3
"""Point-in-time A/B/C validation for breakout-retest support confluence V1.0."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from support_retest_watch import classify_latest, read_market

BASE = Path(__file__).resolve().parent
RESULT = BASE / "backtest_support_confluence_results.json"
TRADES = BASE / "backtest_support_confluence_trades.csv"
WARMUP = 80
BUY_FEE = 0.001425
SELL_FEE = 0.001425
SELL_TAX = 0.003
SLIPPAGE = 0.001


def simulate(group: pd.DataFrame, signal_i: int, days: int, stop: float) -> dict | None:
    entry_i = signal_i + 1
    exit_i = entry_i + days - 1
    if exit_i >= len(group):
        return None
    entry = float(group.iloc[entry_i].open) * (1.0 + SLIPPAGE)
    exit_price = float(group.iloc[exit_i].close) * (1.0 - SLIPPAGE)
    exit_reason = f"持有{days}日"
    for j in range(entry_i, exit_i + 1):
        row = group.iloc[j]
        if float(row.low) <= stop:
            # A gap through the stop is filled at the worse, actually available open.
            exit_price = min(stop, float(row.open)) * (1.0 - SLIPPAGE)
            exit_reason = "停損"
            break
    net_entry = entry * (1.0 + BUY_FEE)
    net_exit = exit_price * (1.0 - SELL_FEE - SELL_TAX)
    return {
        f"return_{days}d": net_exit / net_entry - 1.0,
        f"exit_{days}d": round(exit_price, 4),
        f"exit_reason_{days}d": exit_reason,
    }


def max_drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def metrics(frame: pd.DataFrame, days: int) -> dict:
    column = f"return_{days}d"
    values = frame[column].dropna().astype(float)
    wins = values[values > 0]
    losses = values[values <= 0]
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    return {
        "samples": int(len(values)),
        "win_rate_pct": round(float((values > 0).mean() * 100), 2) if len(values) else None,
        "average_return_pct": round(float(values.mean() * 100), 3) if len(values) else None,
        "profit_loss_ratio": round(avg_win / abs(avg_loss), 3) if avg_loss < 0 else None,
        "max_drawdown_pct": round(max_drawdown(values.tolist()) * 100, 3) if len(values) else None,
        "expectancy_pct": round(float(values.mean() * 100), 3) if len(values) else None,
    }


def main() -> int:
    market = read_market()
    trades = []
    for raw_code, raw_group in market.groupby("code", sort=False):
        group = raw_group.sort_values("date").reset_index(drop=True)
        for i in range(WARMUP, len(group) - 1):
            signal = classify_latest(group.iloc[: i + 1])
            if signal is None:
                continue
            stop = float(signal["stop_price"])
            row = {
                "signal_date": signal["date"],
                "code": str(raw_code),
                "name": signal["name"],
                "group": "B" if signal["confluence"] else "C",
                "confluence": bool(signal["confluence"]),
                "confluence_categories": int(signal["confluence_categories"]),
                "confluence_evidence": signal["confluence_evidence"],
                "pivot": signal["pivot"],
                "stop_price": stop,
            }
            for days in (3, 5):
                outcome = simulate(group, i, days, stop)
                if outcome:
                    row.update(outcome)
            trades.append(row)

    frame = pd.DataFrame(trades)
    if frame.empty:
        frame = pd.DataFrame(columns=[
            "signal_date", "code", "name", "group", "confluence",
            "confluence_categories", "confluence_evidence", "pivot", "stop_price",
            "return_3d", "return_5d",
        ])
    frame.to_csv(TRADES, index=False, encoding="utf-8-sig")
    groups = {
        "A_all_breakout_retests": frame,
        "B_with_confluence": frame[frame["group"] == "B"],
        "C_without_confluence": frame[frame["group"] == "C"],
    }
    report = {
        "method": {
            "entry": "訊號次一交易日開盤，加0.1%滑價",
            "stop": "原突破樞紐下緣1%；跳空越過時按較差開盤價",
            "costs": {"buy_fee": BUY_FEE, "sell_fee": SELL_FEE, "sell_tax": SELL_TAX},
            "lookahead_bias": "每一訊號僅傳入當日以前資料；POC亦只使用突破日前資料",
            "D_reversal": "破底翻維持獨立既有回測，不與A/B/C重複合併",
        },
        "signal_count": int(len(frame)),
        "confluence_share_pct": round(float(frame["confluence"].mean() * 100), 2) if len(frame) else None,
        "groups": {
            name: {"3_day": metrics(data, 3), "5_day": metrics(data, 5)}
            for name, data in groups.items()
        },
    }
    RESULT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
