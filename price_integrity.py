"""Fail closed unless every tracked stock has usable prices for the requested day."""
import json
import math
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_universe import STOCKS_TO_TRACK
from scan_trade_date import resolve_trade_date


def requested_date():
    return resolve_trade_date(explicit=os.environ.get('TRADE_DATE'))


def validate_rows(rows, trade_date):
    expected = {ticker.split('.')[0]: '上櫃' if ticker.endswith('.TWO') else '上市'
                for ticker in STOCKS_TO_TRACK}
    valid = set()
    invalid = set()
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
        else:
            invalid.add(code)
    missing = sorted(set(expected) - valid)
    if missing or invalid:
        raise RuntimeError(json.dumps({'error': 'PRICE_SOURCE_INCOMPLETE',
            'trade_date': trade_date, 'expected': len(expected), 'usable': len(valid),
            'missing_codes': missing, 'invalid_codes': sorted(invalid)}, ensure_ascii=False))
    return trade_date


def validate_database(db, trade_date=None):
    day = trade_date or requested_date()
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as con:
        rows = con.execute('SELECT date,market,stock_id,stock_name,open,high,low,close,volume,turnover '
                           'FROM prices WHERE date=?', (day,)).fetchall()
    return validate_rows(rows, day)


def guard_market_frame(frame):
    day = requested_date()
    # Explicit historical runs must never use candles after their requested day.
    frame = frame.loc[frame['date'].astype(str).str[:10] <= day].copy()
    today = frame.loc[frame['date'].astype(str).str[:10] == day]
    today = today.copy()
    today['date'] = today['date'].astype(str).str[:10]
    validate_rows(today[['date','market','code','name','open','high','low','close','volume','turnover']]
                  .astype({'date': str}).itertuples(index=False, name=None), day)
    return frame


if __name__ == '__main__':
    print('PRICE_INTEGRITY_OK', validate_database(Path(__file__).with_name('lobster_tw_6m_prices.sqlite')))
