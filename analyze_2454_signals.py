#!/usr/bin/env python3
import json
import backtest_primary_key as pk
import backtest_current_rules as cr

CODE='3131'

def prep_one(mod):
    df=mod.load_data()
    g=df[df['code'].astype(str)==CODE]
    if g.empty:
        return None,None
    x=mod.prep(g)
    name=str(x.iloc[-1].get('name', CODE))
    return x,name

out={'code':CODE}

x,name=prep_one(pk)
out['name']=name
out['primary_key_signals']=[] if x is None else pk.run_stock(x,CODE,name)

x,name=prep_one(cr)
all_current=[] if x is None else cr.run_stock(x,CODE,name)
out['strong20_signals']=[r for r in all_current if r.get('route')=='強勢20MA續強']
out['secondary_30ma_signals']=[r for r in all_current if r.get('route')=='30MA回踩']
out['counts']={
 'primary_key':len(out['primary_key_signals']),
 'strong20':len(out['strong20_signals']),
 'secondary_30ma':len(out['secondary_30ma_signals']),
 'total':len(out['primary_key_signals'])+len(out['strong20_signals'])+len(out['secondary_30ma_signals'])
}
with open('analysis_2454_signals.json','w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps(out,ensure_ascii=False,indent=2))
