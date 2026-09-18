#!/usr/bin/env python3
"""A/B walk-forward backtest: core routes vs core plus structural fakeout."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import buy_signal as engine

BASE = Path(__file__).resolve().parent
OUT_JSON = BASE / "backtest_fakeout_ab_results.json"
OUT_MD = BASE / "backtest_fakeout_ab_report.md"
HORIZONS = (5, 10, 20)
ROUND_TRIP_COST = 0.00585
COOLDOWN_SESSIONS = 10


def market_return20_by_date(market: pd.DataFrame) -> dict:
    x = market.sort_values(["code", "date"]).copy()
    x["ret20"] = x.groupby("code", sort=False)["close"].pct_change(20)
    return x.groupby("date")["ret20"].median().to_dict()


def collect(market: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    market_ret = market_return20_by_date(market)
    core, fakeout = [], []
    detectors = (
        ("core", engine.detect_false_break_reversal),
        ("core", engine.detect_true_breakout),
        ("fakeout", engine.detect_fakeout_recovery),
    )
    for raw_code, group in market.groupby("code", sort=False):
        code = str(raw_code)
        name = str(group.iloc[-1]["name"])
        x = engine.prepare(group)
        seen = set()
        start = max(
            engine.LOOKBACK + engine.FALSE_BREAK_RECOVERY_DAYS - 1,
            engine.FAKEOUT_PRE_WINDOW + engine.FAKEOUT_RECOVERY_MAX_DAYS + 1,
            65,
        )
        for i in range(start, len(x) - 1):
            hist = x.iloc[:i + 1]
            date = hist.iloc[-1].date
            benchmark = float(market_ret.get(date, 0.0))
            for family, detector in detectors:
                setup, signal = detector(code, hist)
                signal = engine.apply_new_plan_gate(signal, setup, hist, benchmark)
                if signal is None:
                    continue
                key = str(signal["pattern_key"])
                if key in seen:
                    continue
                seen.add(key)
                row = {
                    **signal,
                    "name": name,
                    "signal_i": i,
                    "dates": x["date"].tolist(),
                    "opens": x["open"].astype(float).tolist(),
                    "highs": x["high"].astype(float).tolist(),
                    "lows": x["low"].astype(float).tolist(),
                    "closes": x["close"].astype(float).tolist(),
                }
                (core if family == "core" else fakeout).append(row)
    return core, fakeout


def cooldown(signals: list[dict]) -> list[dict]:
    selected, last = [], {}
    for signal in sorted(signals, key=lambda s: (s["date"], s["code"], s["signal_route"])):
        key = (signal["code"], signal["signal_route"])
        idx = int(signal["signal_i"])
        if key in last and idx - last[key] < COOLDOWN_SESSIONS:
            continue
        last[key] = idx
        selected.append(signal)
    return selected


def evaluate(signal: dict, horizon: int) -> dict | None:
    signal_i = int(signal["signal_i"])
    entry_i, target_i = signal_i + 1, signal_i + horizon
    if target_i >= len(signal["closes"]):
        return None
    entry = float(signal["opens"][entry_i])
    if not math.isfinite(entry) or entry <= 0:
        return None
    stop = float(signal["support_lower"]) * (1.0 - engine.SUPPORT_BREAK_TOL)
    exit_i, stopped = target_i, False
    for j in range(entry_i, target_i + 1):
        if float(signal["closes"][j]) < stop:
            exit_i, stopped = j, True
            break
    exit_price = float(signal["closes"][exit_i])
    net = exit_price / entry - 1.0 - ROUND_TRIP_COST
    lows = signal["lows"][entry_i:exit_i + 1]
    return {
        "route": signal["signal_route"],
        "net": net,
        "win": net > 0,
        "stopped": stopped,
        "mae": min(lows) / entry - 1.0,
    }


def summarize(signals: list[dict], horizon: int) -> dict:
    rows = [row for s in signals if (row := evaluate(s, horizon)) is not None]
    if not rows:
        return {"samples": 0, "win_rate": None, "avg_net": None, "stop_rate": None, "worst_mae": None}
    frame = pd.DataFrame(rows)
    return {
        "samples": len(frame),
        "win_rate": round(float(frame["win"].mean()) * 100, 2),
        "avg_net": round(float(frame["net"].mean()) * 100, 2),
        "stop_rate": round(float(frame["stopped"].mean()) * 100, 2),
        "worst_mae": round(float(frame["mae"].min()) * 100, 2),
    }


def package(signals: list[dict]) -> dict:
    result = {}
    for horizon in HORIZONS:
        result[str(horizon)] = {"全部": summarize(signals, horizon)}
        for route in ("破底翻", "突破後站穩", "突破回踩不破", "假摔收復確認"):
            result[str(horizon)][route] = summarize(
                [s for s in signals if s["signal_route"] == route], horizon
            )
    return result


def pct(value) -> str:
    return "—" if value is None else f"{value:.2f}%"


def main() -> None:
    market = engine.read_market()
    core_raw, fakeout_raw = collect(market)
    core = cooldown(core_raw)
    fakeout = cooldown(fakeout_raw)
    combined = cooldown(core_raw + fakeout_raw)
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_start": market["date"].min().strftime("%Y-%m-%d"),
        "data_end": market["date"].max().strftime("%Y-%m-%d"),
        "entry": "訊號隔日開盤",
        "round_trip_cost_pct": ROUND_TRIP_COST * 100,
        "core_signal_count": len(core),
        "fakeout_signal_count": len(fakeout),
        "core": package(core),
        "fakeout_only": package(fakeout),
        "core_plus_fakeout": package(combined),
    }
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 結構假摔 A/B 回測",
        "",
        f"- 資料：{results['data_start']}～{results['data_end']}",
        "- A組：破底翻＋突破確認",
        "- B組：A組＋修正版結構假摔",
        "- 假摔限定：明確支撐至少2次測試、跌破支撐至少1%、急跌至少8%、3～10日收復、未再破低、最後突破原壓力。",
        "- 30MA不具假摔判定資格；所有訊號套用流動性、8%防追價、相對強度、上方空間與60MA保護。",
        "",
        "| 期間 | 組別 | 樣本 | 稅費後勝率 | 平均淨報酬 | 停損率 | 最差持有回撤 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for horizon in HORIZONS:
        for key, label in (("core", "只留破底翻＋突破"), ("fakeout_only", "假摔單獨"), ("core_plus_fakeout", "加入假摔")):
            row = results[key][str(horizon)]["全部"]
            lines.append(
                f"| {horizon}日 | {label} | {row['samples']} | {pct(row['win_rate'])} | "
                f"{pct(row['avg_net'])} | {pct(row['stop_rate'])} | {pct(row['worst_mae'])} |"
            )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
