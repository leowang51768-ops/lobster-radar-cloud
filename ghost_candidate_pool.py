from __future__ import annotations

import json
import sqlite3
import math
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
TRACKING = ROOT / "breakout_tracking.json"
POOL_OUT = ROOT / "ghost_candidate_pool.json"
SEATS_OUT = ROOT / "today_trading_seats.json"
PRICE_DB = ROOT / "lobster_tw_6m_prices.sqlite"

# Fallback is used only when the official TWSE calendar cannot be reached.
# Normal operation reads each year's market open/close schedule directly from TWSE.
FALLBACK_HOLIDAYS_2026 = {
    "2026-01-01","2026-02-12","2026-02-13","2026-02-16","2026-02-17",
    "2026-02-18","2026-02-19","2026-02-20","2026-02-27","2026-04-03",
    "2026-04-06","2026-05-01","2026-06-19","2026-09-25","2026-09-28",
    "2026-10-09","2026-10-26","2026-12-25",
}
_CALENDAR_CACHE = {}
_CALENDAR_SOURCE = {}

BAD_STATUS = {
    "retest_failed","failed","invalid","expired","cancelled","deleted"
}

def _fetch_twse_calendar(year: int):
    if year in _CALENDAR_CACHE:
        return _CALENDAR_CACHE[year]

    roc_year = year - 1911
    url = (
        "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"
        f"?response=json&queryYear={roc_year}"
    )
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ghost-candidate-pool/1.0)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if str(payload.get("stat", "")).lower() != "ok":
            raise RuntimeError("TWSE holiday schedule stat is not ok")

        closed = set()
        open_override = set()
        for row in payload.get("data", []):
            if not row:
                continue
            ds = str(row[0]).strip()
            name = str(row[1] if len(row) > 1 else "")
            desc = str(row[2] if len(row) > 2 else "")
            text = name + " " + desc

            # TWSE explicitly marks start/last trading days and any make-up
            # trading days as open. Other listed weekday dates are closures.
            if any(k in text for k in ("開始交易", "最後交易", "補行交易")):
                open_override.add(ds)
            else:
                closed.add(ds)

        _CALENDAR_CACHE[year] = (closed, open_override)
        _CALENDAR_SOURCE[year] = "TWSE官方開休市日曆"
        return _CALENDAR_CACHE[year]
    except Exception:
        # Safe fallback for the currently deployed 2026 calendar only.
        # For unknown future years, fail closed instead of guessing.
        if year == 2026:
            _CALENDAR_CACHE[year] = (set(FALLBACK_HOLIDAYS_2026), set())
            _CALENDAR_SOURCE[year] = "2026本機備援日曆"
            return _CALENDAR_CACHE[year]
        raise RuntimeError(f"無法取得 {year} TWSE 官方開休市日曆；不猜測交易日")


def is_trade_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    closed, open_override = _fetch_twse_calendar(d.year)
    ds = d.isoformat()
    if ds in open_override:
        return True
    return ds not in closed


def next_trade_day(d: date) -> date:
    x=d
    for _ in range(40):
        x += timedelta(days=1)
        if is_trade_day(x):
            return x
    raise RuntimeError("40 天內找不到下一交易日")


def trading_day_index(signal_date: str, reference_date: str) -> int:
    """Match local D0-D10 logic: signal day is D0; count only TWSE trading days."""
    try:
        start = date.fromisoformat(str(signal_date)[:10])
        end = date.fromisoformat(str(reference_date)[:10])
    except Exception:
        return -1
    if end < start:
        return -1
    count = 0
    x = start
    while x < end:
        x += timedelta(days=1)
        if is_trade_day(x):
            count += 1
    return count

def target_trade_date(now: datetime) -> date:
    local=now.astimezone(TAIPEI)

    # Candidate-pool date rule:
    # - After Taiwan market close (13:30), start preparing the NEXT trading day.
    # - Before close, if today is a trading day, the pool belongs to today.
    # - On a non-trading day, point to the next trading day.
    if is_trade_day(local.date()) and (local.hour, local.minute) >= (13, 30):
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

def latest_price_snapshot() -> tuple[str, dict[str, str], dict[str, float]]:
    if not PRICE_DB.exists():
        return "", {}, {}
    with sqlite3.connect(PRICE_DB) as con:
        latest = con.execute(
            "SELECT value FROM metadata WHERE key='latest_trade_date'"
        ).fetchone()
        latest_trade_date = str(latest[0]) if latest and latest[0] else ""
        rows = con.execute(
            "SELECT stock_id, date, close FROM prices WHERE date=?",
            (latest_trade_date,),
        ).fetchall() if latest_trade_date else []
    price_dates = {}
    closes = {}
    for code, day, close in rows:
        if not code or not day or close is None:
            continue
        price_dates[str(code)] = str(day)
        try:
            closes[str(code)] = float(close)
        except Exception:
            pass
    return latest_trade_date, price_dates, closes


def cloud_daily_score(item, code, close, support, pattern_score, price_date):
    # Same component weights, with daily MACD clearly identified as a cloud proxy.
    status=str(item.get('status') or '')
    breakout=95 if status=='confirmed' else 80 if status=='true_breakout' else 90 if close>support*1.01 else 95
    parts={'breakout':breakout, 'pattern':max(0,min(100,pattern_score)), 'macd':None,'volume':None,'chips':40}
    if PRICE_DB.exists():
        with sqlite3.connect(PRICE_DB) as con:
            bars=list(con.execute('SELECT close,volume FROM prices WHERE stock_id=? AND date<=? ORDER BY date DESC LIMIT 65',(code,price_date)))[::-1]
        closes=[float(b[0]) for b in bars if b[0] is not None]
        if len(closes)>=35:
            def ema(values,span):
                result=[]; e=values[0]; a=2/(span+1)
                for v in values:
                    e=a*v+(1-a)*e; result.append(e)
                return result
            dif=[a-b for a,b in zip(ema(closes,12),ema(closes,26))]
            dea=ema(dif,9); h=[a-b for a,b in zip(dif,dea)]
            parts['macd']=100 if h[-2]<=0<h[-1] else 90 if h[-1]>0 and h[-1]>h[-2] else 70 if h[-1]>h[-2] else 0 if h[-1]<0 and h[-1]<h[-2] else 20
        if len(bars)>=6 and all(v is not None and float(v)>0 for _,v in bars[-6:]):
            vr=float(bars[-1][1])/(sum(float(v) for _,v in bars[-6:-1])/5)
            parts['volume']=45 if vr>=4 else 65 if vr>=2 else 90 if vr>=1.6 else 100 if vr>=1.2 else 70 if vr>=1 else 50 if vr>=.8 else 30
    feed=load_json(ROOT/'branch_tracker/data/monitor_branch_feed.json',{})
    stock=next((r for r in feed.get('stocks',[]) if str(r.get('code'))==code),{})
    if stock.get('data_through')==price_date:
        b=(stock.get('branches') or [{}])[0]
        parts['chips']=10 if int(b.get('consecutive_sell_days') or 0)>0 else 80 if b.get('formal_key_branch') else 90 if int(b.get('recent_buy_days_5') or 0)>=2 else 40
        # Selling is kept negative; a historical grade cannot mask current selling.
        if parts['chips']!=10:
            parts['chips']=max(parts['chips'],{'A':100,'B':80,'C':60}.get(str(b.get('historical_grade') or ''),0))
    weights={'breakout':.35,'pattern':.25,'macd':.20,'volume':.10,'chips':.10}
    valid=[k for k,v in parts.items() if v is not None and math.isfinite(v)]
    total=sum(parts[k]*weights[k] for k in valid)/sum(weights[k] for k in valid)
    parts.update(macd_source='daily_MACD_proxy',price_source_date=price_date,missing=[k for k in weights if parts[k] is None])
    return total,parts

def build_rows(data: dict, expected_completed_date=None) -> tuple[list[dict], str]:
    latest_trade_date, price_dates, official_closes = latest_price_snapshot()

    if expected_completed_date and latest_trade_date != expected_completed_date:
        return [], latest_trade_date

    # Same stock can have multiple historical tracking entries.
    # Match the local D1-D10 display rule: keep only the latest signal for each code.
    latest_by_code = {}
    for item in data.values():
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        if not code:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in BAD_STATUS:
            continue
        signal_date = str(item.get("signal_date") or "")[:10]
        prev = latest_by_code.get(code)
        prev_signal = str(prev.get("signal_date") or "")[:10] if prev else ""
        if prev is None or signal_date >= prev_signal:
            latest_by_code[code] = item

    rows=[]
    for code, item in latest_by_code.items():
        signal_date=str(item.get("signal_date") or "")[:10]
        dday=int(item.get("tracking_day")) if item.get("lifecycle_stage")=="D" else trading_day_index(signal_date, latest_trade_date)
        if not 0 <= dday <= 10:
            continue

        try:
            support=float(
                item.get("current_support")
                or item.get("confirmed_support")
                or item.get("candidate_new_support")
                or item.get("retest_support")
                or item.get("support")
                or 0
            )
            # Candidate 現價 always uses the latest official close.
            close=float(official_closes.get(code) or 0)
        except Exception:
            continue
        if support <= 0 or close <= 0:
            continue

        dist=(close/support-1.0)*100.0
        if dist < 0 or dist > 5:
            continue

        try:
            score=float(item.get("composite_score") or item.get("score") or 0)
        except Exception:
            score=0.0

        price_source_date=price_dates.get(code, "")
        stale_price=bool(latest_trade_date and price_source_date != latest_trade_date)
        if stale_price:
            continue

        score, score_parts = cloud_daily_score(item, code, close, support, score, latest_trade_date)
        rows.append({
            "code":code,
            "name":str(item.get("name") or item.get("code") or ""),
            "d":dday,
            "price":round(close,4),
            "support":round(support,4),
            "distance_pct":round(dist,4),
            "score":round(score,2),
            "score_source":"cloud_daily_snapshot",
            "score_parts":score_parts,
            "signal_id":item.get("signal_id") or item.get("tracking_key"),
            "route":str(item.get("route") or ""),
            "status":"優先" if dist <= 3 else "可接受",
            "source_signal_date":signal_date,
            "source_last_checked_date":str(item.get("last_checked_date") or ""),
            "price_source_date":price_source_date,
        })

    rows.sort(key=lambda r: (
        0 if r["distance_pct"] <= 3 else 1,
        -r["score"],
        r["distance_pct"],
        r["code"],
    ))
    return rows[:10], latest_trade_date

def build_daily_scores(tracking, rows, latest_trade_date, completed, target, now):
    latest,dates,closes=latest_price_snapshot()
    ranks={r['code']:i+1 for i,r in enumerate(rows)}
    stocks={}
    for item in tracking.values():
        code=str(item.get('code') or '')
        if not code: continue
        sid=item.get('signal_id') or item.get('tracking_key')
        support=float(item.get('current_support') or item.get('confirmed_support') or item.get('candidate_new_support') or item.get('retest_support') or item.get('support') or 0)
        close=float(closes.get(code) or 0)
        valid=latest==completed and dates.get(code)==completed and support>0 and close>0
        score,parts=(cloud_daily_score(item,code,close,support,float(item.get('composite_score') or item.get('score') or 0),completed) if valid else (None,{}))
        dist=(close/support-1)*100 if valid else None
        stocks[code]={'signal_id':sid,'score':None if score is None else round(score,2),
            'score_parts':parts,'score_source':'canonical_daily', 'score_date':completed,
            'd':item.get('tracking_day'), 'price':close if valid else None,'support':support or None,
            'distance_pct':None if dist is None else round(dist,4),
            'eligible':code in ranks,'candidate_rank':ranks.get(code),
            'exclusion_reason':'' if code in ranks else '日線資料未齊／超出支撐距離／未入前10名'}
    result={'version':244,'generated_at':now.isoformat(timespec='seconds'),
        'target_date':target.isoformat(),'score_date':completed,'stocks':stocks,
        'weights':{'breakout':35,'pattern':25,'macd':20,'volume':10,'chips':10},
        'macd_basis':'daily','selection':'D0-D10 -> 0-3% priority / 3-5% acceptable -> daily score -> distance -> code'}
    (ROOT/'ghost_daily_scores.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result

def main():
    now=datetime.now(TAIPEI)
    target=target_trade_date(now)
    from ghost_state_sync import build_state
    tracking, completed=build_state(now,is_trade_day)
    rows, latest_trade_date=build_rows(tracking,completed)
    build_daily_scores(tracking,rows,latest_trade_date,completed,target,now)
    # A pool is final only on its own target trading day from 08:00 onward.
    # After 13:30, a newly-created next-day pool must remain "updating".
    final=(target == now.date()) and ((now.hour > 8) or (now.hour==8 and now.minute>=0))
    pool={
        "version":244,
        "target_date":target.isoformat(),
        "generated_at":now.isoformat(timespec="seconds"),
        "source":"GitHub shared lifecycle D0-D10",
        "price_source_date":latest_trade_date,
        "calendar_source":_CALENDAR_SOURCE.get(target.year, "TWSE官方開休市日曆"),
        "finalized":bool(final),
        "rules":{
            "distance":"0-3% priority; 3-5% acceptable; >5% excluded",
            "max_candidates":10,
            "selection":"latest signal per stock -> D0-D10 by actual TWSE trading days -> distance zone -> composite score -> distance",
            "d_day_rule":"confirmed_date=D0；只計算官方交易日；同股重複只留最新 signal_date",
            "price_guard":"現價一律使用 latest_trade_date 的官方收盤價；舊價或缺價不得進入"
        },
        "rows":rows,
    }
    POOL_OUT.write_text(json.dumps(pool,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    # Publish formal seats only on the target trading day at/after 08:30.
    # An after-close next-day preview must NOT overwrite formal seats early.
    previous_seats=load_json(SEATS_OUT,{})
    if target == now.date() and ((now.hour > 8) or (now.hour==8 and now.minute>=30)) and previous_seats.get('date')!=target.isoformat():
        seats={
            "version":244,
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

