import importlib.util
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('collector',Path(__file__).parents[1]/'collect.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

RANK='''<html><script>ignore()</script>愛普*(6531) 單位：張 最後更新日：2026/10/01
<table><tr><td>買超券商</td><td>買進</td><td>賣出</td><td>買超</td><td>佔成交比重</td><td>賣超券商</td><td>買進</td><td>賣出</td><td>賣超</td><td>佔成交比重</td></tr>
<tr><td><a href="/z/zc/zco/zco0/zco0.djhtm?a=6531&b=1480&BHID=1480">美商高盛</a></td><td>157</td><td>40</td><td>117</td><td>6.29%</td><td><a href="/z/zc/zco/zco0/zco0.djhtm?a=6531&b=1470&BHID=1470">台灣摩根士丹利</a></td><td>58</td><td>309</td><td>251</td><td>13.5%</td></tr></table></html>'''
HISTORY='''愛普*(6531)個股 單一券商歷史明細
<select name="sel_Broker"><option value="6010">其他</option><option value="1480" selected>美商高盛</option></select>
<select name="sel_BrokerBranch"><option value="1480" selected>美商高盛</option></select>
<table><tr><td>2026/10/01</td><td>157</td><td>40</td><td>197</td><td>117</td></tr><tr><td>2026/09/30</td><td>49</td><td>243</td><td>291</td><td>-194</td></tr></table>'''


class CollectorTests(unittest.TestCase):
    def test_rank_units_and_sell_sign(self):
        rows=m.parse_rank(RANK,'6531','2026-10-01')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1][6],-251)
        self.assertEqual(rows[0][7],6.29)

    def test_stale_wrong_stock_and_malformed_rejected(self):
        for html,code in [(RANK.replace('2026/10/01','2026/09/30'),'6531'),(RANK,'2330'),(RANK.replace('<td>117</td>','<td>999</td>'),'6531')]:
            with self.assertRaises(ValueError):m.parse_rank(html,code,'2026-10-01')

    def test_history_rounding_and_identity(self):
        rows=m.parse_history(HISTORY,'6531','1480','1480','2026-10-01')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1][-1],-194)
        with self.assertRaises(ValueError):m.parse_history(HISTORY,'6531','1470','1480','2026-10-01')

    def test_static_javascript_selector_page_binds_server_identity(self):
        import re
        html=re.sub(r'<select.*?</select>','',HISTORY,flags=re.S)
        nav="<script>self.location = '/z/zc/zco/zco0/zco0.djhtm?a=6531&BHID=1480&b=1480&C='+i;</script>"
        self.assertEqual(len(m.parse_history(html+nav,'6531','1480','1480','2026-10-01')),2)
        for bad in (html,html+nav.replace('b=1480','b=1470')):
            with self.assertRaises(ValueError):m.parse_history(bad,'6531','1480','1480','2026-10-01')

    def test_duplicates_and_future_history_rejected(self):
        for html in (HISTORY.replace('2026/09/30','2026/10/01'),HISTORY.replace('2026/10/01','2026/10/02')):
            with self.assertRaises(ValueError):m.parse_history(html,'6531','1480','1480','2026-10-01')

    def test_history_upsert_preserves_daily_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=m.open_db(Path(tmp)/'data.sqlite')
            row=('2026-10-01','6531','1480','1480',157,40,197,117,'stamp')
            for _ in range(2):db.execute('INSERT OR REPLACE INTO history VALUES(?,?,?,?,?,?,?,?,?)',row)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0],1)
            db.close()


if __name__=='__main__':unittest.main()
