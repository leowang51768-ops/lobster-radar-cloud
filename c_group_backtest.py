#!/usr/bin/env python3
"""Point-in-time C-group experiment on historical snapshots (no production writes).

Uses existing official SQLite 6-month history, recomputes features for each date
WITHOUT future prices in ranking.  Entry next session open, fixed 3/5 session
holding periods, intraday structural stop, buy/sell fees, sale tax and slippage.
Historical sector score deliberately zero until point-in-time sector mapping exists.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import pandas as pd
import c_group_scan as c

BUY_FEE = .001425
SELL_FEE = .001425
SELL_TAX = .003
SLIPPAGE = .001
BASE = Path(__file__).resolve().parent


def simulate(group: pd.DataFrame, signal_date: pd.Timestamp, stop: float, days: int):
    rows = group.sort_values("date").reset_index(drop=True)
    indices = rows.index[rows.date.eq(signal_date)].tolist()
    if not indices:
        return None
    start = indices[0] + 1
    end = start + days - 1
    if end >= len(rows):
        return None
    opened = float(rows.iloc[start].open)
    if opened <= 0 or not pd.notna(opened):
        return None
    entry = opened * (1 + SLIPPAGE)
    exit_price = float(rows.iloc[end].close) * (1 - SLIPPAGE)
    exit_day = rows.iloc[end].date.strftime("%Y-%m-%d")
    reason = "持有到期"
    for j in range(start, end + 1):
        day = rows.iloc[j]
        if float(day.low) <= stop:
            exit_price = min(float(day.open), stop) * (1 - SLIPPAGE)
            exit_day = day.date.strftime("%Y-%m-%d")
            reason = "停損"
            break
    net = exit_price * (1 - SELL_FEE - SELL_TAX) / (entry * (1 + BUY_FEE)) - 1
    return {"net_return": round(net, 8), "exit_date": exit_day, "exit_reason": reason,
            "actual_entry": round(entry, 4), "actual_exit": round(exit_price, 4)}


def metrics(rows: list[dict]):
    vals = [r["net_return"] for r in rows]
    if not vals:
        return {"samples": 0, "win_rate_pct": None, "average_return_pct": None,
                "expectancy_pct": None, "profit_loss_ratio": None,
                "max_drawdown_pct": None}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    rr = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses and sum(losses) else None
    # Equal-weight sequential trade-level compounded proxy, not calendar-portfolio MDD.
    equity = peak = 1.0
    drawdown = 0.0
    for v in vals:
        equity *= 1 + v
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
    return {"samples": len(vals), "win_rate_pct": round(100 * len(wins) / len(vals), 2),
            "average_return_pct": round(100 * sum(vals) / len(vals), 3),
            "expectancy_pct": round(100 * sum(vals) / len(vals), 3),
            "profit_loss_ratio": round(rr, 3) if rr is not None else None,
            "max_drawdown_pct": round(100 * drawdown, 3)}


def run(sessions: int, output: Path):
    market = c.core.read_market().sort_values(["code", "date"])
    dates = sorted(market.date.dropna().unique())
    # Warmup on the official 6-month history, reserve 5 sessions for complete outcomes.
    valid = dates[80:-5]
    if sessions:
        valid = valid[-sessions:]
    groups = {str(code): g.reset_index(drop=True)
              for code, g in market.groupby("code", sort=False)}
    trades = {"all_eligible": {3: [], 5: []},
              "c_top5": {3: [], 5: []},
              "reversal_only": {3: [], 5: []}}
    daily = []
    for day in valid:
        frame = pd.concat([g[g.date.le(day)] for g in groups.values()], ignore_index=True)
        ranked, observations, stats = c.build_candidates(frame)
        daily.append({"date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                      "eligible": len(ranked), "formal": min(5, len(ranked)),
                      "reversal": stats["reversal"], "retest": stats["retest"]})
        for i, signal in enumerate(ranked):
            stop = float(signal.get("stop_price") or 0)
            if stop <= 0:
                continue
            code = str(signal["code"])
            for days in (3, 5):
                sim = simulate(groups[code], pd.Timestamp(day), stop, days)
                if sim is None:
                    continue
                row = {"date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                       "code": code, "route": signal["kind"],
                       "rank": i + 1, "quality_score": signal["quality"]["total"],
                       **sim}
                trades["all_eligible"][days].append(row)
                if i < 5:
                    trades["c_top5"][days].append(row)
                if signal["kind"] == "破底翻":
                    trades["reversal_only"][days].append(row)
        print(json.dumps(daily[-1], ensure_ascii=False), flush=True)
    report = {"source": "lobster_tw_6m_prices.sqlite", "sessions": len(valid),
              "window": [str(pd.Timestamp(valid[0]).date()), str(pd.Timestamp(valid[-1]).date())] if len(valid) else [],
              "execution": "signal-close known; next-open buy; intraday stop; 3/5-session exit; net of assumed costs",
              "drawdown_note": "Sequential trade proxy only; overlapping holdings mean this is NOT portfolio maximum drawdown",
              "comparison_note": "Reversal-only subset is a same-snapshot proxy, not an independently reconstructed production notification ledger",
              "daily": daily,
              "metrics": {name: {f"{days}d": metrics(rows[days]) for days in (3,5)}
                          for name, rows in trades.items()},
              "trades": {name: {f"{days}d": rows[days] for days in (3,5)}
                         for name, rows in trades.items()}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT "+json.dumps({"window":report["window"],"sessions":report["sessions"],
                                "metrics":report["metrics"]}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sessions", type=int, default=12, help="0=all eligible sessions")
    p.add_argument("--output", type=Path, default=BASE / "c_group_backtest_results.json")
    args = p.parse_args()
    run(args.sessions, args.output)
