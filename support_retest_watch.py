#!/usr/bin/env python3
"""N字底觀察雷達：取代舊突破回踩觀察；不產生正式推薦。"""
from __future__ import annotations
import csv, json, os, sqlite3, urllib.request
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
import pandas as pd

from stock_universe import filter_market_frame

BASE=Path(__file__).resolve().parent
DB=BASE/"lobster_tw_6m_prices.sqlite"
OUTPUT=BASE/"n_bottom_watch.csv"
FIELDS=["date","code","name","market","stage","close","a_low","b_neckline","c_low","invalidation","volume_ratio","avg20_lots","avg20_turnover","entry_lower","entry_upper","stop_price","entry_status"]

def tick(price):
    return .01 if price<10 else .05 if price<50 else .1 if price<100 else .5 if price<500 else 1 if price<1000 else 5

def round_tick(price, direction):
    unit=Decimal(str(tick(price)))
    return float((Decimal(str(price))/unit).to_integral_value(rounding=direction)*unit)


def classify_latest(g):
    x=g.sort_values("date").reset_index(drop=True)
    if len(x)<65: return None
    t=x.iloc[-1]; i=len(x)-1
    if not (x.iloc[-20:].volume.mean()/1000>=300 and x.iloc[-20:].turnover.mean()>=30000000): return None
    close=float(t.close)
    # A、B、C 依時間順序尋找：A為局部低點，B為反彈高點，C為較高的第二底。
    # 僅採用C點已過至少兩根K棒的候選，避免以當日低點誤判已止跌。
    candidates=[]
    for a in range(max(5,i-48),i-9):
        if float(x.iloc[a].low)>float(x.iloc[a-2:a+3].low.min()): continue
        peak=float(x.iloc[max(0,a-60):a].high.max())
        A=float(x.iloc[a].low)
        if peak<=0 or A>peak*.85: continue
        for b in range(a+3,min(a+21,i-5)):
            B=float(x.iloc[b].high)
            if B<A*1.05 or B<float(x.iloc[a+1:b+1].high.max()): continue
            for c in range(b+3,min(b+21,i-1)):
                C=float(x.iloc[c].low)
                if C<=A or C>=B*.98: continue
                if C>float(x.iloc[b+1:c+1].low.min()): continue
                if float(x.iloc[c+1:i+1].low.min())<C*.99: continue
                if close<C*1.01 or close>B*1.10: continue
                # C之後已上行；突破只在當日首次跨越B頸線時提示。
                if close>B*1.005:
                    if float(x.iloc[i-1].close)>B: continue
                    stage="突破B點｜當日收盤確認"
                elif close>=B*.97:
                    stage="接近B點"
                elif close>float(x.iloc[i-1].close) and close>float(x.iloc[c].close):
                    stage="C點形成"
                else: continue
                v5=float(x.iloc[i-5:i].volume.mean())
                ratio=float(t.volume)/v5 if v5>0 else 0
                if stage.startswith("突破B點") and ratio<1.2: continue
                entry_lower=round_tick(B*1.005,ROUND_CEILING)
                entry_upper=round_tick(B*1.03,ROUND_FLOOR)
                stop=round_tick(C*.99,ROUND_FLOOR)
                eligible=(stage.startswith("突破B點") and entry_lower<=close<=entry_upper and stop<entry_lower and close>float(t.open))
                candidates.append(({"entry_lower":entry_lower,"entry_upper":entry_upper,"stop_price":stop,"entry_status":"正式試單" if eligible else "僅觀察","date":str(t.date)[:10],"code":str(t.code),"name":str(t["name"]),"market":str(t.market),"stage":stage,"close":round(close,2),"a_low":round(A,2),"b_neckline":round(B,2),"c_low":round(C,2),"invalidation":round(C*.99,2),"volume_ratio":round(ratio,2),"avg20_lots":round(float(x.iloc[-20:].volume.mean()/1000),0),"avg20_turnover":round(float(x.iloc[-20:].turnover.mean()),0)},c))
    if not candidates:return None
    return max(candidates,key=lambda item: ({"突破B點｜當日收盤確認":3,"接近B點":2,"C點形成":1}[item[0]["stage"]],item[1]))[0]

def main():
    if not DB.exists(): raise SystemExit(f"Database missing: {DB}")
    with sqlite3.connect(DB) as con:
        market=pd.read_sql_query("SELECT date,market,stock_id AS code,stock_name AS name,open,high,low,close,volume,turnover FROM prices ORDER BY stock_id,date",con)
    market=filter_market_frame(market)
    for col in ("open","high","low","close","volume","turnover"):
        market[col]=pd.to_numeric(market[col],errors="coerce")
    market=market.dropna(subset=["open","high","low","close","volume","turnover"])
    dates=market.groupby("code").date.max()
    latest=str(market.date.max())[:10]
    rows=[]
    for code,g in market.groupby("code",sort=False):
        if str(dates[code])[:10]!=latest: continue
        row=classify_latest(g)
        if row: rows.append(row)
    rows.sort(key=lambda r:({"突破B點｜當日收盤確認":0,"接近B點":1,"C點形成":2}[r["stage"]],-r["volume_ratio"],r["code"]))
    with OUTPUT.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    from buy_signal import RECOMMENDATIONS, recommendation_fields
    fields=recommendation_fields()
    existing=[]
    if RECOMMENDATIONS.exists():
        with RECOMMENDATIONS.open("r",encoding="utf-8-sig",newline="") as handle:
            existing=list(csv.DictReader(handle))
    seen={(r.get("code"),r.get("pattern_key")) for r in existing}
    added=0
    for r in rows:
        if r["entry_status"]!="正式試單": continue
        key=f"N字底:{r['a_low']:.2f}:{r['b_neckline']:.2f}:{r['c_low']:.2f}"
        if (r["code"],key) in seen: continue
        record={k:"" for k in fields}
        record.update({"date":r["date"],"code":r["code"],"name":r["name"],"trend":"N字底突破B點",
                       "strategy_source":"N字底","signal_route":"N字底突破B點","signal_light":"🟢綠燈",
                       "baseline_entry":r["close"],"key_high":r["b_neckline"],"key_low":r["c_low"],
                       "a_point_lower":r["a_low"],"b_point":r["b_neckline"],"entry_stage":"突破B點早期試單",
                       "volume_ratio":r["volume_ratio"],"support_lower":r["c_low"],
                       "support_upper":r["b_neckline"],"stop_price":r["stop_price"],
                       "pattern_key":key,"strategy_version":"N字底-B突破-v1"})
        existing.append(record)
        seen.add((r["code"],key))
        added+=1
    if added:
        with RECOMMENDATIONS.open("w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore")
            writer.writeheader()
            writer.writerows(existing)
    print(json.dumps({"date":latest,"n_bottom_observations":len(rows),"formal_recommendations_added":added},ensure_ascii=False))
    token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","").strip()
    if not token:
        print("LINE token missing; CSV only");return 0
    if not rows and os.getenv("N_BOTTOM_FORCE_NOTIFY")!="1":
        print("No N-bottom observations; LINE skipped");return 0
    lines=[f"🦞 龍蝦雷達｜N字底突破B點｜{latest}","符合突破B點、量比與試單區間者為正式試單；其餘僅觀察。"]
    if not rows:lines.append("目前無符合條件個股")
    for n,r in enumerate(rows[:10],1):
        breakout_label = (f"🚀 突破日：{r['date']}｜當日收盤確認突破B點"\n                          if r["stage"].startswith("突破B點")\n                          else f"尚未突破B點｜觀察日期：{r['date']}")\n        lines.append(f"{n}. {r['code']} {r['name']}｜{r['stage']}\n{breakout_label}\n收盤{r['close']}｜A底{r['a_low']}｜B頸線{r['b_neckline']}｜C底{r['c_low']}\n量比{r['volume_ratio']}｜試單區{r['entry_lower']}～{r['entry_upper']}｜停損{r['stop_price']}｜{r['entry_status']}")
    payload=json.dumps({"messages":[{"type":"text","text":"\n".join(lines)[:4900]}]},ensure_ascii=False).encode()
    req=urllib.request.Request("https://api.line.me/v2/bot/message/broadcast",data=payload,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json; charset=UTF-8"},method="POST")
    with urllib.request.urlopen(req,timeout=30) as response:print("N-bottom LINE status:",response.status)
    return 0

if __name__=="__main__":raise SystemExit(main())
