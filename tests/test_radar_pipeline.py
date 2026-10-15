import os,json,tempfile,sqlite3,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from contextlib import ExitStack
import combined_line_digest as d

class RadarPipelineTest(unittest.TestCase):
    def run_main(self,root,notify='false',token='',send=None):
        today=datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
        stack=ExitStack()
        stack.enter_context(patch('scan_trade_date.resolve_trade_date',return_value=today))
        stack.enter_context(patch.object(d,'BASE',root));stack.enter_context(patch.object(d,'SEND_STATE',root/'sent.json'))
        stack.enter_context(patch.dict(os.environ,{'LINE_NOTIFY_ENABLED':notify,'LINE_CHANNEL_ACCESS_TOKEN':token}))
        for name,value in {'market_date':today,'collect':[],'score_candidates':[],'choose':[],'populate_historical_zones':None,'write_diagnostics':[],'format_message':'daily result','update_breakout_tracking':([],[],[])}.items():stack.enter_context(patch.object(d,name,return_value=value))
        stack.enter_context(patch('chip_candidate_tracking.record_candidates'));stack.enter_context(patch('chip_candidate_tracking.update_outcomes'))
        sender=stack.enter_context(patch.object(d,'send_line_text',side_effect=send))
        return stack,sender,today
    def test_data_only_run_still_builds_current_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stack,sender,today=self.run_main(root)
            with stack:self.assertEqual(d.main(),0);sender.assert_not_called()
            self.assertEqual(json.loads((root/'radar_sync_status.json').read_text())['trade_date'],today)
            self.assertFalse((root/'sent.json').exists())
    def test_due_notify_without_token_fails_after_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stack,_,today=self.run_main(root,'true','')
            with stack,self.assertRaisesRegex(RuntimeError,'缺少LINE'):d.main()
            self.assertTrue((root/'radar_sync_status.json').exists());self.assertFalse((root/'sent.json').exists())
    def test_daily_send_is_idempotent_and_zero_pick_result_is_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stack,sender,today=self.run_main(root,'true','TEST-NOT-REAL')
            with stack:d.main();d.main();self.assertEqual(sender.call_count,1)
            self.assertTrue(json.loads((root/'sent.json').read_text())[today]['daily_result_sent'])
    def test_send_error_does_not_claim_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stack,_,_=self.run_main(root,'true','TEST-NOT-REAL',RuntimeError('mock delivery failed'))
            with stack,self.assertRaisesRegex(RuntimeError,'mock delivery'):d.main()
            self.assertTrue((root/'radar_sync_status.json').exists());self.assertFalse((root/'sent.json').exists())
    def test_tracking_snapshot_has_trade_date_even_zero_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);today=datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
            with sqlite3.connect(root/'lobster_tw_6m_prices.sqlite') as con:
                con.execute('CREATE TABLE prices (date TEXT,stock_id TEXT,low REAL,close REAL,volume REAL)')
                con.execute('INSERT INTO prices VALUES (?,?,?,?,?)',(today,'1234',99,100,1000))
            with patch.object(d,'BASE',root),patch.object(d,'TRACKING_PATH',root/'tracking.json'),patch.object(d,'WATCHLIST_PATH',root/'watch.json'):
                d.update_breakout_tracking(today,[])
            x=json.loads((root/'watch.json').read_text());self.assertEqual(x['data_date'],today);self.assertEqual(x['as_of_date'],today);self.assertEqual(x['stocks'],[])
    def test_workflow_build_is_not_gated_by_notification_schedule(self):
        text=(Path(__file__).parents[1]/'.github/workflows/main.yml').read_text()
        section=text[text.index('id: radar_pipeline'):text.index('- name: Update daily LINE pick performance')]
        self.assertIn("if: ${{ steps.market_calendar.outputs.closed != 'true' }}",section)
        self.assertIn("github.event_name == 'workflow_dispatch'",section)
        self.assertIn("steps.radar_pipeline.outcome == 'failure'",text)

    def test_next_calendar_day_can_publish_latest_completed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);day='2026-10-15'
            with sqlite3.connect(root/'lobster_tw_6m_prices.sqlite') as con:
                con.execute('CREATE TABLE prices (date TEXT,stock_id TEXT,low REAL,close REAL,volume REAL)')
                con.execute('INSERT INTO prices VALUES (?,?,?,?,?)',(day,'1234',99,100,1000))
            with patch.object(d,'BASE',root),patch.object(d,'TRACKING_PATH',root/'tracking.json'),patch.object(d,'WATCHLIST_PATH',root/'watch.json'),patch('scan_trade_date.resolve_trade_date',return_value=day):
                d.update_breakout_tracking(day,[])
            self.assertEqual(json.loads((root/'watch.json').read_text())['data_date'],day)

    def test_retry_retains_same_day_scanner_signal_but_not_older_signal(self):
        import csv
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'formal_recommendations.csv').write_text('date,code,signal_route\n2026-10-15,1503,破底翻\n2026-10-14,1513,破底翻\n2026-10-15,1301,破底翻\n')
            fields=['date','code','name','pattern','status','close','trigger_level','b_point']
            with (root/'candidate_status.csv').open('w') as f:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                for code in ['1503','1513','1301']:w.writerow(dict(date='2026-10-15',code=code,name=code,pattern='破底翻',status='同日重複訊號',close=100,trigger_level=99,b_point=95))
            for name in ['vcp_candidates.csv','n_bottom_watch.csv']:(root/name).write_text('date,code\n')
            with patch.object(d,'BASE',root):out=d.collect('2026-10-15')
            self.assertEqual([r['code'] for r in out],['1503']);self.assertTrue(out[0]['within'])
