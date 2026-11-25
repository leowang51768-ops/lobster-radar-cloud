"""Validate official daily prices. Missing single stocks are quarantined, never backfilled with stale prices."""
import argparse
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

from stock_universe import STOCKS_TO_TRACK
from scan_trade_date import resolve_trade_date


def fixed_price_universe():
    return {
        ticker.split('.')[0]: '上櫃' if ticker.endswith('.TWO') else '上市'
        for ticker in STOCKS_TO_TRACK
    }


def daily_price_universe():
    """Add the permanent watchlist for the official price workflow."""
    expected = fixed_price_universe()
    path = Path(__file__).parent / 'branch_tracker' / 'data' / 'extra_universe.json'
    with path.open(encoding='utf-8-sig') as source:
        payload = json.load(source)
    if not isinstance(payload, dict) or not isinstance(payload.get('stocks'), dict):
        raise ValueError(f'Invalid permanent watchlist: {path}')

    unsupported = []
    for code in payload['stocks']:
        if code in expected:
            continue
        if not re.fullmatch(r'[1-9][0-9]{3}', code) or code.startswith('91'):
            unsupported.append(code)
            continue
        # The cloud list has bare codes; the official quote supplies the market.
        expected[code] = None
    if unsupported:
        print('PRICE_UNSUPPORTED_CODES', json.dumps(sorted(unsupported)), file=sys.stderr)
    return expected


def requested_date():
    return resolve_trade_date(explicit=os.environ.get('TRADE_DATE'))


def assess_rows(rows, trade_date, expected=None):
    if expected is None:
        expected = fixed_price_universe()
    valid = set()
    invalid = set()
    valid_rows = []
    for row in rows:
        day, market, code, name, *values = row
        if code not in expected:
            continue
        expected_market = expected[code]
        usable = (day == trade_date and market in ('上市', '上櫃')
                  and (expected_market is None or market == expected_market)
                  and len(values) == 6)
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


def check_report(report, require_all):
    if report['usable'] <= 0:
        raise RuntimeError(json.dumps({'error': 'PRICE_SOURCE_EMPTY', **report}, ensure_ascii=False))
    if require_all and (report['missing_codes'] or report['invalid_codes']):
        raise RuntimeError(json.dumps({'error': 'PRICE_SOURCE_INCOMPLETE', **report}, ensure_ascii=False))


def validate_rows(rows, trade_date, require_all=True):
    report, _ = assess_rows(rows, trade_date)
    check_report(report, require_all)
    return trade_date


def validate_database(db, trade_date=None, require_all=True, expected=None):
    day = trade_date or requested_date()
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as con:
        rows = con.execute(
            'SELECT date,market,stock_id,stock_name,open,high,low,close,volume,turnover '
            'FROM prices WHERE date=?', (day,)
        ).fetchall()
    report, _ = assess_rows(rows, day, expected=expected)
    check_report(report, require_all)
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
        expected=daily_price_universe(),
    )
    print('PRICE_INTEGRITY_OK', json.dumps(report, ensure_ascii=False))
