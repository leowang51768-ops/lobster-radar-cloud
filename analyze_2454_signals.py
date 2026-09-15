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
# === 補在第 36 行下方：發送 LINE 推播 ===
import os
import requests

token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
user_id = os.environ.get("LINE_USER_ID")

if token and user_id:
    msg_lines = ["🚨 【龍蝦雷達與 30MA 訊號日報】"]
    
    # 1. 整理 30MA 訊號
    ma30_list = out.get('secondary_30ma_signals', [])
    if ma30_list:
        msg_lines.append("\n📈 【30MA 回踩/突破訊號】")
        for item in ma30_list[:5]:  # 取前 5 筆避免過長
            msg_lines.append(f"• {item.get('code','')} {item.get('name','')} | 收盤: {item.get('close','')} (防守: {item.get('stop_loss','')})")
    else:
        msg_lines.append("\n📈 【30MA 訊號】：今日無符合標的")

    # 發送 LINE Push Notification
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": "\n".join(msg_lines)}]}
    requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)
