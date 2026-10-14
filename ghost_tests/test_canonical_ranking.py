import tempfile
import unittest
from pathlib import Path
from datetime import datetime,date
from unittest.mock import patch
from zoneinfo import ZoneInfo
import ghost_candidate_pool as pool

class CanonicalRankingTest(unittest.TestCase):
    def test_desktop_projection_uses_exact_candidate_score_and_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            tracking={'x':{'code':'3706','signal_id':'2026-10-12|3706','support':100,
                           'composite_score':90,'tracking_day':2}}
            rows=[{'code':'3706','score':81.62}]
            now=datetime(2026,10,14,22,tzinfo=ZoneInfo('Asia/Taipei'))
            with patch.object(pool,'ROOT',p),patch.object(pool,'latest_price_snapshot',return_value=('2026-10-14',{'3706':'2026-10-14'},{'3706':102})),patch.object(pool,'cloud_daily_score',return_value=(81.62,{'macd_source':'daily_MACD_proxy'})):
                scores=pool.build_daily_scores(tracking,rows,'2026-10-14','2026-10-14',date(2026,10,15),now)
            self.assertEqual(scores['stocks']['3706']['score'],rows[0]['score'])
            self.assertEqual(scores['stocks']['3706']['candidate_rank'],1)
            self.assertEqual(scores['stocks']['3706']['signal_id'],'2026-10-12|3706')
            self.assertEqual(scores['macd_basis'],'daily')
            self.assertTrue((p/'ghost_daily_scores.json').exists())

    def test_stale_price_does_not_publish_trade_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pool,'ROOT',Path(tmp)),patch.object(pool,'latest_price_snapshot',return_value=('2026-10-13',{'3706':'2026-10-13'},{'3706':102})):
                scores=pool.build_daily_scores({'x':{'code':'3706','support':100}},[],'2026-10-13','2026-10-14',date(2026,10,15),datetime(2026,10,14,tzinfo=ZoneInfo('Asia/Taipei')))
                self.assertIsNone(scores['stocks']['3706']['score'])
                self.assertFalse(scores['stocks']['3706']['eligible'])

if __name__=='__main__': unittest.main()
