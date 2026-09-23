#!/usr/bin/env python3
"""Immutable snapshots of actual daily LINE stock picks + 5/10/20-session outcomes.

Tracking is separate from formal recommendation history. Historical manual replay
does not create a new live recommendation; all maturities use official DB closes.
"""
from __future__ import annotations
import csv
import json
import math
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
DB = BASE / "lobster_tw_6m_prices.sqlite"
SNAPSHOTS = BASE / "line_pick_snapshots.csv"
PERFORMANCE = BASE / "line_pick_performance.csv"
FIELDS = ["signal_date","first_notified_at","code","name","route","stage",
          "baseline_entry","pivot","stop_price","risk_pct","volume_ratio",
          "breakout_date","status","entry_basis","notification_version"]
PERF_FIELDS = FIELDS + ["horizon","exit_date","exit_close","return_pct",
                        "win","stop_touched_through_horizon","outcome_status"]
HORIZONS = (5,10,20)


def read_rows(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig",newline="") as file:
        return list(csv.DictReader(file))


def write_rows(path,fields,items):
    temp=path.with_suffix(".tmp")
    with temp.open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)
    temp.replace(path)


def store_notification(signal_date, selected):
    """Call only AFTER successful LINE HTTP response and for live trade dates."""
    today=datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    if signal_date != today:
        print(f"Historical replay {signal_date}; no new LIVE snapshots")
        return 0
    old=read_rows(SNAPSHOTS)
    seen={(r["signal_date"],r["code"]) for r in old}
    added=0
    timestamp=datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    for pick in selected:
        key=(signal_date,pick["code"])
        if key in seen:
            continue  # Same-day reruns cannot inflate sample count.
        row=dict(signal_date=signal_date,first_notified_at=timestamp,
                 code=pick["code"],name=pick["name"],route=pick["route"],
                 stage=pick["stage"],baseline_entry=pick["close"],
                 pivot=pick["pivot"],stop_price=pick["stop"],
                 risk_pct=pick["risk_pct"],volume_ratio=pick["volume_ratio"],
                 breakout_date=pick["day"],status=pick["status"],
                 entry_basis="signal_day_close_paper",
                 notification_version="combined-top10-v1")
        old.append(row)
        seen.add(key)
        added+=1
    if added:
        write_rows(SNAPSHOTS,FIELDS,old)
    print(f"LINE_PICK_SNAPSHOTS new={added} total={len(old)}")
    return added


def update_performance():
    """Recompute outcomes from fixed snapshots without mutating entry fields.

    Future day 1 is the first distinct exchange trading date after signal_date.
    Return is a mark-to-market close-to-close observation, NOT stop-executed
    realized return. Stop-touch is flagged separately without filling assumptions.
    """
    snapshots=read_rows(SNAPSHOTS)
    if not snapshots:
        if not PERFORMANCE.exists():
            write_rows(PERFORMANCE,PERF_FIELDS,[])
        print("LINE_PICK_PERFORMANCE tracked=0")
        return
    with sqlite3.connect(DB) as con:
        dates=[r[0] for r in con.execute("SELECT DISTINCT date FROM prices ORDER BY date")]
        index={day:i for i,day in enumerate(dates)}
        needed={r["code"] for r in snapshots}
        prices={}
        for code in needed:
            prices[code]={day:(float(close),float(low)) for day,close,low
                          in con.execute("SELECT date,close,low FROM prices WHERE stock_id=?",
                                         (code,))}
    results=[]
    for snap in snapshots:
        base=float(snap["baseline_entry"])
        stop=float(snap["stop_price"])
        start=index.get(snap["signal_date"])
        for horizon in HORIZONS:
            outcome=dict(snap,horizon=horizon,exit_date="",exit_close="",
                         return_pct="",win="",stop_touched_through_horizon="",
                         outcome_status="待到期")
            if start is None or base<=0:
                outcome["outcome_status"]="基準資料缺失"
            elif start+horizon>=len(dates):
                outcome["outcome_status"]="未到期"
            else:
                window=dates[start+1:start+horizon+1]
                observations=[prices[snap["code"]].get(day) for day in window]
                if any(item is None for item in observations):
                    outcome["outcome_status"]="官方股價不完整"
                else:
                    close=observations[-1][0]
                    ret=(close/base-1)*100
                    outcome.update(exit_date=window[-1],exit_close=round(close,2),
                                   return_pct=round(ret,4),
                                   win="1" if ret>0 else "0",
                                   stop_touched_through_horizon=(
                                       "1" if any(low<=stop for _,low in observations) else "0"),
                                   outcome_status="已到期")
            results.append(outcome)
    write_rows(PERFORMANCE,PERF_FIELDS,results)
    summary={}
    for h in HORIZONS:
        matured=[r for r in results if r["horizon"]==h and r["outcome_status"]=="已到期"]
        summary[h]={"matured":len(matured),
                    "win_rate_pct":round(100*sum(r["win"]=="1" for r in matured)/len(matured),2) if matured else None,
                    "avg_return_pct":round(sum(r["return_pct"] for r in matured)/len(matured),2) if matured else None}
    print("LINE_PICK_PERFORMANCE "+json.dumps(summary,ensure_ascii=False))


if __name__=="__main__":
    update_performance()
