import importlib.util
import unittest
import sqlite3
from pathlib import Path

spec=importlib.util.spec_from_file_location('relationship',Path(__file__).parents[1]/'price_relationship.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class RelationshipTests(unittest.TestCase):
    def test_quantity_uses_lots_to_shares_and_market_volume(self):
        self.assertEqual(m.quantity_shares(600,100,500,5000000)['net_share_pct'],10)
        self.assertEqual(m.quantity_shares(600,100,500,50000000)['net_share_pct'],1)
        self.assertIsNone(m.quantity_shares(600,100,500,0)['net_share_pct'])

    def test_quantity_correlation_requires_variation(self):
        rows=[{'net_share_pct':i,'return_pct':i*2} for i in (1,2,3)]
        self.assertAlmostEqual(m.quantity_correlation(rows)[1],1)
        self.assertIsNone(m.quantity_correlation(rows[:2])[1])
        self.assertIsNone(m.quantity_correlation([dict(r,net_share_pct=1) for r in rows])[1])

    def test_prior_price_state_uses_only_signal_day_or_earlier(self):
        sessions=['a','b','c','future']; prices={(d,'X'):p for d,p in zip(sessions,[100,102,101,200])}
        self.assertAlmostEqual(m.prior_price_state(prices,sessions,2,'X',2)['return_pct'],1)
        self.assertIsNone(m.prior_price_state(prices,sessions,1,'X',2))

    def test_sessions_not_calendar_days(self):
        sessions=['2026-09-24','2026-09-29','2026-09-30','2026-10-01','2026-10-02','2026-10-05']
        prices={(d,'6531'):100+i for i,d in enumerate(sessions)}
        result=m.price_change(prices,sessions,0,'6531',5)
        self.assertEqual(result['end_day'],'2026-10-05')
        self.assertAlmostEqual(result['return_pct'],5)

    def test_analysis_keeps_pending_and_nonoverlapping_outcomes(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript('CREATE TABLE official_prices(day TEXT,code TEXT,close REAL,volume REAL); CREATE TABLE ranking(day TEXT,code TEXT,side TEXT,rank INT,branch TEXT,broker TEXT,name TEXT,net REAL,buy REAL,sell REAL);')
            days=[f'2026-09-{i:02d}' for i in range(1,13)]
            db.executemany('INSERT INTO official_prices VALUES(?,?,?,?)',[(d,'X',100+i,5000000) for i,d in enumerate(days)])
            db.executemany('INSERT INTO ranking VALUES(?,?,?,?,?,?,?,?,?,?)',[(d,'X','buy',1,'B','R','branch',500,600,100) for d in days])
            detail,summary=m.analyze(db)
            self.assertEqual(len(detail),36)
            five=next(r for r in summary if r['horizon_sessions']==5)
            self.assertEqual(five['mature_daily_observations'],7)
            self.assertEqual(five['nonoverlapping_windows'],2)
            self.assertEqual(detail[0]['net_share_pct'],10)
            self.assertTrue(all(r['return_pct'] is None for r in detail if r['status']!='mature'))

    def test_missing_or_immature_is_not_zero_return(self):
        sessions=['2026-10-01','2026-10-02']
        self.assertIsNone(m.price_change({('2026-10-01','6531'):100},sessions,0,'6531',1))
        self.assertIsNone(m.price_change({(d,'6531'):100 for d in sessions},sessions,0,'6531',5))


if __name__=='__main__':unittest.main()
