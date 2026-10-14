from __future__ import annotations

import json
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
TRACKING = ROOT / "breakout_tracking.json"
POOL_OUT = ROOT / "ghost_candidate_pool.json"
SEATS_OUT = ROOT / "today_trading_seats.json"

HOLIDAYS_2026 = {
    "2026-01-01","2026-02-12","2026-02-13","2026-02-16","2026-02-17",
    "2026-02-18","2026-02-19","2026-02-20","2026-02-27","2026-04-03",
    "2026-04-06","2026-05-01","2026-06-19","2026-09-25","2026-09-28",
    "2026-10-09","2026-10-26","2026-12-25",
}
BAD_STATUS = {
    "retest_failed","failed","invalid","expired","cancelled","deleted"
}

def is_trade_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS_2026

def next_trade_day(d: date) -> date:
    x=d
    for _ in range(20):
        x += timedelta(days=1)
        if is_trade_day(x):
            return x
    return x

def target_trade_date(now: datetime) -> date:
    local=now.astimezone(TAIPEI)
    if local.hour >= 23:
        return next_trade_day(local.date())
    if is_trade_day(local.date()):
        return local.date()
    x=local.date()
    while not is_trade_day(x):
        x += timedelta(days=1)
    return x

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def build_rows(data: dict) -> list[dict]:
    rows=[]
    for item in data.values():
        if not isinstance(item, dict):
            continue
        status=str(item.get("status") or "").strip().lower()
        if status in BAD_STATUS:
            continue
        try:
            tracking_day=int(item.get("tracking_day"))
        except Exception:
            continue
        dday=tracking_day+1
        if not 1 <= dday <= 10:
            continue
        try:
            support=float(item.get("current_support") or item.get("confirmed_support") or item.get("candidate_new_support") or item.get("retest_support") or 0)
            close=float(item.get("last_close") or item.get("true_breakout_close") or item.get("breakout_close") or 0)
        except Exception:
            continue
        if support <= 0 or close <= 0:
            continue
        dist=(close/support-1.0)*100.0
        if dist < 0 or dist > 5:
            continue
        try:
            score=float(item.get("composite_score") or 0)
        except Exception:
            score=0.0
        rows.append({
            "code":str(item.get("code") or ""),
            "name":str(item.get("name") or item.get("code") or ""),
            "d":dday,
            "price":round(close,4),
            "support":round(support,4),
            "distance_pct":round(dist,4),
            "score":round(score,2),
            "route":str(item.get("route") or ""),
            "status":"優先" if dist <= 3 else "可接受",
            "source_signal_date":str(item.get("signal_date") or ""),
            "source_last_checked_date":str(item.get("last_checked_date") or ""),
        })
    rows.sort(key=lambda r: (0 if r["distance_pct"] <= 3 else 1, -r["score"], r["distance_pct"], r["code"]))
    seen=set()
    out=[]
    for r in rows:
        if not r["code"] or r["code"] in seen:
            continue
        seen.add(r["code"])
        out.append(r)
        if len(out)>=10:
            break
    return out

def main():
    now=datetime.now(TAIPEI)
    target=target_trade_date(now)
    tracking=load_json(TRACKING,{})
    rows=build_rows(tracking)
    final=(now.hour > 8) or (now.hour==8 and now.minute>=0)
    pool={
        "version":234,
        "target_date":target.isoformat(),
        "generated_at":now.isoformat(timespec="seconds"),
        "source":"GitHub breakout_tracking D1-D10",
        "finalized":bool(final),
        "rules":{
            "distance":"0-3% priority; 3-5% acceptable; >5% excluded",
            "max_candidates":10,
            "selection":"distance zone -> composite score -> distance"
        },
        "rows":rows,
    }
    POOL_OUT.write_text(json.dumps(pool,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    # 08:30 Taiwan and later: publish today's cloud ranking.
    if (now.hour > 8) or (now.hour==8 and now.minute>=30):
        seats={
            "version":234,
            "date":target.isoformat(),
            "generated_at":now.isoformat(timespec="seconds"),
            "codes":[r["code"] for r in rows[:3]],
            "standby_codes":[r["code"] for r in rows[3:5]],
            "name_map":{r["code"]:r["name"] for r in rows[:5]},
            "source":"ghost_candidate_pool.json",
            "rule":"08:30 cloud top3 + rank4/5 standby; local open positions still have priority when desktop starts"
        }
        SEATS_OUT.write_text(json.dumps(seats,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
