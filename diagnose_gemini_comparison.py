#!/usr/bin/env python3
"""Read-only per-stock diagnostics for the 2026-09-22 Gemini/Lobster comparison.

Runs the *existing* buy_signal.py and vcp_scan.py checks. Does not modify
recommendations, signal state, stock database, or LINE settings.
"""
from __future__ import annotations

import argparse
import csv
import inspect
import json
import math
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import buy_signal as buy
import vcp_scan as vcp

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
OUT = BASE / "diagnostics"
GEMINI_VCP = ("2330 6488 6182 3443 6462 8299 2379 3711 2449 6239 2441 "
              "8150 6196 3324 8996 6805 4566 1513 6203 3044 6191 3533 "
              "3605 2317 2376 2395").split()
TARGETS = list(dict.fromkeys(["6191", "3526", *GEMINI_VCP]))
VCP_CODES = set(GEMINI_VCP)


def trace_none(fn, *args):
    """Run an unmodified original function and capture its last None return site.

    Captures a precise code location rather than attributing an unverified
    market interpretation. This tracer is enabled for one stock at a time.
    """
    code = fn.__code__
    hits = []
    captured = {}
    def tracer(frame, event, arg):
        if frame.f_code is code:
            if event == "return" and arg is None:
                hits.append(frame.f_lineno)
                captured.update(frame.f_locals)
            return tracer
        return tracer if event == "call" else None
    old = sys.gettrace()
    try:
        sys.settrace(tracer)
        result = fn(*args)
    finally:
        sys.settrace(old)
    line = hits[-1] if hits else None
    return result, line, captured


def site(fn, number):
    if number is None:
        return ""
    source, first = inspect.getsourcelines(fn)
    index = number - first
    if not 0 <= index < len(source):
        return str(number)
    # Record nearest guard and actual return site for reproducible diagnosis.
    preceding = [s.strip() for s in source[max(0, index-8):index]
                 if s.strip() and not s.strip().startswith("#")]
    return f"{fn.__name__}:{number}: " + " / ".join(preceding[-4:])


def diagnose_buy(code, market):
    if market.empty:
        return {"status": "資料缺失", "reason": "當日或歷史價格不足"}
    market = market.copy()
    market["volume_lots"] = market["volume"] / 1000.0
    x = buy.prepare(market)
    if len(x) < buy.FALSE_BREAK_SUPPORT_LOOKBACK + buy.FALSE_BREAK_RECOVERY_DAYS:
        return {"status": "未過歷史長度", "reason": f"歷史 {len(x)} 日，不足 63 日"}
    (setup, signal), line, _ = trace_none(buy.detect_false_break_reversal, code, x)
    if not setup:
        return {"status": "無完整ABC候選", "reason": "60日支撐、跌破與3日內收復的組合未成立",
                "source": site(buy.detect_false_break_reversal, line)}
    gated = buy.apply_new_plan_gate(signal, setup, x)
    status = "正式條件通過" if gated else "觀察中"
    reason = ("全部正式條件通過，仍須查新訊號去重與當日工作流程"
              if gated else "初步ABC已建構；正式訊號未通過")
    if signal is None:
        if setup.get("entry_stage", "").startswith("C點"):
            reason = "早期C點回收的K棒強度、量能或延伸條件未同時通過"
        else:
            reason = "較高回檔低點、當日首次突破頸線、收紅、量能及延伸條件未同時通過"
    elif not gated:
        reason = "正式訊號未通過上方空間8%或風報比1.5的風險門檻"
    return {"status": status, "reason": reason, "source": site(buy.detect_false_break_reversal, line),
            "a_lower": setup.get("a_point_lower", ""), "a_upper": setup.get("a_point_upper", ""),
            "b_low": setup.get("b_point", ""), "stage": setup.get("entry_stage", ""),
            "trigger": setup.get("trigger_level", ""), "neckline": setup.get("neckline_status", ""),
            "higher_low": setup.get("higher_low_status", ""),
            "upside_room_pct": setup.get("upside_room_pct", ""),
            "risk_reward": setup.get("risk_reward", ""),
            "close_location": setup.get("close_location", ""),
            "structure_extension_pct": setup.get("structure_extension_pct", "")}


def diagnose_vcp(market):
    if market.empty:
        return {"status": "資料缺失", "reason": "當日或歷史價格不足"}
    x = vcp.prepare(market)
    i = len(x) - 1
    if len(x) < vcp.MIN_HISTORY:
        return {"status": "歷史不足", "reason": f"{len(x)} 日，最低需 {vcp.MIN_HISTORY} 日"}
    row = x.iloc[i]
    trend = vcp.trend_and_liquidity_ok(x, i)
    avg_lots = float(row.avg20_lots) if pd.notna(row.avg20_lots) else 0.
    avg_turnover = float(row.avg20_turnover) if pd.notna(row.avg20_turnover) else 0.
    detail = {"trend_ok": trend, "avg20_lots": round(avg_lots, 1),
              "avg20_turnover": round(avg_turnover), "ma20": round(float(row.ma20), 2),
              "ma50": round(float(row.ma50), 2), "ma60": round(float(row.ma60), 2)}
    ma20, ma50, ma60 = (float(row.ma20), float(row.ma50), float(row.ma60))
    old_ma60 = float(x.iloc[i - 20].ma60)
    detail.update({
        "liquidity_lots_pass": avg_lots >= vcp.MIN_VOLUME_LOTS,
        "liquidity_turnover_pass": avg_turnover >= vcp.MIN_AVG_TURNOVER,
        "close_above_ma60": float(row.close) > ma60,
        "ma20_above_ma50": ma20 > ma50,
        "ma50_above_ma60": ma50 > ma60,
        "ma60_rising_20d": ma60 > old_ma60,
        "ma60_20d_ago": round(old_ma60, 2),
    })
    if i >= 204 and pd.notna(row.ma200) and pd.notna(row.ma150):
        old_ma200 = float(x.iloc[i - 20].ma200)
        detail.update(trend_mode="200MA完整條件", ma150=round(float(row.ma150), 2),
                      ma200=round(float(row.ma200), 2),
                      close_above_ma200=float(row.close)>float(row.ma200),
                      ma200_rising_20d=float(row.ma200)>old_ma200,
                      ma150_above_ma200=float(row.ma150)>float(row.ma200),
                      ma50_above_ma150=ma50>float(row.ma150))
    else:
        recent = x.iloc[max(0, i - 40):i + 1]
        first, last = recent.iloc[:20], recent.iloc[-20:]
        detail.update(trend_mode="不足200日替代條件",
                      recent_higher_high=float(last.high.max()) >= float(first.high.max()),
                      recent_higher_low=float(last.low.min()) >= float(first.low.min()) * .98)
    if not trend:
        liquidity = avg_lots >= vcp.MIN_VOLUME_LOTS and avg_turnover >= vcp.MIN_AVG_TURNOVER
        return {**detail, "status": "前置篩選排除",
                "reason": "20日均量/成交額不足" if not liquidity else "上升趨勢與均線結構未通過"}
    pivot = vcp.pivot_before(x, i)
    detail["pivot"] = round(pivot, 2) if pivot else ""
    if pivot is None:
        return {**detail, "status": "樞紐未成立", "reason": "前60日缺少至少兩次測試的局部壓力樞紐"}
    profile, line, detail_locals = trace_none(vcp.contraction_profile, x, i - 1, pivot)
    if profile is None:
        # These values are from the original scanner at its exact return site.
        # An early return may not yet have computed later metrics.
        for field, key in (("base_sessions", "base_sessions"),
                           ("last_trough_age", "last_trough_age"),
                           ("peak_distances", "peak_distances"),
                           ("dry_ratio", "dry_ratio"),
                           ("last_leg_volume_ratio", "last_leg_volume_ratio"),
                           ("gaps", "gaps"), ("depths", "depths")):
            value = detail_locals.get(key)
            if isinstance(value, (int, float)):
                detail[field] = round(float(value), 4)
            elif isinstance(value, list):
                detail[field] = "/".join(str(round(float(z), 4)) for z in value)
        return {**detail, "status": "收縮結構未通過",
                "reason": "原始VCP函式在所列原始碼位置拒絕；查看 source 及已計算量化值",
                "source": site(vcp.contraction_profile, line)}
    detail.update({"contractions": profile["count"], "depths_pct": "/".join(
        f"{d * 100:.2f}" for d in profile["depths"]),
        "dry_ratio": round(profile["dry_ratio"], 3),
        "last_leg_volume_ratio": round(profile["last_leg_volume_ratio"], 3)})
    result = vcp.classify_latest(market)
    if result:
        return {**detail, "status": "VCP入選", "reason": result["vcp_stage"],
                "stage": result["vcp_stage"], "stop": result["stop_price"]}
    close = float(row.close)
    distance = close / pivot - 1
    detail["distance_to_pivot_pct"] = round(distance * 100, 2)
    if -vcp.NEAR_PIVOT_PCT <= distance <= 0 and close >= float(row.open) * .98:
        generated, line, _ = trace_none(vcp.make_row, x, i, profile, "接近突破", pivot, 0.)
        if generated is None:
            return {**detail, "status": "風險條件排除",
                    "reason": "停損距離或上方樞紐風報比未達門檻",
                    "source": site(vcp.make_row, line)}
    return {**detail, "status": "階段未確認",
            "reason": "未通過今日突破、突破後2～5日回踩或接近樞紐的階段條件"}


def verify_snapshot(trade_date):
    state = BASE / "signal_state.json"
    candidates = BASE / "candidate_status.csv"
    notes = {}
    if state.exists():
        data = json.loads(state.read_text(encoding="utf-8"))
        notes.update(state_date=data.get("latest_trade_date"),
                     state_candidates=data.get("candidate_count"),
                     formal_signals=data.get("formal_buy_signal_count"),
                     state_updated_at=data.get("updated_at"))
    if candidates.exists():
        with candidates.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        dated = [r for r in rows if r.get("date") == trade_date]
        notes.update(csv_total=len(rows), csv_matching_date=len(dated),
                     csv_formal_labels=sum(r.get("status") == "正式買點" for r in dated),
                     csv_dates=sorted(set(r.get("date") for r in rows)))
    notes["same_date"] = (notes.get("state_date") == trade_date
                          and notes.get("csv_dates") == [trade_date])
    notes["consistent_counts"] = (notes["same_date"]
                                 and notes.get("state_candidates") == notes.get("csv_matching_date")
                                 and notes.get("formal_signals") == notes.get("csv_formal_labels"))
    notes["interpretation"] = ("數量一致" if notes["consistent_counts"] else
        "狀態數量或日期不一致；需核對工作流程執行SHA及報表是否被其他執行覆寫")
    return notes


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-09-22", help="YYYY-MM-DD; use database snapshot from this date")
    args = p.parse_args()
    if not DB.exists():
        raise SystemExit(f"Missing local database: {DB}")
    with sqlite3.connect(DB) as conn:
        dates = {r[0] for r in conn.execute("SELECT DISTINCT date FROM prices")}
        if args.date not in dates:
            raise SystemExit(f"Date {args.date} not in database. No report generated.")
        marks = ",".join("?" for _ in TARGETS)
        raw = pd.read_sql_query(
            f"SELECT date, market, stock_id AS code, stock_name AS name, open, high, low, close, volume, turnover "
            f"FROM prices WHERE stock_id IN ({marks}) AND date <= ? ORDER BY stock_id, date",
            conn, params=[*TARGETS, args.date])
    raw["date"] = pd.to_datetime(raw["date"])
    for col in ("open", "high", "low", "close", "volume", "turnover"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw["code"] = raw["code"].astype(str).str.zfill(4)
    raw = raw.dropna(subset=["open", "high", "low", "close"])
    groups = {code: group for code, group in raw.groupby("code")}
    OUT.mkdir(exist_ok=True)
    result = []
    for code in TARGETS:
        group = groups.get(code, pd.DataFrame())
        if not group.empty and group.date.max().strftime("%Y-%m-%d") != args.date:
            group = pd.DataFrame()
        name = str(group.iloc[-1]["name"]) if not group.empty else ""
        base = {"date": args.date, "code": code, "name": name, "close":
                float(group.iloc[-1].close) if not group.empty else "",
                "data_days": len(group)}
        for strategy, fn in (("破底翻", lambda: diagnose_buy(code, group)),
                             ("VCP", lambda: diagnose_vcp(group))):
            if strategy == "VCP" and code not in VCP_CODES:
                continue
            try:
                result.append({**base, "strategy": strategy, **fn()})
            except Exception as ex:
                result.append({**base, "strategy": strategy,
                               "status": "診斷錯誤", "reason": f"{type(ex).__name__}: {ex}"})
    filename = OUT / f"gemini_vs_lobster_{args.date}.csv"
    keys = list(dict.fromkeys(k for r in result for k in r))
    with filename.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(result)
    snapshot = verify_snapshot(args.date)
    (OUT / f"snapshot_check_{args.date}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DIAGNOSTICS {filename}: {len(result)} strategy checks")
    print(json.dumps({"status_counts": {str(s): sum(r["status"] == s for r in result)
                                      for s in sorted(set(r["status"] for r in result))},
                      "snapshot": snapshot}, ensure_ascii=False))
    errors = [r for r in result if r["status"] == "診斷錯誤"]
    for r in errors:
        print(f"DIAGNOSTIC_ERROR {r['strategy']} {r['code']} {r['reason']}")
    if errors:
        raise SystemExit(f"{len(errors)} diagnostics failed; see diagnostic CSV")


if __name__ == "__main__":
    main()
