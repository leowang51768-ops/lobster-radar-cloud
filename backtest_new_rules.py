#!/usr/bin/env python3
"""Walk-forward backtest for the live 破底翻＋突破確認 rules."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import buy_signal as engine

BASE = Path(__file__).resolve().parent
OUT_JSON = BASE / "backtest_new_rules_results.json"
OUT_MD = BASE / "backtest_new_rules_report.md"
HORIZONS = (5, 10, 20)
ROUND_TRIP_COST = 0.00585
COOLDOWN_SESSIONS = 10


def collect_signals(market: pd.DataFrame) -> list[dict]:
    signals = []
    for raw_code, group in market.groupby("code", sort=False):
        code = str(raw_code)
        name = str(group.iloc[-1]["name"])
        x = engine.prepare(group)
        seen_keys = set()
        for i in range(engine.LOOKBACK + engine.FALSE_BREAK_RECOVERY_DAYS - 1, len(x) - 1):
            hist = x.iloc[: i + 1]
            for detector in (engine.detect_false_break_reversal, engine.detect_true_breakout):
                _setup, signal = detector(code, hist)
                if signal is None:
                    continue
                key = str(signal["pattern_key"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                signals.append({
                    **signal,
                    "name": name,
                    "signal_i": i,
                    "dates": x["date"].tolist(),
                    "opens": x["open"].astype(float).tolist(),
                    "highs": x["high"].astype(float).tolist(),
                    "lows": x["low"].astype(float).tolist(),
                    "closes": x["close"].astype(float).tolist(),
                })
    return signals


def apply_cooldown(signals: list[dict]) -> list[dict]:
    selected = []
    last_index = {}
    for signal in sorted(signals, key=lambda s: (s["date"], s["code"], s["signal_route"])):
        key = (signal["code"], signal["signal_route"])
        current = int(signal["signal_i"])
        previous = last_index.get(key)
        if previous is not None and current - previous < COOLDOWN_SESSIONS:
            continue
        last_index[key] = current
        selected.append(signal)
    return selected


def evaluate(signal: dict, horizon: int) -> dict | None:
    signal_i = int(signal["signal_i"])
    entry_i = signal_i + 1
    target_i = signal_i + horizon
    if target_i >= len(signal["closes"]):
        return None

    entry = float(signal["opens"][entry_i])
    if not math.isfinite(entry) or entry <= 0:
        return None

    support = float(signal["support_lower"])
    stop_line = support * (1.0 - engine.SUPPORT_BREAK_TOL)
    exit_i = target_i
    stopped = False
    for j in range(entry_i, target_i + 1):
        if float(signal["closes"][j]) < stop_line:
            exit_i = j
            stopped = True
            break

    exit_price = float(signal["closes"][exit_i])
    gross = exit_price / entry - 1.0
    net = gross - ROUND_TRIP_COST
    window_lows = signal["lows"][entry_i:exit_i + 1]
    window_highs = signal["highs"][entry_i:exit_i + 1]
    return {
        "date": signal["date"],
        "code": signal["code"],
        "name": signal["name"],
        "route": signal["signal_route"],
        "horizon": horizon,
        "entry_date": signal["dates"][entry_i].strftime("%Y-%m-%d"),
        "entry_price": round(entry, 4),
        "exit_date": signal["dates"][exit_i].strftime("%Y-%m-%d"),
        "exit_price": round(exit_price, 4),
        "stopped": stopped,
        "gross_return": gross,
        "net_return": net,
        "win_gross": gross > 0,
        "win_net": net > 0,
        "mae": min(window_lows) / entry - 1.0,
        "mfe": max(window_highs) / entry - 1.0,
    }


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {
            "samples": 0, "gross_win_rate": None, "net_win_rate": None,
            "avg_gross_return": None, "avg_net_return": None,
            "median_net_return": None, "stop_rate": None,
            "avg_mae": None, "avg_mfe": None,
        }
    frame = pd.DataFrame(rows)
    return {
        "samples": int(len(frame)),
        "gross_win_rate": round(float(frame["win_gross"].mean()) * 100, 2),
        "net_win_rate": round(float(frame["win_net"].mean()) * 100, 2),
        "avg_gross_return": round(float(frame["gross_return"].mean()) * 100, 2),
        "avg_net_return": round(float(frame["net_return"].mean()) * 100, 2),
        "median_net_return": round(float(frame["net_return"].median()) * 100, 2),
        "stop_rate": round(float(frame["stopped"].mean()) * 100, 2),
        "avg_mae": round(float(frame["mae"].mean()) * 100, 2),
        "avg_mfe": round(float(frame["mfe"].mean()) * 100, 2),
    }


def result_set(signals: list[dict]) -> tuple[dict, list[dict]]:
    evaluated = []
    summary = {}
    for horizon in HORIZONS:
        horizon_rows = []
        for signal in signals:
            row = evaluate(signal, horizon)
            if row is not None:
                horizon_rows.append(row)
                evaluated.append(row)
        summary[str(horizon)] = {"全部": summarize(horizon_rows)}
        for route in ("破底翻", "突破後站穩", "突破回踩不破"):
            summary[str(horizon)][route] = summarize(
                [row for row in horizon_rows if row["route"] == route]
            )
    return summary, evaluated


def pct(value) -> str:
    return "—" if value is None else f"{value:.2f}%"


def render_table(summary: dict, title: str) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| 期間 | 型態 | 樣本 | 稅費後勝率 | 平均淨報酬 | 中位淨報酬 | 停損率 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for horizon in HORIZONS:
        for route in ("全部", "破底翻", "突破後站穩", "突破回踩不破"):
            row = summary[str(horizon)][route]
            lines.append(
                f"| {horizon}日 | {route} | {row['samples']} | "
                f"{pct(row['net_win_rate'])} | {pct(row['avg_net_return'])} | "
                f"{pct(row['median_net_return'])} | {pct(row['stop_rate'])} |"
            )
    lines.append("")
    return lines


def main() -> None:
    market = engine.read_market()
    raw_signals = collect_signals(market)
    independent_signals = apply_cooldown(raw_signals)
    raw_summary, raw_rows = result_set(raw_signals)
    independent_summary, independent_rows = result_set(independent_signals)

    start_date = market["date"].min().strftime("%Y-%m-%d")
    end_date = market["date"].max().strftime("%Y-%m-%d")
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": "破底翻+突破確認-v2",
        "data_start": start_date,
        "data_end": end_date,
        "entry": "訊號隔日開盤",
        "round_trip_cost_pct": ROUND_TRIP_COST * 100,
        "stop": "收盤跌破支撐下緣0.5%",
        "raw_signal_count": len(raw_signals),
        "independent_signal_count": len(independent_signals),
        "independent_cooldown_sessions": COOLDOWN_SESSIONS,
        "raw": raw_summary,
        "independent": independent_summary,
        "evaluated_rows": independent_rows,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 破底翻＋突破後站穩／回踩不破｜六個月走勢回測",
        "",
        f"- 官方資料區間：{start_date}～{end_date}",
        "- 進場：訊號隔一交易日開盤（避免使用收盤後才知道的資訊）",
        f"- 交易成本：來回估算 {ROUND_TRIP_COST * 100:.3f}%",
        "- 停損：收盤有效跌破結構支撐下緣0.5%",
        f"- 獨立樣本：同股票同型態至少間隔{COOLDOWN_SESSIONS}個交易日",
        "",
    ]
    lines += render_table(independent_summary, "主要結果：獨立訊號")
    lines += render_table(raw_summary, "敏感度檢查：全部原始訊號")
    lines += [
        "## 判讀限制",
        "",
        "- 六個月資料可用來初篩規則，但不足以涵蓋完整多空循環。",
        "- 最近20個交易日尚無完整20日後績效，因此20日樣本會少於5日樣本。",
        "- 同股連續突破高度相關，決策時應以「獨立訊號」表為主。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "data_start": start_date,
        "data_end": end_date,
        "raw_signals": len(raw_signals),
        "independent_signals": len(independent_signals),
        "independent_summary": independent_summary,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

