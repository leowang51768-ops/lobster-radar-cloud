#!/usr/bin/env python3
"""C-group experimental scanner. Never writes production files or sends LINE.

Full TWSE/TPEx universe -> current-day liquidity >300 lots and >=30m TWD ->
existing false-break detector or high-quality support-confluence retest ->
one distinct stock per date, ranked; at most five formal *test* candidates.
VCP near/instant breakout stays in the existing observation-only scanner.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import pandas as pd

import buy_signal as core
import support_retest_watch as retest

BASE = Path(__file__).resolve().parent
MIN_LOTS_EXCLUSIVE = 300.0
MIN_TURNOVER = 30_000_000.0
MAX_FORMAL = 5


def liquid_today(row: pd.Series) -> bool:
    """Official database volume is in shares; core.read_market converts to lots."""
    return (
        math.isfinite(float(row.volume_lots))
        and float(row.volume_lots) > MIN_LOTS_EXCLUSIVE
        and math.isfinite(float(row.turnover))
        and float(row.turnover) >= MIN_TURNOVER
    )


def score(signal: dict, kind: str, latest: pd.Series, sector_bonus: float = 0.0) -> dict:
    """Transparent experimental ranking, not a predicted win probability.

    Sector bonus is zero until a point-in-time sector mapping is available.
    Do not manufacture historical fundamentals or use future sector tags.
    """
    if kind == "破底翻":
        structure = min(40.0, 24.0 + 3.0 * float(signal.get("support_touches") or 0)
                        + 2.0 * float(signal.get("evidence_count") or 0))
        price_volume = min(25.0, 12.0 + 2.0 * float(signal.get("volume_ratio") or 0))
    else:
        precision = abs(float(signal["retest_precision_pct"]))
        structure = min(40.0, 25.0 + 5.0 * min(2.0, float(signal.get("pivot_touches") or 0))
                        + (5.0 if precision <= 0.5 else 0.0))
        contraction = float(signal.get("volume_contraction_ratio") or 1)
        price_volume = min(25.0, max(0.0, 25.0 * (1.0 - contraction / 2.0)))
    rr = signal.get("risk_reward")
    risk_points = min(15.0, max(0.0, float(rr) * 5.0)) if rr not in ("", None) else 0.0
    points = {"structure": round(structure, 2), "price_volume": round(price_volume, 2),
              "sector": min(20.0, max(0.0, sector_bonus)), "risk": round(risk_points, 2)}
    points["total"] = round(sum(points.values()), 2)
    return points


def build_candidates(market: pd.DataFrame) -> tuple[list[dict], list[dict], dict]:
    latest_date = market["date"].max()
    market_ret20 = core.latest_market_median_return20(market)
    ranked, observations = [], []
    stats = {"market_codes": 0, "liquid_codes": 0, "reversal": 0,
             "retest": 0, "excluded_low_liquidity": 0}
    for raw_code, group in market.groupby("code", sort=False):
        stats["market_codes"] += 1
        # Do not mistake stale final rows for today's signals.
        if group["date"].max() != latest_date:
            continue
        raw = group.sort_values("date")
        if len(raw) < 80:
            continue
        today = raw.iloc[-1]
        if not liquid_today(today):
            stats["excluded_low_liquidity"] += 1
            continue
        stats["liquid_codes"] += 1
        code = str(raw_code)
        options = []
        x = core.prepare(raw)
        rev_setup, reversal = core.detect_false_break_reversal(code, x)
        reversal = core.apply_new_plan_gate(reversal, rev_setup, x, market_ret20)
        # DIF(EMA6-EMA13) must be converging/rising for the recovery route.
        dif_ok = (len(x) >= 2 and pd.notna(x.iloc[-1].dif)
                  and pd.notna(x.iloc[-2].dif)
                  and float(x.iloc[-1].dif) >= float(x.iloc[-2].dif))
        if reversal is not None and dif_ok:
            reversal = dict(reversal)
            reversal["signal_route"] = "破底翻"
            options.append(("破底翻", reversal))
            stats["reversal"] += 1
        # Separate detector checks original 60-day pivot and support confluence.
        ret = retest.classify_latest(raw)
        if ret is not None:
            if ret["high_quality_confluence"]:
                risk = float(ret["close"]) - float(ret["stop_price"])
                highs = x.iloc[:-1]["high"]
                overhead = highs[highs > float(ret["close"]) * 1.005]
                target = float(overhead.min()) if len(overhead) else None
                rr = (target - float(ret["close"])) / risk if target is not None and risk > 0 else None
                candidate = dict(ret)
                candidate.update(signal_route="突破回踩不破", baseline_entry=ret["pivot"],
                                 risk_reward=round(rr, 2) if rr is not None else "",
                                 target_price=round(target, 2) if target is not None else "",
                                 pattern_key=f"突破回踩:{ret['breakout_date']}:{ret['pivot']}",
                                 volume_lots=ret["today_volume_lots"], turnover=float(today.turnover))
                options.append(("突破回踩不破", candidate))
                stats["retest"] += 1
            else:
                observations.append({"date": ret["date"], "code": code,
                                     "name": ret["name"], "reason": "未達高品質支撐共振",
                                     "stage": ret["stage"]})
        for kind, signal in options:
            signal["quality"] = score(signal, kind, today)
            signal["kind"] = kind
        if options:
            # One candidate per stock per day; route priority breaks equal-score ties.
            kind, selected = max(options, key=lambda item: (
                item[1]["quality"]["total"], item[0] == "破底翻"))
            selected["date"] = latest_date.strftime("%Y-%m-%d")
            selected["code"] = code
            selected["name"] = str(today["name"])
            ranked.append(selected)
    ranked.sort(key=lambda s: (-s["quality"]["total"], s["code"]))
    for i, item in enumerate(ranked):
        item["test_status"] = "正式試單候選" if i < MAX_FORMAL else "候補觀察"
        item["test_rank"] = i + 1
    stats["formal_count"] = min(MAX_FORMAL, len(ranked))
    stats["eligible_count"] = len(ranked)
    return ranked, observations, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=BASE / "c_group_output")
    args = parser.parse_args()
    market = core.read_market()
    ranked, observations, stats = build_candidates(market)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    # Output-only experiment; never append formal_recommendations.csv or broadcast.
    (out / "c_group_ranked.json").write_text(json.dumps(
        {"stats": stats, "ranked": ranked, "observations": observations},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    fields = ["date", "test_rank", "code", "name", "kind", "test_status",
              "close", "baseline_entry", "support_lower", "support_upper",
              "stop_price", "target_price", "risk_reward", "volume_lots",
              "turnover", "quality"]
    with (out / "c_group_ranked.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in ranked:
            writer.writerow({key: json.dumps(row[key], ensure_ascii=False)
                             if key == "quality" else row.get(key, "") for key in fields})
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
