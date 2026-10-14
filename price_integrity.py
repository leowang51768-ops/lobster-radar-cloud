"""Validate official daily prices. Missing single stocks are quarantined, never backfilled with stale prices."""
import argparse
import json
import math
import os
import sqlite3
from pathlib import Path

from stock_universe import STOCKS_TO_TRACK
from scan_trade_date import resolve_trade_date


def requested_date():
    return resolve_trade_date(explicit=os.environ.get('TRADE_DATE'))


def assess_rows(rows, trade_date):
    expected = {ticker.split('.')[0]: '上櫃' if ticker.endswith('.TWO') else '上市'
                for ticker in STOCKS_TO_TRACK}
    valid = set()
    invalid = set()
    valid_rows = []
    for row in rows:
        day, market, code, name, *values = row
        if code not in expected:
            continue
        usable = (day == trade_date and market == expected[code] and len(values) == 6)
        try:
            numbers = [float(v) for v in values]
            usable = usable and all(math.isfinite(v) for v in numbers)
            o, h, lo, close, volume, turnover = numbers
            usable = usable and 0 < lo <= min(o, close) <= max(o, close) <= h
            usable = usable and volume >= 0 and turnover >= 0
        except (ValueError, TypeError):
            usable = False
        if usable:
            valid.add(code)
            valid_rows.append(row)
        else:
            invalid.add(code)

    missing = sorted(set(expected) - valid)
    report = {
        'trade_date': trade_date,
        'expected': len(expected),
        'usable': len(valid),
        'missing_codes': missing,
        'invalid_codes': sorted(invalid),
        'valid_codes': sorted(valid),
    }
    return report, valid_rows


def validate_rows(rows, trade_date, require_all=True):
    report, valid_rows = assess_rows(rows, trade_date)
    if report['usable'] <= 0:
        raise RuntimeError(json.dumps({'error': 'PRICE_SOURCE_EMPTY', **report}, ensure_ascii=False))
    if require_all and (report['missing_codes'] or report['invalid_codes']):
        raise RuntimeError(json.dumps({'error': 'PRICE_SOURCE_INCOMPLETE', **report}, ensure_ascii=False))
    return trade_date


def validate_database(db, trade_date=None, require_all=True):
    day = trade_date or requested_date()
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as con:
        rows = con.execute(
            'SELECT date,market,stock_id,stock_name,open,high,low,close,volume,turnover '
            'FROM prices WHERE date=?', (day,)
        ).fetchall()
    report, _ = assess_rows(rows, day)
    validate_rows(rows, day, require_all=require_all)
    return report


def guard_market_frame(frame):
    day = requested_date()
    # Explicit historical runs must never use candles after their requested day.
    frame = frame.loc[frame['date'].astype(str).str[:10] <= day].copy()
    today = frame.loc[frame['date'].astype(str).str[:10] == day].copy()
    today['date'] = today['date'].astype(str).str[:10]
    rows = list(
        today[['date','market','code','name','open','high','low','close','volume','turnover']]
        .astype({'date': str}).itertuples(index=False, name=None)
    )
    report, _ = assess_rows(rows, day)
    if report['usable'] <= 0:
        raise RuntimeError(json.dumps({'error': 'PRICE_SOURCE_EMPTY', **report}, ensure_ascii=False))

    # Critical rule: stocks without valid current-day data are removed from the
    # entire scan frame so scanners cannot silently fall back to yesterday.
    valid_codes = set(report['valid_codes'])
    frame = frame.loc[frame['code'].astype(str).isin(valid_codes)].copy()

    if report['missing_codes'] or report['invalid_codes']:
        print('PRICE_PARTIAL_WARNING', json.dumps({
            'trade_date': day,
            'usable': report['usable'],
            'missing_codes': report['missing_codes'],
            'invalid_codes': report['invalid_codes'],
            'action': '缺資料個股排除當日掃描，不使用前一日舊價',
        }, ensure_ascii=False))
    return frame


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    report = validate_database(
        Path(__file__).with_name('lobster_tw_6m_prices.sqlite'),
        require_all=not args.allow_partial,
    )
    print('PRICE_INTEGRITY_OK', json.dumps(report, ensure_ascii=False))
