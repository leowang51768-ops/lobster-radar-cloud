#!/usr/bin/env python3
"""Produce a readable daily audit from persisted evidence, without new requests."""
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA=Path(__file__).resolve().parent/'data'

def cell(value):
    return str(value if value is not None else '—').replace('|','\\|').replace('\n',' ')

def number(value):
    return '待驗證／缺資料' if value in (None,'') else f'{float(value):,.2f}'

def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(cell(v) for v in row)+' |' for row in rows])

def main():
    if not (DATA/'branches.sqlite').exists():
        raise RuntimeError('尚無資料庫，不能產生採集成功報告')
    status=json.loads((DATA/'status.json').read_text(encoding='utf-8'))
    day=status['date']
    with sqlite3.connect(DATA/'branches.sqlite') as db:
        integrity=db.execute('PRAGMA quick_check').fetchone()[0]
        coverage=db.execute('SELECT code,status,rows,error FROM stock_status WHERE day=? ORDER BY code',(day,)).fetchall()
        actual=db.execute('SELECT COUNT(*) FROM ranking WHERE day=?',(day,)).fetchone()[0]
        numeric_bad=db.execute('SELECT COUNT(*) FROM ranking WHERE day=? AND (buy<0 OR sell<0 OR ABS(buy-sell-net)>2)',(day,)).fetchone()[0]
        missing_price=db.execute('SELECT COUNT(*) FROM ranking r LEFT JOIN official_prices p ON p.day=r.day AND p.code=r.code WHERE r.day=? AND (p.close IS NULL OR p.close<=0 OR p.volume IS NULL OR p.volume<=0)',(day,)).fetchone()[0]
        queued,history_success=db.execute('SELECT COUNT(*),SUM(last_success IS NOT NULL) FROM queue').fetchone()
        errors=db.execute('SELECT code,name,last_attempt,error FROM queue WHERE error IS NOT NULL ORDER BY code,branch').fetchall()
        examples=db.execute('SELECT r.code,r.side,r.name,r.buy,r.sell,r.net,p.volume,p.close,r.url FROM ranking r LEFT JOIN official_prices p ON p.day=r.day AND p.code=r.code WHERE r.day=? AND r.code=? AND r.rank<=3 ORDER BY r.side,r.rank',(day,'6531')).fetchall()
        if not examples:
            examples=db.execute('SELECT r.code,r.side,r.name,r.buy,r.sell,r.net,p.volume,p.close,r.url FROM ranking r LEFT JOIN official_prices p ON p.day=r.day AND p.code=r.code WHERE r.day=? ORDER BY r.code,r.side,r.rank LIMIT 6',(day,)).fetchall()
    maturity={h:[0,0] for h in (5,10,20)}
    relationship=DATA/'price_relationship.csv'
    if relationship.exists():
        with relationship.open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f):
                h=int(r['horizon_sessions']);maturity[h][r['status']!='mature']+=1
    good=sum(r[1]=='ok' for r in coverage)
    matches=good==status['covered_stocks'] and good==status['expected_stocks'] and sum(r[2] for r in coverage if r[1]=='ok')==actual
    now=datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')
    report=[f'# 分點追蹤稽核｜{day}',f'\n資料日期：**{day}**。報告產生時間：{now}（台灣時間）。請先核對資料日期；舊日期的報告不代表今天採集成功。',
            '\n## 今天是否收齊\n',table(['項目','實際結果'],[
                ('當日排行股票',f"{good}／{status['expected_stocks']}檔"),('當日排行筆數',f'{actual:,}筆'),
                ('完整採集交易日',status['complete_collection_days']),('歷史明細累積',f"{status['counts']['history']:,}筆"),
                ('曾成功補抓的股票／分點組合',f'{history_success or 0}／{queued}組；其餘仍待補抓'),
                ('仍留有補抓錯誤的組合',len(errors)),('資料庫容量',f"{status['database_bytes']/1024/1024:.2f} MiB")]),
            '\n排行完整不等於歷史完整；只取得買超／賣超各前15名，未上榜不代表沒有交易。歷史回填不能算成當時已知訊號。',
            '\n## 可重算的資料檢查\n',table(['檢查','結果'],[
                ('資料庫結構',integrity),('163檔覆蓋及排行筆數與狀態報告一致','通過' if matches else '未通過'),
                ('買進－賣出與淨買超不符（允許2張四捨五入差）',f'{numeric_bad}筆'),
                ('當日排行缺有效官方收盤價或成交量',f'{missing_price}筆')]),
            '\n## 股價驗證到期狀態\n',table(['觀察期間','已有後續股價','尚未到期或缺價格'],[(f'{h}個交易日',f'{counts[0]:,}筆',f'{counts[1]:,}筆') for h,counts in maturity.items()]),
            '\n同一筆分點排行有3個觀察期間，不能當成3筆獨立成功交易。尚未到期不填零報酬；滿期後仍須看樣本數、失敗案例、同股同期基準及樣本外結果。',
            '\n## 實際抽樣：核對分點數量與股價\n',
            table(['股票','方向','分點','買進張','賣出張','淨買超張','成交量張','淨買超占比','收盤元'],[
                (c,'買超榜' if side=='buy' else '賣超榜',name,number(buy),number(sell),number(net),number(volume/1000 if volume else None),number(net*1000/volume*100 if volume else None)+'%',number(close))
                for c,side,name,buy,sell,net,volume,close,url in examples]),
            '\n計算方式：淨買超占比＝淨買超張數÷當日成交量張數×100%。來源張數有四捨五入；官方原始成交量單位為股，先除以1000。',
            '\n抽樣來源（可人工比對日期、分點與買賣數量）：\n'+ '\n'.join(f'- [{c} {name}]({url})' for c,side,name,buy,sell,net,volume,close,url in examples),
            '\n## 補抓錯誤與待處理項目\n',
            table(['股票','分點','最後嘗試日期','錯誤'],errors[:10]) if errors else '目前沒有保留的補抓錯誤。',
            f'\n共{len(errors)}組保留錯誤，上表最多顯示10組。錯誤可能是先前嘗試留下的，修正後尚待輪到重抓；不得當成零交易。',
            '\n## 163檔逐檔稽核\n',table(['股票','採集狀態','當日筆數','缺漏原因'],coverage),
            '\n[查看原始覆蓋表](coverage.csv) · [採集狀態](status.json) · [股價驗證狀態](relationship_status.json) · [執行紀錄](https://github.com/leowang51768-ops/lobster-radar-cloud/actions)',
            '\n目前沒有依此自動判定關鍵分點或變更雷達進場規則。']
    text='\n'.join(report)+'\n'
    (DATA/'README.md').write_text(text,encoding='utf-8')
    archive=DATA/'audits';archive.mkdir(exist_ok=True)
    (archive/f'{day}.md').write_text(text,encoding='utf-8')
    print(f'Audit saved: {day}, stocks {good}, rows {actual}, checks {integrity}/{matches}/{numeric_bad}/{missing_price}')

if __name__=='__main__':main()
