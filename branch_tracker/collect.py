#!/usr/bin/env python3
"""Independent branch evidence collection. No signals, scores, or messages."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOST = 'https://concords.moneydj.com'


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.text, self.row, self.cell = [], [], None, None
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.ignore += 1
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = {'text': '', 'href': None}
        elif tag == 'a' and self.cell is not None:
            self.cell['href'] = attrs.get('href')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignore = max(0, self.ignore - 1)
        if tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.cell['text'] = self.cell['text'].strip()
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row, self.cell = None, None

    def handle_data(self, data):
        if self.ignore:
            return
        self.text.append(data)
        if self.cell is not None:
            self.cell['text'] += data


def num(text):
    value = float(text.replace(',', '').replace('%', '').strip())
    if not (-1e15 < value < 1e15):
        raise ValueError('Non-finite or implausible numeric value')
    return value


def branch_link(href, code):
    url = urllib.parse.urljoin(HOST, href or '')
    parsed = urllib.parse.urlparse(url)
    args = urllib.parse.parse_qs(parsed.query)
    if parsed.hostname != 'concords.moneydj.com' or parsed.path.lower() != '/z/zc/zco/zco0/zco0.djhtm':
        raise ValueError('Unexpected branch link')
    if args.get('a') != [code] or not args.get('b') or not args.get('BHID'):
        raise ValueError('Branch identity missing or stock mismatch')
    return args['b'][0], args['BHID'][0], url


def parse_rank(html, code, day):
    p = TableReader(); p.feed(html)
    text = ''.join(p.text)
    if not re.search(r'\(' + re.escape(code) + r'\)', text):
        raise ValueError('Stock identity missing')
    match = re.search(r'最後更新日[：:]\s*(\d{4}/\d{2}/\d{2})', text)
    if not match or match[1].replace('/', '-') != day:
        raise ValueError('Source date missing or stale')
    if not re.search(r'單位[：:]\s*張', text):
        raise ValueError('Unexpected unit')
    if not any(len(r) == 10 and r[0]['text'] == '買超券商' and r[5]['text'] == '賣超券商' for r in p.rows):
        raise ValueError('Ranking columns missing')
    out = []
    for row in p.rows:
        if len(row) != 10 or not row[0]['href'] or not row[5]['href']:
            continue
        for offset, side in ((0, 'buy'), (5, 'sell')):
            cells = row[offset:offset+5]
            b, broker, url = branch_link(cells[0]['href'], code)
            buy, sell, net, ratio = [num(c['text']) for c in cells[1:]]
            if min(buy, sell, net, ratio) < 0 or ratio > 100:
                raise ValueError('Invalid ranking numeric range')
            if abs((buy - sell) - (net if side == 'buy' else -net)) > 2:
                raise ValueError('Buy/sell/net mismatch beyond rounding tolerance')
            out.append((side, b, broker, cells[0]['text'], buy, sell, net if side == 'buy' else -net, ratio, url))
    if not out or len(out) > 30:
        raise ValueError('Ranking row count invalid')
    return out


def parse_history(html, code, branch, broker, day):
    p = TableReader(); p.feed(html)
    text = ''.join(p.text)
    if not re.search(r'\(' + re.escape(code) + r'\)', text) or '單一券商歷史明細' not in text:
        raise ValueError('History identity/title mismatch')
    # Static source builds selectors with JavaScript. Verify server-rendered
    # navigation identities when selectors are absent; never execute page scripts.
    navigation=[]
    for url in re.findall(r'/z/zc/zco/zco0/zco0\.djhtm\?[^\s\"\'<>]+',html,re.I):
        query=urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        identity={k.lower():v for k,v in query.items()}
        if all(k in identity for k in ('a','b','bhid')):
            navigation.append(identity)
    server_identity=bool(navigation) and all(v['a']==[code] and v['b']==[branch] and v['bhid']==[broker] for v in navigation)
    # Require response identity, rather than trusting the requested URL.
    for name, expected in (('sel_BrokerBranch', branch), ('sel_Broker', broker)):
        select = re.search(r'<select\b[^>]*\bname\s*=\s*[\"\']?' + name + r'[\"\']?[^>]*>(.*?)</select>', html, re.I | re.S)
        if not select:
            if server_identity:continue
            raise ValueError('History response identity missing or mismatched')
        options = re.findall(r'<option\b([^>]*)>', select[1], re.I)
        chosen = next((a for a in options if re.search(r'\bselected\b', a, re.I)), options[0] if options else '')
        value = re.search(r'\bvalue\s*=\s*[\"\']?([^\s\"\'>]+)', chosen, re.I)
        if not value or value[1] != expected:
            raise ValueError(f'History {name} identity mismatch: expected {expected}, got {value[1] if value else None}')
    out = []
    for row in p.rows:
        if len(row) != 5 or not re.fullmatch(r'\d{4}/\d{2}/\d{2}', row[0]['text']):
            continue
        date = row[0]['text'].replace('/', '-')
        buy, sell, total, net = [num(c['text']) for c in row[1:]]
        if date > day or min(buy, sell, total) < 0 or abs(buy-sell-net) > 2 or abs(buy+sell-total) > 2:
            raise ValueError('History date or numeric mismatch')
        out.append((date, buy, sell, total, net))
    if not out or len({r[0] for r in out}) != len(out) or len(out) > 60:
        raise ValueError('History count invalid')
    return out


class Fetcher:
    def __init__(self, delay):
        self.delay, self.last = delay, 0.0

    def get(self, url):
        time.sleep(max(0, self.delay - (time.monotonic()-self.last)))
        self.last = time.monotonic()
        req = urllib.request.Request(url, headers={'User-Agent': 'LobsterBranchResearch/1.0', 'Accept': 'text/html'})
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Unexpected page size')
        html = raw.decode('cp950', errors='replace')
        if any(x in html.lower() for x in ('verify you are human', 'checking your browser', 'automated traffic', 'captcha')):
            raise RuntimeError('Source verification block; collector stopped')
        return html, hashlib.sha256(raw).hexdigest()


def open_db(path):
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS ranking(day TEXT,code TEXT,side TEXT,rank INTEGER,branch TEXT,broker TEXT,name TEXT,buy REAL,sell REAL,net REAL,ratio REAL,url TEXT,hash TEXT,collected_at TEXT,PRIMARY KEY(day,code,side,branch,broker));
    CREATE TABLE IF NOT EXISTS history(day TEXT,code TEXT,branch TEXT,broker TEXT,buy REAL,sell REAL,total REAL,net REAL,collected_at TEXT,PRIMARY KEY(day,code,branch,broker));
    CREATE TABLE IF NOT EXISTS queue(code TEXT,branch TEXT,broker TEXT,name TEXT,url TEXT,first_seen TEXT,last_seen TEXT,last_attempt TEXT,last_success TEXT,error TEXT,PRIMARY KEY(code,branch,broker));
    CREATE TABLE IF NOT EXISTS stock_status(day TEXT,code TEXT,status TEXT,rows INTEGER,error TEXT,PRIMARY KEY(day,code));
    CREATE TABLE IF NOT EXISTS official_prices(day TEXT,code TEXT,close REAL,volume REAL,PRIMARY KEY(day,code));
    ''')
    return db


def collect(args):
    from stock_universe import STOCKS_TO_TRACK
    universe = {t.split('.')[0]: n for t,n in STOCKS_TO_TRACK.items()}
    codes = args.codes.split(',') if args.codes else sorted(universe)
    if not set(codes) <= set(universe):
        raise ValueError('Only fixed-universe stocks allowed')
    day = args.day or datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    data = ROOT/'branch_tracker'/'data'; data.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(ROOT/'lobster_tw_6m_prices.sqlite') as prices:
        today_codes = {r[0] for r in prices.execute('SELECT stock_id FROM prices WHERE date=?', (day,))}
        if not today_codes:
            print(json.dumps({'day':day,'status':'no_official_prices','note':'休市或上游未更新；不採用舊資料'},ensure_ascii=False))
            return 0
        if not set(universe) <= today_codes:
            raise RuntimeError('Official prices incomplete; collection deferred')
        price_rows = prices.execute('SELECT date,stock_id,close,volume FROM prices WHERE date<=?',(day,)).fetchall()
    db = open_db(data/'branches.sqlite')
    db.executemany('INSERT OR IGNORE INTO official_prices VALUES(?,?,?,?)',[r for r in price_rows if r[1] in universe])
    db.commit()
    fetch = Fetcher(args.delay)
    stop = False
    for code in codes:
        if db.execute("SELECT 1 FROM stock_status WHERE day=? AND code=? AND status='ok'",(day,code)).fetchone():
            continue
        try:
            html,digest = fetch.get(f'{HOST}/z/zc/zco/zco_{code}.djhtm')
            rows = parse_rank(html,code,day)
            stamp = datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')
            with db:
                db.execute('DELETE FROM ranking WHERE day=? AND code=?',(day,code))
                ranks = {'buy':0,'sell':0}
                for side,b,broker,name,buy,sell,net,ratio,url in rows:
                    ranks[side] += 1
                    rank = ranks[side]
                    db.execute('INSERT INTO ranking VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(day,code,side,rank,b,broker,name,buy,sell,net,ratio,url,digest,stamp))
                    if rank <= 3:
                        db.execute('INSERT INTO queue(code,branch,broker,name,url,first_seen,last_seen) VALUES(?,?,?,?,?,?,?) ON CONFLICT(code,branch,broker) DO UPDATE SET name=excluded.name,url=excluded.url,last_seen=excluded.last_seen',(code,b,broker,name,url,day,day))
                db.execute('INSERT OR REPLACE INTO stock_status VALUES(?,?,?,?,?)',(day,code,'ok',len(rows),None))
        except Exception as e:
            with db: db.execute('INSERT OR REPLACE INTO stock_status VALUES(?,?,?,?,?)',(day,code,'failed',0,str(e)[:250]))
            # Do not keep hitting the same source on access denial/rate limiting.
            if isinstance(e,urllib.error.HTTPError) and e.code in (403,429) or isinstance(e,RuntimeError):
                stop = True; break
    queue = db.execute('SELECT code,branch,broker,name,url FROM queue WHERE (last_attempt IS NULL OR last_attempt<?) ORDER BY COALESCE(last_success,\'\'),first_seen,code,branch LIMIT ?',(day,args.history_budget)).fetchall() if not stop else []
    history_ok = 0
    for code,b,broker,name,url in queue:
        try:
            html,_ = fetch.get(url+'&C=3')
            records = parse_history(html,code,b,broker,day)
            stamp = datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')
            with db:
                for date,buy,sell,total,net in records:
                    db.execute('INSERT OR REPLACE INTO history VALUES(?,?,?,?,?,?,?,?,?)',(date,code,b,broker,buy,sell,total,net,stamp))
                db.execute('UPDATE queue SET last_attempt=?,last_success=?,error=NULL WHERE code=? AND branch=? AND broker=?',(day,day,code,b,broker))
            history_ok += 1
        except Exception as e:
            with db: db.execute('UPDATE queue SET last_attempt=?,error=? WHERE code=? AND branch=? AND broker=?',(day,str(e)[:250],code,b,broker))
            if isinstance(e,urllib.error.HTTPError) and e.code in (403,429) or isinstance(e,RuntimeError):
                stop = True; break
    statuses = db.execute('SELECT code,status,rows,error FROM stock_status WHERE day=? ORDER BY code',(day,)).fetchall()
    good = sum(r[1]=='ok' for r in statuses)
    counts = {table:db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('ranking','history','queue')}
    history_errors=[{'code':c,'branch':b,'error':e} for c,b,e in db.execute('SELECT code,branch,error FROM queue WHERE error IS NOT NULL ORDER BY code,branch LIMIT 60')]
    observed_days = db.execute("SELECT COUNT(DISTINCT day) FROM stock_status WHERE status='ok'").fetchone()[0]
    full_days = db.execute("SELECT COUNT(*) FROM (SELECT day FROM stock_status WHERE status='ok' GROUP BY day HAVING COUNT(*)=?)",(len(universe),)).fetchone()[0]
    report = {'date':day,'mode':'collection_only','expected_stocks':len(universe),'covered_stocks':good,'coverage_complete':good==len(universe),'status':'complete' if good==len(universe) else 'partial','source_blocked':stop,'ranking_limit_per_side':15,'history_target_top_per_side':3,'history_requests_succeeded':history_ok,'observed_collection_days':observed_days,'complete_collection_days':full_days,'counts':counts,'review_stage':'可開始資料品質複盤' if full_days>=20 else '資料累積中','strategy_changes':False,'notes':['未上榜不等於零交易','歷史回填以今天發現的分點為條件，存在選樣偏差；不得冒充當時已知訊號','60日紀錄不代表60筆獨立交易樣本；未建立關鍵分點勝率判定','數字單位為張，原頁有四捨五入；股價成交量使用官方來源且成交量單位為股','排行榜與逐日歷史分開保存，不互相覆蓋'],'missing_stocks':[c for c in universe if not any(r[0]==c and r[1]=='ok' for r in statuses)],'errors':[{ 'code':r[0],'error':r[3]} for r in statuses if r[1]!='ok']}
    (data/'status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report['history_errors']=history_errors
    report['history_coverage_complete']=counts['queue']>0 and db.execute('SELECT COUNT(*) FROM queue WHERE last_success IS NULL').fetchone()[0]==0
    with (data/'coverage.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['date','code','status','rows','error']);writer.writerows((day,*r) for r in statuses)
    db.close()
    report['database_bytes']=(data/'branches.sqlite').stat().st_size
    (data/'status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if good==len(codes) and not stop else 1


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--day');parser.add_argument('--codes')
    parser.add_argument('--history-budget',type=int,default=60)
    parser.add_argument('--delay',type=float,default=1.5)
    raise SystemExit(collect(parser.parse_args()))
