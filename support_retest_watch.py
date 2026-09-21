#!/usr/bin/env python3
"""N字底觀察雷達：取代舊突破回踩觀察；不產生正式推薦。"""
from __future__ import annotations
import csv, json, os, sqlite3, urllib.request
from pathlib import Path
import pandas as pd

BASE=Path(__file__).resolve().parent
DB=BASE/"lobster_tw_6m_prices.sqlite"
OUTPUT=BASE/"n_bottom_watch.csv"
FIELDS=["date","code","name","market","stage","close","a_low","b_neckline","c_low","invalidation","volume_ratio","avg20_lots","avg20_turnover"]

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
                if close>B*1.003:
                    if float(x.iloc[i-1].close)>B*1.003: continue
                    stage="突破B點"
                elif close>=B*.97:
                    stage="接近B點"
                elif close>float(x.iloc[i-1].close) and close>float(x.iloc[c].close):
                    stage="C點形成"
                else: continue
                v5=float(x.iloc[i-5:i].volume.mean())
                ratio=float(t.volume)/v5 if v5>0 else 0
                if stage=="突破B點" and ratio<1.2: continue
                candidates.append(({"date":str(t.date)[:10],"code":str(t.code),"name":str(t["name"]),"market":str(t.market),"stage":stage,"close":round(close,2),"a_low":round(A,2),"b_neckline":round(B,2),"c_low":round(C,2),"invalidation":round(C*.99,2),"volume_ratio":round(ratio,2),"avg20_lots":round(float(x.iloc[-20:].volume.mean()/1000),0),"avg20_turnover":round(float(x.iloc[-20:].turnover.mean()),0)},c))
    if not candidates:return None
    return max(candidates,key=lambda item: ({"突破B點":3,"接近B點":2,"C點形成":1}[item[0]["stage"]],item[1]))[0]

def main():
    if not DB.exists(): raise SystemExit(f"Database missing: {DB}")
    with sqlite3.connect(DB) as con:
        market=pd.read_sql_query("SELECT date,market,stock_id AS code,stock_name AS name,open,high,low,close,volume,turnover FROM prices ORDER BY stock_id,date",con)
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
    rows.sort(key=lambda r:({"突破B點":0,"接近B點":1,"C點形成":2}[r["stage"]],-r["volume_ratio"],r["code"]))
    with OUTPUT.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    print(json.dumps({"date":latest,"n_bottom_observations":len(rows),"formal_recommendations_changed":False},ensure_ascii=False))
    token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","").strip()
    if not token:
        print("LINE token missing; CSV only");return 0
    if not rows and os.getenv("N_BOTTOM_FORCE_NOTIFY")!="1":
        print("No N-bottom observations; LINE skipped");return 0
    lines=[f"🦞 龍蝦雷達｜N字底觀察｜{latest}","僅觀察，非買進推薦；不納入正式推薦績效。"]
    if not rows:lines.append("目前無符合條件個股")
    for n,r in enumerate(rows[:10],1):
        lines.append(f"{n}. {r['code']} {r['name']}｜{r['stage']}\n收盤{r['close']}｜A底{r['a_low']}｜B頸線{r['b_neckline']}｜C底{r['c_low']}\n量比{r['volume_ratio']}｜結構失效參考{r['invalidation']}")
    payload=json.dumps({"messages":[{"type":"text","text":"\n".join(lines)[:4900]}]},ensure_ascii=False).encode()
    req=urllib.request.Request("https://api.line.me/v2/bot/message/broadcast",data=payload,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json; charset=UTF-8"},method="POST")
    with urllib.request.urlopen(req,timeout=30) as response:print("N-bottom LINE status:",response.status)
    return 0

if __name__=="__main__":raise SystemExit(main())
