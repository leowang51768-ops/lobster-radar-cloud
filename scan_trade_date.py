"""Choose the latest eligible radar session, never an unfinished current day."""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo('Asia/Taipei')
# Existing workflow market calendar, shared without changing closure rules.
HOLIDAYS = frozenset({
    '2026-01-01', '2026-02-12', '2026-02-13', '2026-02-16', '2026-02-17',
    '2026-02-18', '2026-02-19', '2026-02-20', '2026-02-27',
    '2026-04-03', '2026-04-06', '2026-05-01', '2026-06-19',
    '2026-09-25', '2026-09-28', '2026-10-09', '2026-10-26', '2026-12-25',
})


def is_trade_day(day):
    return day.weekday() < 5 and day.isoformat() not in HOLIDAYS


def resolve_trade_date(now=None, explicit=None):
    if explicit:
        return date.fromisoformat(explicit).isoformat()
    now = now or datetime.now(TAIPEI)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    local = now.astimezone(TAIPEI)
    # Official daily radar starts at 19:00. A delayed overnight/morning run
    # still belongs to the previous eligible session, even after midnight.
    day = local.date()
    if local.time() < time(19):
        day -= timedelta(days=1)
    if day.year != 2026:
        raise ValueError('Market calendar needs verification for year ' + str(day.year))
    while not is_trade_day(day):
        day -= timedelta(days=1)
    return day.isoformat()


if __name__ == '__main__':
    print(resolve_trade_date(explicit=os.environ.get('MANUAL_TRADE_DATE')))
