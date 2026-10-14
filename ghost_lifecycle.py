"""Shared candidate lifecycle. Estimates and branch evidence never confirm a pattern."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

BAD = {'invalid', 'failed', 'retest_failed', 'cancelled', 'deleted'}

def sessions_after(start, end, is_trade_day):
    a, b = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    if b < a:
        return -1
    n = 0
    while a < b:
        a += timedelta(days=1)
        n += bool(is_trade_day(a))
    return n

def event_id(kind, code, signal_id, source_date):
    return hashlib.sha256(f'{kind}|{code}|{signal_id}|{source_date}'.encode()).hexdigest()[:32]

def branch_candidates(feed):
    out = []
    for stock in feed.get('stocks', []):
        if not isinstance(stock, dict) or not str(stock.get('code') or '').isdigit():
            continue
        branches = stock.get('branches') or []
        active = [b for b in branches if isinstance(b, dict) and
                  (int(b.get('consecutive_buy_days') or 0) > 0 or
                   int(b.get('recent_buy_days_5') or 0) >= 2)]
        if not active:
            continue
        # Event changes only for genuinely newer buying evidence, not a rebuild time.
        evidence_date = max(str(b.get('last_recent_buy_day') or b.get('data_through') or '')[:10]
                            for b in active)
        if not evidence_date:
            evidence_date = str(stock.get('data_through') or feed.get('data_through') or '')[:10]
        if not evidence_date:
            continue
        out.append({**stock, 'evidence_date': evidence_date,
                    'signal_id': f'branch|{stock["code"]}|{evidence_date}'})
    return out

def reconcile(previous, radar, feed, events, reference_date, completed_date, is_trade_day):
    """Merge durable state, formal radar signals and immutable local events.

    A is always displayed A1 with a separate age. D starts at zero on confirmation.
    A expires at its tenth completed session; D expires at D10 close.
    """
    state = deepcopy(previous or {})
    stocks = state.setdefault('stocks', {})
    archive = state.setdefault('archive', {})
    applied = state.setdefault('applied_events', {})

    def retire(code, reason, day):
        row = stocks[code]
        if row.get('stage') == 'POOL':
            return
        row.update(stage='POOL', retired_date=day, exit_reason=reason)
        archive[row['signal_id']] = deepcopy(row)

    def confirmed(raw, source):
        code = str(raw.get('code') or '')
        day = str(raw.get('confirmed_date') or raw.get('signal_date') or '')[:10]
        if not code.isdigit() or not day or raw.get('estimate'):
            return
        try:
            if sessions_after(day, reference_date, is_trade_day) < 0:
                return
            support = float(raw.get('retest_support') or raw.get('support') or raw.get('pivot') or 0)
            close = float(raw.get('breakout_close') or raw.get('close') or 0)
        except (ValueError, TypeError):
            return
        if support <= 0 or close <= 0:
            return
        raw = dict(raw)
        # Legacy radar expired at visible D10 (zero-based day9). The 240 lifecycle
        # owns expiry at D10 close; legacy expiry cannot remove one session early.
        if raw.get('status') == 'expired':
            raw['status'] = 'tracking'
        sid = str(raw.get('signal_id') or raw.get('tracking_key') or f'{day}|{code}')
        old = stocks.get(code, {})
        if old.get('confirmed_date', '') > day or sid in archive:
            return
        if old.get('stage') == 'D' and old.get('signal_id') == sid:
            old['pick'].update(raw)
            old['sources'] = sorted(set(old.get('sources', [])) | {source})
        else:
            if old.get('stage') in ('A1', 'D'):
                archive[old['signal_id']] = deepcopy(old)
            sources = set(old.get('sources', [])) | {source}
            stocks[code] = {'code': code, 'name': raw.get('name') or code,
                            'stage': 'D', 'signal_id': sid, 'confirmed_date': day,
                            'tracking_day': 0, 'sources': sorted(sources), 'pick': deepcopy(raw)}
        if raw.get('status') in BAD:
            retire(code, 'pattern_invalid', str(raw.get('resolved_date') or reference_date)[:10])

    # Process all dated radar rows in order so the newest event stays authoritative.
    for raw in sorted((r for r in radar.values() if isinstance(r, dict)),
                      key=lambda r: str(r.get('signal_date') or '')):
        confirmed(raw, 'radar')

    for eid, ev in sorted((events or {}).items(), key=lambda pair: str(pair[1].get('occurred_at') or '')):
        if eid in applied:
            continue
        code = str(ev.get('code') or '')
        if ev.get('kind') == 'pattern_confirmed' and ev.get('formally_verified') is True:
            confirmed(ev.get('pick') or {}, 'monitor')
        elif ev.get('kind') == 'return_pool' and code.isdigit() and ev.get('signal_id'):
            day = str(ev.get('source_date') or '')[:10]
            try:
                valid = sessions_after(day, reference_date, is_trade_day) >= 0
            except (ValueError, TypeError):
                valid = False
            if valid:
                if code not in stocks:
                    stocks[code] = {'code':code,'name':ev.get('name') or code,'stage':'A1',
                        'signal_id':ev['signal_id'],'first_seen_date':ev.get('first_seen_date') or day,
                        'sources':['monitor']}
                row=stocks[code]
                same = row.get('signal_id') == ev.get('signal_id')
                # Migrated 239 A rows may have an older first-seen identifier.
                # A return covers that episode only; newer buying evidence stays active.
                same_a_episode = row.get('stage')=='A1' and row.get('first_seen_date','')<=day
                if same or same_a_episode:
                    retire(code, ev.get('reason') or 'returned', day)
        applied[eid] = ev.get('occurred_at') or reference_date

    for raw in branch_candidates(feed):
        code, day = str(raw['code']), raw['evidence_date']
        old = stocks.get(code, {})
        if old.get('stage') in ('A1', 'D'):
            old['branch'] = raw
            old['sources'] = sorted(set(old.get('sources', [])) | {'branch'})
            continue
        # Historical permanent membership alone cannot restart a retired candidate.
        if old.get('retired_date') and day <= old['retired_date']:
            continue
        if raw['signal_id'] in archive:
            continue
        if old.get('stage') == 'POOL':
            archive[old['signal_id']] = deepcopy(old)
        stocks[code] = {'code': code, 'name': raw.get('name') or code,
                        'stage': 'A1', 'signal_id': raw['signal_id'],
                        'first_seen_date': day, 'sources': ['branch'], 'branch': raw}

    for code, row in stocks.items():
        if row['stage'] == 'A1':
            row['a_age'] = max(1, sessions_after(row['first_seen_date'], reference_date, is_trade_day) + 1)
            if completed_date and sessions_after(row['first_seen_date'], completed_date, is_trade_day) >= 9:
                retire(code, 'A1_unconfirmed_10_sessions', completed_date)
        elif row['stage'] == 'D':
            row['tracking_day'] = max(0, sessions_after(row['confirmed_date'], reference_date, is_trade_day))
            if completed_date and sessions_after(row['confirmed_date'], completed_date, is_trade_day) >= 10:
                retire(code, 'D10_complete', completed_date)
    state.update(version=240, as_of_date=reference_date)
    return state

def active_picks(state):
    result = []
    for row in state.get('stocks', {}).values():
        if row.get('stage') != 'D':
            continue
        p = deepcopy(row['pick'])
        p.update(code=row['code'], name=row['name'], signal_id=row['signal_id'],
                 tracking_key=row['signal_id'], signal_date=row['confirmed_date'],
                 confirmed_date=row['confirmed_date'], tracking_day=row['tracking_day'],
                 lifecycle_stage='D', sources=row['sources'])
        result.append(p)
    return result
