import sqlite3
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import json
from branch_tracker import final_result
from branch_tracker.collect import open_db
from branch_tracker.backfill import plan_history,record_attempt,finish_attempt,append_dated_raw_evidence


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.db=open_db(':memory:')
        self.day='2026-10-16'
        for code in ('2002','1802'):
            self.db.execute('INSERT INTO queue(code,branch,broker,name,url,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)',
                            (code,'b','k','券商','https://example.test',self.day,self.day))
            for d in ('2026-10-14','2026-10-15',self.day):
                self.db.execute('INSERT INTO official_prices VALUES(?,?,?,?)',(d,code,20,1000000))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_failed_same_day_retry_has_cooldown_and_cap(self):
        for i in range(3):
            now=10000+i*901
            self.assertIn('2002',[r[0] for r in plan_history(self.db,self.day,120,now=now)])
            record_attempt(self.db,self.day,'2002','b','k',now=now)
            finish_attempt(self.db,self.day,'2002','b','k','timeout')
            self.db.execute('UPDATE queue SET last_attempt=?,error=? WHERE code=?',(self.day,'timeout','2002'))
            self.assertNotIn('2002',[r[0] for r in plan_history(self.db,self.day,120,now=now+30)])
        self.assertNotIn('2002',[r[0] for r in plan_history(self.db,self.day,120,now=50000)])
        self.assertIn('2002',[r[0] for r in plan_history(self.db,'2026-10-19',120,now=50000)])

    def test_success_not_refetched_same_day(self):
        self.db.execute('UPDATE queue SET last_success=? WHERE code=?',(self.day,'2002'))
        self.assertNotIn('2002',[r[0] for r in plan_history(self.db,self.day,120,now=10000)])

    def test_larger_gap_prioritized_without_inventing_zero(self):
        for d in ('2026-10-14','2026-10-15',self.day):
            self.db.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?,?,?)',(d,'1802','b','k',1,0,1,1,'stamp'))
        self.assertEqual(plan_history(self.db,self.day,1,now=10000)[0][0],'2002')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM history WHERE code='2002'").fetchone()[0],0)

    def rank(self, code='2002', day=None):
        day=day or self.day
        self.db.execute('INSERT INTO stock_status VALUES(?,?,?,?,?)',(day,code,'ok',1,None))
        self.db.execute('INSERT INTO ranking VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (day,code,'sell',1,'b','k','真實分點',10,110,-100,10,'url','hash','stamp'))

    def test_raw_sell_evidence_is_exported_without_buy_streak(self):
        self.rank();feed=[]
        append_dated_raw_evidence(self.db,feed,{'2002':'中鋼','1802':'台玻'},self.day)
        self.assertEqual(len(feed),1)
        row=feed[0]['branches'][0]
        self.assertEqual(row['net_buy_lots'],-100)
        self.assertEqual(row['latest_day'],self.day)
        self.assertNotIn('consecutive_buy_days',row)
        self.assertFalse(row['formal_key_branch'])
        self.assertIn('有資料',row['display'])

    def test_missing_or_stale_ranking_is_not_relabelled_current(self):
        self.rank(day='2026-10-15');feed=[]
        append_dated_raw_evidence(self.db,feed,{'2002':'中鋼'},self.day)
        self.assertEqual(feed,[])

    def test_existing_analysis_not_overwritten(self):
        self.rank();feed=[{'code':'2002','branches':[{'formal_key_branch':True}]}]
        append_dated_raw_evidence(self.db,feed,{'2002':'中鋼'},self.day)
        self.assertTrue(feed[0]['branches'][0]['formal_key_branch'])

    def test_retry_budget_resets_on_real_day_even_if_source_still_friday(self):
        for i in range(3):
            record_attempt(self.db,'2026-10-19','2002','b','k',now=10000+i)
        self.assertNotIn('2002',[r[0] for r in plan_history(self.db,self.day,120,now=50000,attempt_day='2026-10-19')])
        self.assertIn('2002',[r[0] for r in plan_history(self.db,self.day,120,now=50000,attempt_day='2026-10-20')])

    def test_final_export_contains_real_raw_data_in_297_feed_shape(self):
        self.rank();self.db.commit()
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);target=data/'branches.sqlite'
            with sqlite3.connect(target) as dest:
                self.db.backup(dest)
            (data/'extra_universe.json').write_text(json.dumps({'stocks':{'2002':'中鋼'}}))
            with patch.object(final_result,'DATA',data),patch.object(final_result,'DB',target):
                final_result.main()
            feed=json.loads((data/'monitor_branch_feed.json').read_text())
            stock=next(r for r in feed['stocks'] if r['code']=='2002')
            self.assertEqual(feed['data_through'],self.day)
            self.assertEqual(stock['branches'][0]['net_buy_lots'],-100)
            self.assertEqual(stock['branches'][0]['branch_name'],'真實分點')


if __name__=='__main__':
    unittest.main()
