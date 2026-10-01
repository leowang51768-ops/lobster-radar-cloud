#!/usr/bin/env python3
"""Pair prospectively saved branch observations with later official closes."""
import csv
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

DATA=Path(__file__).resolve().parent/'data'
HORIZONS=(5,10,20)


def quantity_shares(buy,sell,net,volume):
    share=lambda lots:lots*1000/volume*100 if volume and volume>0 else None
    return {'buy_share_pct':share(buy),'sell_share_pct':share(sell),
            'net_share_pct':share(net),'gross_two_sided_share_pct':share(buy+sell)}


def prior_price_state(prices,sessions,position,code,horizon):
    if position<horizon:return None
    values=[prices.get((d,code)) for d in sessions[position-horizon:position+1]]
    if any(v is None or v<=0 for v in values):return None
    return {'return_pct':(values[-1]/values[0]-1)*100,
            'close_range_pct':(max(values)/min(values)-1)*100}


def quantity_correlation(rows):
    pairs=[(abs(r['net_share_pct']),r['return_pct']) for r in rows if r['net_share_pct'] is not None]
    if len(pairs)<3 or len({x for x,y in pairs})<2 or len({y for x,y in pairs})<2:
        return len(pairs),None
    return len(pairs),statistics.correlation([x for x,y in pairs],[y for x,y in pairs])


def price_change(prices,sessions,position,code,horizon):
    window=sessions[position:position+horizon+1]
    if len(window)!=horizon+1:
        return None
    values=[prices.get((d,code)) for d in window]
    if any(v is None or v<=0 for v in values):
        return None
    entry=values[0]
    return {'end_day':window[-1], 'return_pct':(values[-1]/entry-1)*100,
            'max_close_gain_pct':max(v/entry-1 for v in values[1:])*100,
            'min_close_return_pct':min(v/entry-1 for v in values[1:])*100}


def analyze(db):
    prices={(d,c):p for d,c,p in db.execute('SELECT day,code,close FROM official_prices')}
    volumes={(d,c):v for d,c,v in db.execute('SELECT day,code,volume FROM official_prices')}
    sessions=sorted({d for d,c in prices})
    position={d:i for i,d in enumerate(sessions)}
    groups=defaultdict(list)
    detailed=[]
    for day,code,side,rank,branch,broker,name,net,buy,sell in db.execute('SELECT day,code,side,rank,branch,broker,name,net,buy,sell FROM ranking ORDER BY day,code,side,rank'):
        for h in HORIZONS:
            outcome=price_change(prices,sessions,position[day],code,h) if day in position else None
            volume=volumes.get((day,code))
            # Source branch quantities are lots; official market volume is shares.
            row={'signal_date':day,'code':code,'branch':branch,'broker':broker,'name':name,
                 'side':side,'rank':rank,'net_lots':net,'buy_lots':buy,'sell_lots':sell,
                 'market_volume_shares':volume,**quantity_shares(buy,sell,net,volume),
                 'horizon_sessions':h,'observation_type':'prospective_ranking',
                 'base_close':prices.get((day,code)), 'status':'mature' if outcome else 'pending_or_missing_price',
                 'price_basis':'official_unadjusted_close',
                 'corporate_actions':'not_yet_checked'}
            for lookback in (5,20):
                prior=prior_price_state(prices,sessions,position[day],code,lookback) if day in position else None
                for metric in ('return_pct','close_range_pct'):
                    row[f'prior_{lookback}_sessions_{metric}']=prior[metric] if prior else None
            row.update(outcome or {'end_day':None,'return_pct':None,'max_close_gain_pct':None,'min_close_return_pct':None})
            detailed.append(row)
            if outcome:groups[(code,branch,broker,side,h)].append(row)
    summary=[]
    for (code,branch,broker,side,h),rows in sorted(groups.items()):
        chosen=[]; last_end=-1
        for row in rows:
            if position[row['signal_date']]>=last_end:
                chosen.append(row);last_end=position[row['end_day']]
        start,end=position[rows[0]['signal_date']],position[rows[-1]['signal_date']]
        baseline=[price_change(prices,sessions,i,code,h) for i in range(start,end+1)]
        baseline=[r['return_pct'] for r in baseline if r]
        returns=[r['return_pct'] for r in chosen]
        mean=statistics.mean(returns)
        quantity_n,quantity_r=quantity_correlation(chosen)
        summary.append({'code':code,'branch':branch,'broker':broker,'name':rows[-1]['name'],
                        'side':side,'horizon_sessions':h,'mature_daily_observations':len(rows),
                        'nonoverlapping_windows':len(chosen),'average_close_return_pct':mean,
                        'positive_close_fraction':sum(r>0 for r in returns)/len(returns),
                        'same_stock_same_period_baseline_pct':statistics.mean(baseline) if baseline else None,
                        'difference_vs_baseline_pct':mean-statistics.mean(baseline) if baseline else None,
                        'average_min_close_return_pct':statistics.mean(r['min_close_return_pct'] for r in chosen),
                        'quantity_return_pair_count':quantity_n,
                        'abs_net_share_vs_future_return_pearson':quantity_r,
                        'quantity_interpretation':'量占比與後續報酬的描述性相關；非因果或預測能力，少量樣本不作判斷',
                        'interpretation':'描述性關係；尚未扣費用、檢查除權息或做樣本外驗證；不評分、不認定關鍵分點'})
    return detailed,summary


def save_csv(path,rows,empty_headers):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else empty_headers)
        writer.writeheader();writer.writerows(rows)


def main():
    path=DATA/'branches.sqlite'
    if not path.exists():
        print('No branch evidence yet; relationship analysis skipped');return
    with sqlite3.connect(path) as db:
        detail,summary=analyze(db)
    save_csv(DATA/'price_relationship.csv',detail,['signal_date','code','branch','horizon_sessions','status'])
    save_csv(DATA/'relationship_summary.csv',summary,['code','branch','horizon_sessions','nonoverlapping_windows','interpretation'])
    status={'observations_with_horizons':len(detail),'mature_rows':sum(r['status']=='mature' for r in detail),
            'pending_or_missing_rows':sum(r['status']!='mature' for r in detail),'summary_groups':len(summary),
            'historical_backfill_included':False,'strategy_changes':False,
            'note':'只配對採集當天已知的排行榜與之後股價；逐日歷史回填另存，避免事後選樣混入。非重疊期間仍不保證統計獨立。'}
    (DATA/'relationship_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False))


if __name__=='__main__':main()
