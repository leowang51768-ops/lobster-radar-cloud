#!/usr/bin/env python3
"""Walk-forward A/B backtest: base Lobster patterns vs. upside-space + relative-strength plan."""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import buy_signal as engine

BASE=Path(__file__).resolve().parent
OUT_JSON=BASE/"backtest_space_rs_results.json"
OUT_MD=BASE/"backtest_space_rs_report.md"
HORIZONS=(3,5,10,20)
COST=0.00585
COOLDOWN=10
MIN_UPSIDE_ROOM=0.08
MIN_RS20=0.03

def market_rs_map(market):
    x=market[["date","code","close"]].copy().sort_values(["code","date"])
    x["ret20"]=x.groupby("code")["close"].pct_change(20)
    med=x.groupby("date")["ret20"].median()
    return med.to_dict()

def collect(market):
    market_median=market_rs_map(market)
    out=[]
    for raw_code,g in market.groupby("code",sort=False):
        code=str(raw_code); name=str(g.iloc[-1]["name"]); x=engine.prepare(g)
        seen=set()
        for i in range(engine.LOOKBACK+engine.FALSE_BREAK_RECOVERY_DAYS-1,len(x)-1):
            hist=x.iloc[:i+1]
            for detector in (engine.detect_false_break_reversal,engine.detect_true_breakout):
                _,sig=detector(code,hist)
                if sig is None or sig["pattern_key"] in seen: continue
                seen.add(sig["pattern_key"])
                close=float(x.iloc[i].close)
                stock_ret20=close/float(x.iloc[i-20].close)-1 if i>=20 else math.nan
                market_ret20=float(market_median.get(x.iloc[i].date,math.nan))
                rs20=stock_ret20-market_ret20 if math.isfinite(stock_ret20) and math.isfinite(market_ret20) else math.nan
                older=x.iloc[max(0,i-120):max(0,i-20)]
                prior_high=float(older.high.max()) if len(older) else math.nan
                upside=math.inf if not math.isfinite(prior_high) or close>=prior_high else prior_high/close-1
                ma20=float(x.iloc[i].ma20) if pd.notna(x.iloc[i].ma20) else math.nan
                prev_ma20=float(x.iloc[i-1].ma20) if i and pd.notna(x.iloc[i-1].ma20) else math.nan
                ma30=float(x.iloc[i].ma30) if pd.notna(x.iloc[i].ma30) else math.nan
                prev_ma30=float(x.iloc[i-1].ma30) if i and pd.notna(x.iloc[i-1].ma30) else math.nan
                ma20_ok=(math.isfinite(ma20) and math.isfinite(prev_ma20)
                         and close>ma20 and ma20>=prev_ma20)
                ma30_ok=(math.isfinite(ma30) and math.isfinite(prev_ma30)
                         and close>=ma30*0.98 and ma30>=prev_ma30*0.995)
                space_ok=upside>=MIN_UPSIDE_ROOM
                rs_ok=math.isfinite(rs20) and rs20>=MIN_RS20
                out.append({**sig,"name":name,"signal_i":i,
                    "dates":x.date.tolist(),"opens":x.open.astype(float).tolist(),
                    "highs":x.high.astype(float).tolist(),"lows":x.low.astype(float).tolist(),
                    "closes":x.close.astype(float).tolist(),
                    "upside_room":upside,"rs20":rs20,"ma20_ok":ma20_ok,"ma30_ok":ma30_ok,
                    "new_plan_ok":bool(space_ok and rs_ok and ma20_ok and ma30_ok),
                    "no_ma30_ok":bool(space_ok and rs_ok and ma20_ok)})
    return out

def cooldown(signals):
    chosen=[]; last={}
    for s in sorted(signals,key=lambda z:(z["date"],z["code"],z["signal_route"])):
        key=(s["code"],s["signal_route"]); cur=int(s["signal_i"])
        if key in last and cur-last[key]<COOLDOWN: continue
        last[key]=cur; chosen.append(s)
    return chosen

def evaluate(s,h):
    si=int(s["signal_i"]); ei=si+1; ti=si+h
    if ti>=len(s["closes"]): return None
    entry=float(s["opens"][ei])
    if not math.isfinite(entry) or entry<=0:return None
    stop=float(s["support_lower"])*(1-engine.SUPPORT_BREAK_TOL)
    xi=ti; stopped=False
    for j in range(ei,ti+1):
        if float(s["closes"][j])<stop: xi=j; stopped=True; break
    exitp=float(s["closes"][xi]); net=exitp/entry-1-COST
    lows=s["lows"][ei:xi+1]; highs=s["highs"][ei:xi+1]
    return {"date":s["date"],"code":s["code"],"route":s["signal_route"],"horizon":h,
            "net":net,"win":net>0,"stopped":stopped,
            "mae":min(lows)/entry-1,"mfe":max(highs)/entry-1,
            "upside_room":s["upside_room"],"rs20":s["rs20"]}

def summary(rows):
    if not rows:return {"samples":0}
    f=pd.DataFrame(rows); wins=f.loc[f.net>0,"net"]; losses=f.loc[f.net<=0,"net"]
    avgwin=float(wins.mean()) if len(wins) else 0.0
    avgloss=float(losses.mean()) if len(losses) else 0.0
    payoff=avgwin/abs(avgloss) if avgloss else None
    equity=(1+f.net).cumprod(); peak=equity.cummax(); maxdd=float((equity/peak-1).min())
    return {"samples":len(f),"win_rate":round(float(f.win.mean())*100,2),
            "avg_net":round(float(f.net.mean())*100,2),"median_net":round(float(f.net.median())*100,2),
            "avg_win":round(avgwin*100,2),"avg_loss":round(avgloss*100,2),
            "payoff_ratio":None if payoff is None else round(payoff,2),
            "expectancy":round(float(f.net.mean())*100,2),"max_trade_mae":round(float(f.mae.min())*100,2),
            "sequence_max_drawdown":round(maxdd*100,2),"stop_rate":round(float(f.stopped.mean())*100,2)}

def main():
    market=engine.read_market(); signals=cooldown(collect(market))
    variants={
      "基準型態":signals,
      "目前版_20MA加30MA":[s for s in signals if s["new_plan_ok"]],
      "測試版_保留20MA取消30MA":[s for s in signals if s["no_ma30_ok"]],
    }
    result={"generated_at":datetime.now(timezone.utc).isoformat(),
      "data_start":market.date.min().strftime("%Y-%m-%d"),"data_end":market.date.max().strftime("%Y-%m-%d"),
      "entry":"訊號隔日開盤","cost_pct":COST*100,
      "new_plan":f"上方空間>={MIN_UPSIDE_ROOM:.0%}、20日相對市場中位報酬>={MIN_RS20:.0%}；比較20MA+30MA保護與只保留20MA",
      "results":{}}
    for name,sigs in variants.items():
        result["results"][name]={}
        for h in HORIZONS:
            rows=[r for s in sigs if (r:=evaluate(s,h)) is not None]
            result["results"][name][str(h)]=summary(rows)
    OUT_JSON.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 上方空間＋相對強度｜20MA／30MA保護獨立比較","",f"- 官方資料：{result['data_start']}～{result['data_end']}",
      "- 進場：訊號隔日開盤；成本0.585%；收盤跌破結構支撐停損",
      f"- 新版條件：{result['new_plan']}","",
      "|版本|期間|樣本|勝率|平均淨報酬|平均獲利|平均虧損|賺賠比|期望值|序列最大回撤|",
      "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in variants:
      for h in HORIZONS:
        r=result["results"][name][str(h)]
        ratio="—" if r.get("payoff_ratio") is None else f"{r['payoff_ratio']:.2f}"
        lines.append(f"|{name}|{h}日|{r['samples']}|{r.get('win_rate','—')}%|{r.get('avg_net','—')}%|{r.get('avg_win','—')}%|{r.get('avg_loss','—')}%|{ratio}|{r.get('expectancy','—')}%|{r.get('sequence_max_drawdown','—')}%|")
    OUT_MD.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(result["results"],ensure_ascii=False))
if __name__=="__main__": main()
