#!/usr/bin/env python3
"""Fast event-based backtest for structural fakeout only."""
import json, math
from pathlib import Path
import pandas as pd
import buy_signal as e

OUT=Path(__file__).with_name("backtest_fakeout_fast_results.json")
COST=0.00585
H=(5,10,20)

def benchmarks(m):
    z=m.sort_values(["code","date"]).copy()
    z["r"]=z.groupby("code")["close"].pct_change(20)
    return z.groupby("date")["r"].median().to_dict()

def signals(m):
    bm=benchmarks(m); out=[]
    for raw,g in m.groupby("code",sort=False):
        code=str(raw); x=e.prepare(g); seen=set()
        for b in range(max(e.FAKEOUT_PRE_WINDOW,65),len(x)-4):
            p=x.iloc[b-e.FAKEOUT_PRE_WINDOW:b]; br=x.iloc[b]
            sup=float(p.low.min()); res=float(p.high.max()); peak=float(p.tail(10).high.max())
            touches=int((p.low<=sup*(1+e.FAKEOUT_SUPPORT_BAND)).sum())
            low=float(br.low)
            if not (touches>=e.FAKEOUT_MIN_SUPPORT_TOUCHES and low<sup*(1-e.FAKEOUT_SUPPORT_BREAK_MIN) and low<=peak*(1-e.FAKEOUT_ACUTE_DROP_MIN)):
                continue
            reclaim=None
            for r in range(b+e.FAKEOUT_RECOVERY_MIN_DAYS,min(len(x)-1,b+e.FAKEOUT_RECOVERY_MAX_DAYS)+1):
                if float(x.iloc[r].close)>=sup: reclaim=r; break
            if reclaim is None: continue
            end=min(len(x)-2,b+e.FAKEOUT_EVENT_LOOKBACK)
            for i in range(reclaim+1,end+1):
                t=x.iloc[i]; prev=x.iloc[i-1]
                if float(x.iloc[b:i+1].low.min())<low*(1-e.FAKEOUT_NEW_LOW_TOL): break
                fresh=float(t.close)>res*(1+e.FAKEOUT_BREAKOUT_MIN) and float(prev.close)<=res*(1+e.FAKEOUT_BREAKOUT_MIN)
                vok,lots,turn,ratio=e.volume_gate(t); ext=float(t.close)/res-1
                if not (fresh and float(t.close)>float(t.open) and e.close_location(t)>=e.CLOSE_LOCATION_MIN and vok and ext<=e.MAX_STRUCTURE_EXTENSION):
                    continue
                key=f"{code}:{x.iloc[b].date:%Y-%m-%d}:{res:.2f}"
                if key in seen: break
                setup={"support_lower":round(sup,2)}
                sig={"date":f"{t.date:%Y-%m-%d}","code":code,"signal_route":"假摔收復確認","pattern_key":key,"support_lower":round(sup,2)}
                sig=e.apply_new_plan_gate(sig,setup,x.iloc[:i+1],float(bm.get(t.date,0)))
                if sig:
                    seen.add(key); out.append({"i":i,"x":x,"support":sup})
                break
    return out

def summary(ss,h):
    rows=[]
    for s in ss:
        i=s["i"]; x=s["x"]
        if i+h>=len(x): continue
        entry=float(x.iloc[i+1].open); stop=s["support"]*(1-e.SUPPORT_BREAK_TOL); ex=i+h; stopped=False
        for j in range(i+1,i+h+1):
            if float(x.iloc[j].close)<stop: ex=j; stopped=True; break
        net=float(x.iloc[ex].close)/entry-1-COST
        rows.append((net,stopped,float(x.iloc[i+1:ex+1].low.min())/entry-1))
    if not rows:return {"samples":0}
    return {"samples":len(rows),"win_rate":round(sum(r[0]>0 for r in rows)/len(rows)*100,2),"avg_net":round(sum(r[0] for r in rows)/len(rows)*100,2),"stop_rate":round(sum(r[1] for r in rows)/len(rows)*100,2),"worst_mae":round(min(r[2] for r in rows)*100,2)}

m=e.read_market(); ss=signals(m)
result={"data_start":f"{m.date.min():%Y-%m-%d}","data_end":f"{m.date.max():%Y-%m-%d}","signals":len(ss),"results":{str(h):summary(ss,h) for h in H}}
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
