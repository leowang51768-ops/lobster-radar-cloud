# Branch collection repair, 2026-10-19

This change is confined to the GitHub collector and its exported feed. Ghost 297
and its trading rules are unchanged.

- Failed history requests may retry after 15 minutes, up to three attempts per
  stock/branch/broker per Taipei calendar day. Successful current-session fetches
  are not repeated. HTTP 403/429 and source verification blocks still stop a run.
- Queue priority favors never-fetched / oldest refreshed pairs and, within that
  tier, the number of missing actual official stock sessions in the latest 60.
  Requests remain limited to 120 per scheduled run.
- A source history page supplies at most 60 rows. Older gaps and missing rows
  remain unverified; no zero-trade rows are synthesized and six-month completeness
  is not claimed. Existing full-window coverage reporting is retained.
- For stocks absent from the persistent key-branch feed, a validated same-date
  ranking can supply one factual reference branch (largest absolute daily net).
  It contains actual buy/sell/net lots, source date and branch identity. It does
  not invent consecutive days, historical grades, or a formal key-branch signal.
- Collector retry state survives database evidence merging. Existing ranking,
  history and permanent stock membership are retained.

Scheduled collection remains 17:30, 20:40 and 21:30 Asia/Taipei on weekdays.
The separate official-price workflow remains enabled; retired radar / LINE
notification workflows are not reintroduced.
