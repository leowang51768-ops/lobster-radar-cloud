import unittest
from datetime import date
from ghost_lifecycle import reconcile, active_picks

def calendar(d):
    return d.weekday() < 5 and d.isoformat() != '2026-10-09'

def feed(day='2026-10-07'):
    return {'stocks':[{'code':'1234', 'name':'test', 'data_through':day,
                     'branches':[{'recent_buy_days_5':2, 'last_recent_buy_day':day}]}]}

def signal(day='2026-10-08', status='tracking'):
    return {'s':{'code':'1234','signal_date':day,'support':100,'breakout_close':102,
                 'stop':98,'score':90,'status':status}}

class LifecycleTest(unittest.TestCase):
    def step(self, state=None, radar=None, branch=None, events=None, ref='2026-10-08', completed='2026-10-07'):
        return reconcile(state or {}, radar or {}, branch or {}, events or {}, ref, completed, calendar)

    def test_repeat_feed_does_not_reset_and_holiday_does_not_count(self):
        s = self.step(branch=feed())
        s = self.step(s, branch=feed(), ref='2026-10-12', completed='2026-10-08')
        self.assertEqual(s['stocks']['1234']['first_seen_date'], '2026-10-07')
        self.assertEqual(s['stocks']['1234']['a_age'], 3)

    def test_confirmation_day_is_d0_and_positive_status_remains(self):
        s = self.step(branch=feed())
        s = self.step(s, radar=signal(status='confirmed'), branch=feed())
        self.assertEqual(s['stocks']['1234']['stage'], 'D')
        self.assertEqual(active_picks(s)[0]['tracking_day'], 0)
        s = self.step(s, radar=signal(status='true_breakout'), ref='2026-10-12', completed='2026-10-08')
        self.assertEqual(active_picks(s)[0]['tracking_day'], 1)
        self.assertTrue(s['archive'])  # Original A evidence remains.

    def test_tenth_a_session_expires_and_same_old_signal_does_not_restart(self):
        s = self.step(branch=feed('2026-10-01'), ref='2026-10-14', completed='2026-10-13')
        self.assertEqual(s['stocks']['1234']['stage'], 'A1')
        s = self.step(s, branch=feed('2026-10-01'), ref='2026-10-15', completed='2026-10-15')
        self.assertEqual(s['stocks']['1234']['stage'], 'POOL')
        s = self.step(s, branch=feed('2026-10-01'), ref='2026-10-16', completed='2026-10-15')
        self.assertEqual(s['stocks']['1234']['stage'], 'POOL')
        s = self.step(s, branch=feed('2026-10-16'), ref='2026-10-16', completed='2026-10-15')
        self.assertEqual(s['stocks']['1234']['stage'], 'A1')

    def test_d10_kept_intraday_and_retired_at_close(self):
        s = self.step(radar=signal('2026-10-01'), ref='2026-10-16', completed='2026-10-15')
        self.assertEqual(active_picks(s)[0]['tracking_day'], 10)
        s = self.step(s, radar=signal('2026-10-01'), ref='2026-10-16', completed='2026-10-16')
        self.assertFalse(active_picks(s))
        self.assertEqual(s['stocks']['1234']['exit_reason'], 'D10_complete')

    def test_invalid_signal_returns_to_pool_and_history_survives(self):
        s = self.step(radar=signal())
        s = self.step(s, radar=signal(status='invalid'), completed='2026-10-08')
        self.assertEqual(s['stocks']['1234']['stage'], 'POOL')
        self.assertTrue(s['archive'])
        s = self.step(s, radar=signal(), branch=feed())
        self.assertFalse(active_picks(s))

    def test_legacy_expiry_does_not_end_at_d9(self):
        s = self.step(radar=signal('2026-10-01','expired'), ref='2026-10-15', completed='2026-10-15')
        self.assertEqual(active_picks(s)[0]['tracking_day'], 9)
        self.assertEqual(active_picks(s)[0]['status'], 'tracking')

    def test_estimate_and_unverified_event_cannot_confirm(self):
        r=signal(); r['s']['estimate']=True
        s = self.step(radar=r, branch=feed(), events={'e':{'kind':'pattern_confirmed','pick':signal()['s']}})
        self.assertEqual(s['stocks']['1234']['stage'], 'A1')
        self.assertFalse(active_picks(s))

if __name__ == '__main__':
    unittest.main()
