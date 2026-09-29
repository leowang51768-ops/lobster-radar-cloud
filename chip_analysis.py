"""Dated institutional evidence only; no broker/overnight-trader inference."""
import datetime
import json
import math
import os
import time
import urllib.parse
import urllib.request

REQUIRED = {'Foreign_Investor', 'Investment_Trust', 'Dealer_self', 'Dealer_Hedging'}

def missing(reason, data_date=''):
    return dict(available=False, reason=reason, data_date=data_date, foreign=None,
                trust=None, total=None, trust_days=0, foreign_days=0,
                summary='資料未齊，暫不判讀', score=0, history=[])


def parse_institutional(data, code, day, sessions):
    """Require five consecutive market sessions; zero must be explicit, not absent."""
    by_date = {}
    for row in data:
        date = str(row.get('date', ''))
        if str(row.get('stock_id', '')) != code or date > day:
            continue
        name = row.get('name')
        if name == 'Dealer_Self':
            name = 'Dealer_self'
        if name not in REQUIRED:
            continue
        try:
            buy, sell = float(row['buy']), float(row['sell'])
            if not all(math.isfinite(v) and v >= 0 for v in (buy, sell)):
                raise ValueError('invalid volume')
        except (KeyError, TypeError, ValueError):
            return missing('法人買賣數值缺漏或無效', date)
        values = by_date.setdefault(date, {})
        if name in values:
            return missing('法人類別重複，待核對', date)
        values[name] = (buy - sell) / 1000
    latest = max(by_date, default='')
    expected = sorted(d for d in sessions if d <= day)[-5:]
    if len(expected) != 5 or expected[-1] != day:
        return missing('交易日曆不足五日或非目標日', latest)
    if any(not REQUIRED.issubset(by_date.get(d, {})) for d in expected):
        return missing('目標日或近五交易日法人資料未齊', latest)
    history = [dict(date=d, foreign=by_date[d]['Foreign_Investor'],
                    trust=by_date[d]['Investment_Trust'],
                    dealer_self=by_date[d]['Dealer_self'],
                    dealer_hedging=by_date[d]['Dealer_Hedging']) for d in expected]
    current, previous = history[-1], history[-2]
    labels, streaks = [], {}
    score = 0
    for key, label in [('foreign', '外資'), ('trust', '投信')]:
        streak = 0
        for row in reversed(history):
            if row[key] <= 0:
                break
            streak += 1
        streaks[key] = streak
        net = current[key]
        # Each investor group: 0-5 points; negative evidence reduces its
        # contribution without vetoing a strategy-qualified stock.
        score += min(5, streak + 1) if net > 0 else (1 if net == 0 else 0)
        if streak >= 2:
            labels.append(f'{label}連買{streak}日' + ('以上' if streak == 5 else ''))
        elif net > 0:
            labels.append(f'{label}單日買超')
        elif net < 0:
            labels.append(f'{label}' + ('轉賣' if previous[key] > 0 else '賣超'))
        else:
            labels.append(f'{label}買賣持平')
    return dict(available=True, reason='', data_date=day,
                foreign=current['foreign'], trust=current['trust'],
                total=sum(current[k] for k in ('foreign', 'trust', 'dealer_self', 'dealer_hedging')),
                foreign_days=streaks['foreign'], trust_days=streaks['trust'],
                summary='｜'.join(labels), score=score, history=history)


def fetch_institutional(code, day, sessions):
    start = (datetime.date.fromisoformat(day) - datetime.timedelta(days=35)).isoformat()
    query = urllib.parse.urlencode(dict(dataset='TaiwanStockInstitutionalInvestorsBuySell',
                                      data_id=code, start_date=start, end_date=day))
    headers = {'User-Agent': 'lobster-radar/2.0'}
    token = os.getenv('FINMIND_TOKEN', '').strip()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request('https://api.finmindtrade.com/api/v4/data?' + query,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if payload.get('msg') != 'success' or not isinstance(payload.get('data'), list):
            return missing('法人來源回應失敗或無資料')
        return parse_institutional(payload['data'], code, day, sessions)
    except Exception as exc:
        # Do not print request headers or credentials.
        print(f'CHIP_SOURCE_WARNING code={code} type={type(exc).__name__}')
        return missing('法人來源暫時無法取得')


def load_institutional(codes, day, sessions, fetch=None, sleep=None):
    """Three passes, retry missing tickers only, one minute between passes."""
    fetch = fetch or fetch_institutional
    sleep = sleep or time.sleep
    results = {}
    pending = sorted(set(codes))
    for attempt in range(3):
        for code in pending:
            results[code] = fetch(code, day, sessions)
        pending = [code for code in pending if not results[code]['available']]
        if not pending:
            break
        print(f'CHIP_PENDING attempt={attempt + 1} count={len(pending)}')
        if attempt < 2:
            sleep(60)
    return results
