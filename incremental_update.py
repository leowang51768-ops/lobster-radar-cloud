#!/usr/bin/env python3
"""Append one official TWSE + TPEx trade date to lobster_tw_6m_prices.sqlite."""
import datetime as dt, json, re, sqlite3, subprocess, sys, tempfile
from pathlib import Path

DB=Path(__file__).with_name('lobster_tw_6m_prices.sqlite')
day=dt.date.fromisoformat(sys.argv[1]) if len(sys.argv)>1 else dt.date.today()

def get(url):
    with tempfile.NamedTemporaryFile(suffix='.json') as f:
        subprocess.run(['curl','-L','--compressed','--retry','6','--retry-all-errors','--max-time','90','-sS',url,'-o',f.name],check=True)
        return json.load(open(f.name,encoding='utf-8'))
def n(v,integer=False):
    s=re.sub(r'[,\s]','',str(v or '')).replace('X','')
    if s in {'','--','---','-','N/A'}: return None
    try: x=float(s); return int(round(x)) if integer else x
    except ValueError: return None
def ordinary(c,name):
    return bool(re.fullmatch(r'\d{4}',c)) and not c.startswith(('0','91')) and not any(x in name.upper() for x in ('特別股','存託','DR','ETF','ETN','權證','受益'))
def parse(data,market):
    if str(data.get('stat','')).lower()!='ok': return []
    for t in data.get('tables',[]):
        f=t.get('fields') or []
        if market=='上市' and '證券代號' in f and '收盤價' in f:
            ix={x:i for i,x in enumerate(f)}; out=[]
            for r in t.get('data',[]):
                c,name=str(r[ix['證券代號']]).strip(),str(r[ix['證券名稱']]).strip()
                if ordinary(c,name): out.append((day.isoformat(),market,c,name,n(r[ix['開盤價']]),n(r[ix['最高價']]),n(r[ix['最低價']]),n(r[ix['收盤價']]),n(r[ix['成交股數']],True),n(r[ix['成交金額']],True)))
            return out
        if market=='上櫃' and '代號' in f and '收盤' in f and '成交金額(元)' in f:
            ix={x:i for i,x in enumerate(f)}; out=[]
            for r in t.get('data',[]):
                c,name=str(r[ix['代號']]).strip(),str(r[ix['名稱']]).strip()
                if ordinary(c,name): out.append((day.isoformat(),market,c,name,n(r[ix['開盤']]),n(r[ix['最高']]),n(r[ix['最低']]),n(r[ix['收盤']]),n(r[ix['成交股數']],True),n(r[ix['成交金額(元)']],True)))
            return out
    return []

ymd=day.strftime('%Y%m%d')
tw=parse(get(f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json'),'上市')
otc=parse(get(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={day.strftime('%Y/%m/%d')}&id=&response=json"),'上櫃')
if not tw or not otc: raise SystemExit(f'Not committed: official data incomplete for {day}; TWSE={len(tw)}, TPEx={len(otc)}')
con=sqlite3.connect(DB)
con.executemany('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)',tw+otc)
con.execute("INSERT OR REPLACE INTO metadata VALUES ('latest_trade_date',?)",(day.isoformat(),))
con.execute("CREATE TABLE IF NOT EXISTS incremental_log(date TEXT PRIMARY KEY,twse_rows INTEGER,tpex_rows INTEGER,updated_at_utc TEXT)")
con.execute("INSERT OR REPLACE INTO incremental_log VALUES (?,?,?,?)",(day.isoformat(),len(tw),len(otc),dt.datetime.now(dt.timezone.utc).isoformat()))
con.commit(); con.close()
print(json.dumps({'date':day.isoformat(),'twse_rows':len(tw),'tpex_rows':len(otc),'mode':'one-day incremental only'},ensure_ascii=False))
