"""Research only: measure post-event performance per stock and broker branch."""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

HORIZONS = (5, 10, 20)
MIN_AMOUNT = 10_000_000


def calculate(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        prices = defaultdict(dict)
        days = set()
        for day, code, close in db.execute(
                'SELECT day,code,close FROM official_prices WHERE close>0 AND volume>0'):
            days.add(day)
            prices[code][day] = close
        sessions = sorted(days)
        if not sessions:
            raise ValueError('No valid official trading sessions')
        positions = {d: i for i, d in enumerate(sessions)}
        names = {(code, branch, broker): name for code, branch, broker, name in db.execute(
            'SELECT code,branch,broker,name FROM queue')}
        histories = defaultdict(dict)
        for day, code, branch, broker, net in db.execute('SELECT day,code,branch,broker,net FROM history'):
            if day in positions:
                histories[code, branch, broker][positions[day]] = net
        observed = defaultdict(dict)
        for day, code, branch, broker, net in db.execute(
                "SELECT r.day,r.code,r.branch,r.broker,r.net FROM ranking r JOIN stock_status s "
                "ON r.day=s.day AND r.code=s.code WHERE s.status='ok'"):
            if day in positions:
                observed[code, branch, broker][positions[day]] = net

    def calc_return(code, i, j):
        if i < 0 or j >= len(sessions):
            return None
        a, b = prices[code].get(sessions[i]), prices[code].get(sessions[j])
        return 100 * (b / a - 1) if a and b else None

    def events(code, series):
        side = prev = None
        streak = amount = 0
        emitted = False
        found = defaultdict(list)
        for i, net in sorted(series.items()):
            close = prices[code].get(sessions[i])
            current = 'buy' if net > 0 else 'sell' if net < 0 else None
            if not current or not close or prev != i - 1 or side != current:
                streak, amount, emitted = 0, 0, False
            prev, side = i, current
            if current and close:
                streak += 1
                amount += abs(net) * close * 1000
                if streak >= 3 and amount > MIN_AMOUNT and not emitted:
                    found[current].append(i)
                    emitted = True
        return found

    def summarize(code, triggers, horizon):
        samples, excess, last_end, available = [], [], -1, 0
        for i in triggers:
            if i <= last_end:
                continue
            result = calc_return(code, i, i + horizon)
            if result is None:
                continue
            last_end = i + horizon
            samples.append(result)
            # Use only completed, non-overlapping same-stock reference windows
            # from before the signal. Require three prices with a usable return.
            refs = [calc_return(code, i - 1 - k * horizon, i - 1 - (k - 1) * horizon)
                    for k in range(1, 7)]
            refs = [r for r in refs if r is not None]
            if len(refs) >= 3:
                available += 1
                excess.append(result - median(refs))
        return {'events': len(samples), 'baseline_matched': available,
                'median_return_pct': round(median(samples), 3) if samples else None,
                'median_excess_pp': round(median(excess), 3) if excess else None,
                'above_baseline_fraction': round(sum(x > 0 for x in excess) / len(excess), 4) if excess else None}

    rows, labels = [], Counter()
    for key in sorted(histories.keys() | observed.keys()):
        code, branch, broker = key
        historical = events(code, histories.get(key, {}))
        recorded = events(code, observed.get(key, {}))
        for side in ('buy', 'sell'):
            old, new = historical.get(side, []), recorded.get(side, [])
            if not old and not new:
                continue
            stats = {str(h): {'historical': summarize(code, old, h),
                              'recorded': summarize(code, new, h)} for h in HORIZONS}
            h10 = stats['10']['historical']
            n = max(h10['baseline_matched'], stats['10']['recorded']['baseline_matched'])
            label = '資料不足' if n < 5 else '觀察' if n < 10 else '歷史樣本達門檻，待新資料驗證'
            labels[label] += 1
            if n < 5:
                continue
            rows.append({'code': code, 'branch': branch, 'broker': broker,
                         'branch_name': names.get(key, ''), 'side': side, 'state': label,
                         'last_recorded_event': sessions[new[-1]] if new else None, 'metrics': stats})
    rows.sort(key=lambda r: (r['code'], r['broker'], r['branch'], r['side']))
    return {'schema_version': 1, 'data_through': sessions[-1],
            'first_price_date': sessions[0], 'state_counts': dict(labels),
            'registry_entries': len(rows), 'confirmed_key_branches': 0,
            'method': {'trigger': '同向連續至少3個交易日且淨額估計超過1000萬元，首次達標只記一次',
                       'missing': '缺日、未上榜及缺價格不視為零，不跨缺口計連續',
                       'baseline': '訊號前最多6個同股非重疊期間的報酬中位數，至少3個；未作市況配對',
                       'historical': '歷史回填和當時採集分開統計，兩組可能重疊，不可相加；尚未作樣本外驗證'},
            'registry': rows}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('database', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    result = calculate(a.database)
    a.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('data_through','state_counts','registry_entries')}, ensure_ascii=False))
