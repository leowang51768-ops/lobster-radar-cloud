"""Cloud continuation of Ghost lifecycle; no trading or message sending."""
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from ghost_lifecycle import reconcile, active_picks

ROOT = Path(__file__).resolve().parent

def load(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)

def completed_session(now, is_trade_day):
    d=now.date()
    if (now.hour,now.minute)<(13,31):
        d-=timedelta(days=1)
    while not is_trade_day(d):
        d-=timedelta(days=1)
    return d

def build_state(now, is_trade_day, root=ROOT):
    reference=now.date()
    while not is_trade_day(reference):
        reference-=timedelta(days=1)
    completed=completed_session(now,is_trade_day)
    tracking=load(root/'breakout_tracking.json',{})
    feed=load(root/'branch_tracker/data/monitor_branch_feed.json',{})
    events=load(root/'ghost_monitor_events.json',{}).get('events',{})
    previous=load(root/'ghost_monitor_state.json',{})
    state=reconcile(previous,tracking,feed,events,reference.isoformat(),completed.isoformat(),is_trade_day)

    # Check official completed-session prices. Missing prices never confirm failure.
    prices={}
    db=root/'lobster_tw_6m_prices.sqlite'
    if db.exists():
        with sqlite3.connect(db) as con:
            prices={str(c):float(p) for c,p in con.execute(
                'SELECT stock_id,close FROM prices WHERE date=?',(completed.isoformat(),)) if p is not None}
    failures={}
    for code,row in state.get('stocks',{}).items():
        if row.get('stage')!='D' or code not in prices:
            continue
        p=row['pick']
        support=float(p.get('current_support') or p.get('retest_support') or p.get('support') or 0)
        stop=float(p.get('stop_price') or p.get('stop') or 0)
        close=prices[code]
        p.update(last_close=close,last_checked_date=completed.isoformat())
        if (stop>0 and close<stop) or (support>0 and close<support):
            p.update(status='invalid' if stop>0 and close<stop else 'failed',
                     resolved_date=completed.isoformat())
            failures[row['signal_id']]={**p,'signal_id':row['signal_id'],
                                       'signal_date':row['confirmed_date']}
    if failures:
        state=reconcile(state,failures,{}, {},reference.isoformat(),completed.isoformat(),is_trade_day)

    registry=load(root/'branch_tracker/data/monitor_return_pool.json',{'stocks':{}})
    before_registry=json.dumps(registry.get('stocks',{}),sort_keys=True)
    for code,row in state.get('stocks',{}).items():
        if row.get('stage')!='POOL':
            continue
        old=registry.setdefault('stocks',{}).get(code,{})
        registry['stocks'][code]={**old,'code':code,'name':row.get('name') or code,
            'first_returned_date':old.get('first_returned_date') or row['retired_date'],
            'last_returned_date':row['retired_date'],'last_signal_id':row['signal_id'],
            'reason':row['exit_reason'],'permanent':True,'continue_branch_tracking':True}
    registry['version']=241
    if before_registry != json.dumps(registry.get('stocks',{}),sort_keys=True) or not registry.get('updated_at'):
        registry['updated_at']=now.isoformat(timespec='seconds')
    state.update(version=241,updated_at=now.isoformat(timespec='seconds'),
                 completed_through=completed.isoformat(),price_basis='official_completed_session')
    save(root/'ghost_monitor_state.json',state)
    # Full permanent evidence stays in the cloud ledger; a compact projection
    # avoids transferring archived multi-branch snapshots on every desktop poll.
    projection={**state,'stocks':{},'archive':{}}
    for code,row in state.get('stocks',{}).items():
        projection['stocks'][code]={k:v for k,v in row.items() if k!='branch'}
    for sid,row in state.get('archive',{}).items():
        projection['archive'][sid]={k:row.get(k) for k in ('code','stage','signal_id','confirmed_date','first_seen_date','retired_date','exit_reason')}
    save(root/'ghost_monitor_projection.json',projection)
    save(root/'branch_tracker/data/monitor_return_pool.json',registry)
    save(root/'ghost_monitor_contract.json',{'version':241,'receiver_ready':True,
        'events_path':'ghost_monitor_events.json','state_path':'ghost_monitor_projection.json',
        'updated_at':now.isoformat(timespec='seconds'),
        'scope':'pattern lifecycle and evidence only; no orders'})
    return {p['signal_id']:p for p in active_picks(state)},completed.isoformat()

