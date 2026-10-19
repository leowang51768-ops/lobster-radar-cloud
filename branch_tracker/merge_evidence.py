#!/usr/bin/env python3
"""Merge a preserved collector DB into the remote DB without dropping rows."""
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TABLES = {'ranking': 'collected_at', 'history': 'collected_at',
          'history_attempts': 'last_at', 'queue': 'last_attempt', 'stock_status': None, 'official_prices': None}


def merge(source, destination):
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Preserved evidence database is corrupt')
        from branch_tracker.backfill import ensure_attempts
        ensure_attempts(src)
        ensure_attempts(dst)
        for table, stamp in TABLES.items():
            info = dst.execute(f'PRAGMA table_info({table})').fetchall()
            columns = [r[1] for r in info]
            keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            updates = [c for c in columns if c not in keys]
            cols = ','.join(columns)
            assignments = ','.join(f'{c}=excluded.{c}' for c in updates)
            if table == 'history_attempts':
                assignments = 'attempts=MAX(history_attempts.attempts,excluded.attempts),last_at=MAX(history_attempts.last_at,excluded.last_at),error=CASE WHEN excluded.last_at>=history_attempts.last_at THEN excluded.error ELSE history_attempts.error END'
            if stamp:
                condition = f"COALESCE(excluded.{stamp},'') > COALESCE({table}.{stamp},'')"
                if table == 'queue':
                    # A failed refresh must not erase a successful backfill marker.
                    assignments = assignments.replace('first_seen=excluded.first_seen',
                        'first_seen=MIN(queue.first_seen,excluded.first_seen)')
                    assignments = assignments.replace('last_seen=excluded.last_seen',
                        'last_seen=MAX(queue.last_seen,excluded.last_seen)')
                    assignments = assignments.replace('last_success=excluded.last_success',
                        "last_success=NULLIF(MAX(COALESCE(queue.last_success,''),COALESCE(excluded.last_success,'')),'')")
                    condition += " OR COALESCE(excluded.last_success,'') > COALESCE(queue.last_success,'') OR excluded.last_seen > queue.last_seen"
            elif table == 'stock_status':
                condition = "excluded.status='ok' AND stock_status.status!='ok'"
            else:
                # Remote official prices are authoritative; only fill missing keys.
                condition = '0'
            sql = f'INSERT INTO {table} ({cols}) VALUES ({",".join("?" for _ in columns)}) ON CONFLICT({",".join(keys)}) DO UPDATE SET {assignments} WHERE {condition}'
            dst.executemany(sql, src.execute(f'SELECT {cols} FROM {table}'))
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Merged database failed integrity check')


if __name__ == '__main__':
    merge(*sys.argv[1:])
