"""Bounded retry planning and dated raw evidence; no trading signals."""
import time


def ensure_attempts(db):
    db.execute('''CREATE TABLE IF NOT EXISTS history_attempts(
        day TEXT,code TEXT,branch TEXT,broker TEXT,attempts INTEGER,
        last_at REAL,error TEXT,PRIMARY KEY(day,code,branch,broker))''')


def plan_history(db, day, budget, now=None, retry_seconds=900, max_attempts=3, attempt_day=None):
    """Prefer never-fetched and missing recent sessions, with a daily retry cap.

    Source C=3 returns at most 60 rows. Older gaps remain explicitly unresolved;
    missing rows are never synthesized as zero trades.
    """
    ensure_attempts(db)
    now = time.time() if now is None else now
    sessions = {}
    for code, date in db.execute(
            'SELECT code,day FROM official_prices WHERE day<=? ORDER BY day DESC', (day,)):
        dates = sessions.setdefault(code, [])
        if len(dates) < 60:
            dates.append(date)
    known = {}
    for code, branch, broker, date in db.execute(
            'SELECT code,branch,broker,day FROM history WHERE day<=?', (day,)):
        known.setdefault((code, branch, broker), set()).add(date)
    attempt_day = attempt_day or day
    attempts = {(c,b,k):(n,at) for c,b,k,n,at in db.execute(
        'SELECT code,branch,broker,attempts,last_at FROM history_attempts WHERE day=?', (attempt_day,))}
    planned = []
    for code,b,broker,name,url,last_success in db.execute(
            'SELECT code,branch,broker,name,url,last_success FROM queue'):
        if last_success and last_success >= day:
            continue
        n, at = attempts.get((code,b,broker), (0,0))
        if n >= max_attempts or (n and now-at < retry_seconds):
            continue
        dates = sessions.get(code, [])
        have = known.get((code,b,broker), set())
        missing = sum(d not in have for d in dates)
        # Fairness across runs: unseen / oldest successful fetch first. Within
        # that tier prefer greater verifiable gaps, then deterministic identity.
        priority = (last_success or '', -missing, code,b,broker)
        planned.append((priority,(code,b,broker,name,url)))
    return [row for _,row in sorted(planned)[:max(0,budget)]]


def record_attempt(db, day, code, branch, broker, now=None):
    ensure_attempts(db)
    db.execute('''INSERT INTO history_attempts VALUES(?,?,?,?,1,?,NULL)
        ON CONFLICT(day,code,branch,broker) DO UPDATE SET
        attempts=history_attempts.attempts+1,last_at=excluded.last_at,error=NULL''',
        (day,code,branch,broker,time.time() if now is None else now))
    db.commit()


def finish_attempt(db, day, code, branch, broker, error=None):
    db.execute('UPDATE history_attempts SET error=? WHERE day=? AND code=? AND branch=? AND broker=?',
               (error,day,code,branch,broker))


def append_dated_raw_evidence(db, feed, stock_names, day):
    """Publish actual daily rankings even when no persistent key branch exists.

    No invented buy streak, grade, or historical zeroes. Only stocks with a
    validated current-date ranking are admitted. Existing conclusions remain.
    """
    existing = {str(r['code']) for r in feed}
    for code in sorted(stock_names):
        if code in existing:
            continue
        ok = db.execute("SELECT 1 FROM stock_status WHERE day=? AND code=? AND status='ok'",(day,code)).fetchone()
        if not ok:
            continue
        rows = db.execute('''SELECT branch,broker,name,buy,sell,net,side,rank FROM ranking
            WHERE day=? AND code=? ORDER BY ABS(net) DESC,side,rank LIMIT 1''',(day,code)).fetchall()
        if not rows:
            continue
        branches = []
        for branch,broker,name,buy,sell,net,side,rank in rows:
            branches.append({'code':code,'name':stock_names[code],
                'branch':branch,'broker':broker,'branch_name':name,
                'latest_day':day,'as_of_date':day,'net_buy_lots':net,
                'buy_lots':buy,'sell_lots':sell,'ranking_side':side,'latest_rank':rank,
                'formal_key_branch':False,'signal_class':'none','status':'none',
                'live_status':'當日排行榜參考',
                'display':'有資料｜當日排行榜參考；尚未確認連續買賣與關鍵分點',
                'evidence_source':'validated_daily_ranking','history_verified':False})
        feed.append({'code':code,'name':stock_names[code],'data_through':day,
            'watch_branch_count':0,'active_branch_count':0,'formal_key_branch_count':0,
            'branch_signal':'有資料｜當日排行榜參考','branches':branches})
    feed.sort(key=lambda r:str(r['code']))
