"""First-run candidate evidence, separate from actual LINE notification tracking."""
import csv
import datetime
import json
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
SNAPSHOTS = BASE / 'chip_candidate_snapshots.jsonl'
OUTCOMES = BASE / 'chip_candidate_outcomes.csv'
DB = BASE / 'lobster_tw_6m_prices.sqlite'


def record_candidates(day, candidates, selected):
    # Historical replay must not fabricate point-in-time chip evidence.
    if day != datetime.datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat():
        return
    old = [json.loads(line) for line in SNAPSHOTS.read_text().splitlines() if line] if SNAPSHOTS.exists() else []
    seen = {(r['signal_date'], r['code'], r['route']) for r in old}
    stamp = datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')
    added = []
    for row in candidates:
        key = (day, row['code'], row['route'])
        if key in seen:
            continue
        # Selection is not proof of delivery; actual delivery remains in
        # line_pick_snapshots.csv, written only after accepted LINE response.
        added.append(dict(row, signal_date=day, observed_at=stamp,
                          selected_for_digest=row in selected,
                          version='chip-ranking-v1'))
        seen.add(key)
    if added:
        with SNAPSHOTS.open('a', encoding='utf-8') as handle:
            for row in added:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


def update_outcomes():
    snaps = [json.loads(line) for line in SNAPSHOTS.read_text().splitlines() if line] if SNAPSHOTS.exists() else []
    fields = ['signal_date', 'code', 'route', 'selected_for_digest', 'institutional_available',
              'chip_summary', 'horizon', 'entry_date', 'entry_open', 'exit_date', 'exit_close',
              'return_pct', 'close_below_pivot', 'close_below_stop', 'intraday_stop_touched', 'status']
    results = []
    with sqlite3.connect(DB) as con:
        dates = [r[0] for r in con.execute('SELECT DISTINCT date FROM prices ORDER BY date')]
        for snap in snaps:
            future = [d for d in dates if d > snap['signal_date']]
            prices = {r[0]: r[1:] for r in con.execute(
                'SELECT date,open,close,low FROM prices WHERE stock_id=? AND date>?',
                (snap['code'], snap['signal_date']))}
            for horizon in (1, 3, 5, 10, 20):
                row = {k: snap.get(k, '') for k in fields[:6]}
                row.update(horizon=horizon, status='未到期')
                window = future[:horizon]
                if len(window) == horizon:
                    observed = [prices.get(d) for d in window]
                    if any(p is None or any(v is None or v <= 0 for v in p) for p in observed):
                        row['status'] = '官方股價不完整'
                    else:
                        entry, close = observed[0][0], observed[-1][1]
                        row.update(entry_date=window[0], entry_open=entry, exit_date=window[-1],
                                   exit_close=close, return_pct=round((close / entry - 1) * 100, 4),
                                   close_below_pivot=int(any(p[1] < snap['pivot'] for p in observed)),
                                   close_below_stop=int(any(p[1] < snap['stop'] for p in observed)),
                                   intraday_stop_touched=int(any(p[2] <= snap['stop'] for p in observed)),
                                   status='已到期（未扣成本；非停損成交績效）')
                results.append(row)
    temp = OUTCOMES.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    temp.replace(OUTCOMES)
