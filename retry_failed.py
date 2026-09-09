import sqlite3
import time
import lobster_update as lr

DB = r"C:\LobsterRadar\lobster_radar.db"
SLEEP_BETWEEN = 2
BACKOFF = (5, 15, 30)

def load_failed():
    con = sqlite3.connect(DB)
    try:
        return con.execute("""
            SELECT f.symbol, s.yahoo_symbol
            FROM failed_list f
            JOIN stocks s ON s.symbol = f.symbol
            WHERE f.stage = 'prices' AND s.active = 1
            ORDER BY f.symbol
        """).fetchall()
    finally:
        con.close()

rows = load_failed()
total = len(rows)
ok = 0
still_failed = 0

print(f"Retrying {total} failed symbols...")

for i, (symbol, yahoo_symbol) in enumerate(rows, 1):
    done = False
    last_error = None

    for attempt in range(1, 5):
        try:
            lr.upsert_prices_and_technicals(symbol, yahoo_symbol)
            ok += 1
            done = True
            print(f"[{i}/{total}] OK {symbol} {yahoo_symbol}")
            break
        except Exception as e:
            last_error = e
            if attempt <= len(BACKOFF):
                wait = BACKOFF[attempt - 1]
                print(f"[{i}/{total}] retry {attempt} {symbol}: {e} ; wait {wait}s")
                time.sleep(wait)

    if not done:
        still_failed += 1
        print(f"[{i}/{total}] FAIL {symbol}: {last_error}")

    time.sleep(SLEEP_BETWEEN)

con = sqlite3.connect(DB)
try:
    remaining = con.execute("SELECT COUNT(*) FROM failed_list").fetchone()[0]
finally:
    con.close()

print(f"Done. recovered={ok}, still_failed={still_failed}, failed_list={remaining}")
