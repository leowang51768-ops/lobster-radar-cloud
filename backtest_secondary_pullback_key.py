#!/usr/bin/env python3
from __future__ import annotations

import json, sqlite3
from pathlib import Path
import pandas as pd

BASE=Path(__file__).resolve().parent
DB=BASE/'lobster_tw_6m_prices.sqlite'
FUND=BASE/'fundamental_support.json'
OUT=BASE/'backtest_secondary_key_results.json'
VOL_RATIO_MIN=1.20
VOL_RATIO_MAX=3.00
MIN_VOLUME_LOTS=1000
MIN_TURNOVER=30_000_000
MAX_30MA_EXTENSION=0.08
PULLBACK_TOL=0.01
SUPPORT_BREAK_TOL=0.005

def ema(s,span): return s.ewm(span=span,adjust=False).mean()

def load():
    con=sqlite3.connect(DB)
    try:
        df=pd.read_sql_query("SELECT date,stock_id AS code,stock_name AS name,open,high,low,close,volume,turnover FROM prices ORDER BY stock_id,date",con)
    finally: con.close()
    df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume','turnover']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    df['volume_lots']=df['volume']/1000.0
    return df.dropna(subset=['close'])

def good_codes():
    raw=json.loads(FUND.read_text(encoding='utf-8'))
    return {str(k) for k,v in raw.get('stocks',{}).items() if isinstance(v,dict) and v.get('supported')}

def prep(g):
    x=g.sort_values('date').reset_index(drop=True).copy()
    x['ma20']=x['close'].rolling(20).mean(); x['ma30']=x['close'].rolling(30).mean()
    x['ema6']=ema(x['close'],6); x['ema13']=ema(x['close'],13); x['dif']=x['ema6']-x['ema13']
    x['avg20_lots']=x['volume_lots'].rolling(20).mean(); return x

def vol_ok(r):
    avg=float(r.avg20_lots) if pd.notna(r.avg20_lots) else 0.0
    lots=float(r.volume_lots) if pd.notna(r.volume_lots) else 0.0
    ratio=lots/avg if avg else 0.0
    turn=float(r.turnover) if pd.notna(r.turnover) else 0.0
    return VOL_RATIO_MIN<=ratio<=VOL_RATIO_MAX and lots>=MIN_VOLUME_LOTS and turn>=MIN_TURNOVER

def fwd(x,i,entry):
    o={}
    for n in (5,10,20):
        j=min(i+n,len(x)-1); o[f'ret_{n}d']=None if j<=i else (float(x.iloc[j].close)/entry-1)*100
    fut=x.iloc[i+1:min(i+21,len(x))]
    o['mfe_20d']=None if fut.empty else (float(fut.high.max())/entry-1)*100
    o['mae_20d']=None if fut.empty else (float(fut.low.min())/entry-1)*100
    return o

def run_stock(x):
    out=[]; s={}; last_key=None
    for i in range(34,len(x)):
        t,y=x.iloc[i],x.iloc[i-1]
        if pd.isna(t.ma20) or pd.isna(t.ma30): continue
        standard=bool(t.ma20>y.ma20 and t.close>t.ma20 and t.dif>=y.dif)
        if not standard and not s: continue
        if s.get('support_lower') is not None and float(t.close)<float(s['support_lower'])*(1-SUPPORT_BREAK_TOL):
            s={}; last_key=None; continue
        q=x.iloc[i-3:i+1]
        below20=q['ma20'].notna().all() and bool((q['close']<q['ma20']).all())
        d=q['dif'].tolist(); dif_weak=all(pd.notna(v) for v in d) and d[1]<d[0] and d[2]<d[1] and d[3]<d[2]
        if below20 or dif_weak: s={}; last_key=None; continue
        date=t.date.strftime('%Y-%m-%d')
        if 'three_above30_date' not in s:
            for j in range(max(2,i-15),i+1):
                q3=x.iloc[j-2:j+1]
                if q3['ma30'].notna().all() and bool((q3['close']>q3['ma30']).all()):
                    s['three_above30_date']=x.iloc[j].date.strftime('%Y-%m-%d'); break
        if s.get('three_above30_date') and not s.get('pullback_date') and i>=3:
            prior3_low=float(x.iloc[i-3:i]['low'].min())
            if float(t.low)<=prior3_low*(1+PULLBACK_TOL):
                s['pullback_date']=date; s['pullback_i']=i
        if s.get('pullback_date') and not s.get('key_date'):
            pb_i=int(s['pullback_i']); elapsed=i-pb_i
            if 0<=elapsed<=2 and t.close>t.ma30:
                s['key_date']=date; s['key_high']=float(t.high); s['key_low']=float(t.low)
                pullback_low=float(x.iloc[pb_i:i+1]['low'].min())
                s['support_lower']=min(pullback_low,float(t.ma30)); s['support_upper']=max(pullback_low,float(t.ma30))
            elif elapsed>2:
                s={}; last_key=None; continue
        if s.get('key_date') and date>s['key_date']:
            ext=float(t.close)/float(t.ma30)-1
            if standard and t.close>s['key_high'] and vol_ok(t) and ext<=MAX_30MA_EXTENSION:
                key=(s['key_date'],s['key_high'])
                if key!=last_key:
                    entry=float(s['key_high']); row={'date':date,'entry':entry}; row.update(fwd(x,i,entry)); out.append(row); last_key=key
    return out

def rate(vals):
    a=[v for v in vals if v is not None]; return round(100*sum(v>0 for v in a)/len(a),2) if a else None

def avg(vals):
    a=[v for v in vals if v is not None]; return round(sum(a)/len(a),2) if a else None

def main():
    df=load(); good=good_codes(); trades=[]
    for code,g in df.groupby('code',sort=False):
        if str(code) in good: trades.extend(run_stock(prep(g)))
    res={
      'data_start':df['date'].min().strftime('%Y-%m-%d'),'data_end':df['date'].max().strftime('%Y-%m-%d'),'stocks_in_db':int(df['code'].astype(str).nunique()),'fundamental_supported_now':len(good),'signals':len(trades),
      'win_rate_5d_pct':rate([r['ret_5d'] for r in trades]),'win_rate_10d_pct':rate([r['ret_10d'] for r in trades]),'win_rate_20d_pct':rate([r['ret_20d'] for r in trades]),
      'avg_ret_5d_pct':avg([r['ret_5d'] for r in trades]),'avg_ret_10d_pct':avg([r['ret_10d'] for r in trades]),'avg_ret_20d_pct':avg([r['ret_20d'] for r in trades]),
      'avg_mfe_20d_pct':avg([r['mfe_20d'] for r in trades]),'avg_mae_20d_pct':avg([r['mae_20d'] for r in trades]),
      'rule':'Secondary 30MA pullback key: after 3 closes above 30MA, wait for pullback, reclaim 30MA within 3 candles to form secondary key, then first later close above key high with four-point technical conditions, qualified volume/liquidity, <=8% extension above 30MA; structural support invalidation retained.',
      'method_note':'Uses current fundamental_support.json as fixed historical eligibility proxy because point-in-time fundamental snapshots are unavailable. Isolated research only; live rules unchanged.'}
    OUT.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(res,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
