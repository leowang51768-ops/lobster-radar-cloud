#!/usr/bin/env python3
"""Append one official TWSE + TPEx trade date and keep a rolling six-month price window."""
import calendar, datetime as dt, json, re, sqlite3, sys
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DB=Path(__file__).with_name('lobster_tw_6m_prices.sqlite')
day=dt.date.fromisoformat(sys.argv[1]) if len(sys.argv)>1 else dt.date.today()

def six_months_before(d):
    year=d.year
    month=d.month-6
    while month<=0:
        month+=12; year-=1
    return dt.date(year,month,min(d.day,calendar.monthrange(year,month)[1]))

def get(url):
    retry = Retry(
        total=6,
        connect=6,
        read=6,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET']),
        raise_on_status=False,
    )
    with requests.Session() as session:
        session.mount('https://', HTTPAdapter(max_retries=retry))
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; lobster-radar-cloud/1.0)',
            'Accept': 'application/json,text/plain,*/*',
        })
        response = session.get(url, timeout=90)
        response.raise_for_status()
        return response.json()

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

cutoff=six_months_before(day).isoformat()
con=sqlite3.connect(DB)
try:
    con.execute("CREATE TABLE IF NOT EXISTS incremental_log(date TEXT PRIMARY KEY,twse_rows INTEGER,tpex_rows INTEGER,updated_at_utc TEXT)")
    con.execute('BEGIN IMMEDIATE')
    deleted=con.execute('DELETE FROM prices WHERE date < ?',(cutoff,)).rowcount
    con.execute('DELETE FROM incremental_log WHERE date < ?',(cutoff,))
    con.executemany('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)',tw+otc)
    con.execute("INSERT OR REPLACE INTO metadata VALUES ('latest_trade_date',?)",(day.isoformat(),))
    con.execute("INSERT OR REPLACE INTO incremental_log VALUES (?,?,?,?)",(day.isoformat(),len(tw),len(otc),dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit()
finally:
    con.close()

print(json.dumps({'date':day.isoformat(),'twse_rows':len(tw),'tpex_rows':len(otc),'retention_cutoff':cutoff,'deleted_old_rows':deleted,'mode':'one-day incremental + rolling six-month retention'},ensure_ascii=False))
