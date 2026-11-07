"""Restore the three preserved Actions snapshots after the November publish failures."""
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from merge_evidence import merge

DATA = Path(__file__).resolve().parent / 'data'
ARTIFACTS = {'2026-11-04': 13362580662, '2026-11-05': 13453098478, '2026-11-06': 13538002304}
MARKER = 'november_2026_recovery.json'
LIMIT = 100 * 1024 * 1024


def coverage(path, day):
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT COUNT(DISTINCT r.code),COUNT(*) FROM ranking r JOIN stock_status s "
            "ON s.day=r.day AND s.code=r.code WHERE r.day=? AND s.status='ok'", (day,)
        ).fetchone()


def restore(archives, data=DATA):
    marker = data / MARKER
    if marker.exists():
        return
    target = data / 'branches.sqlite'
    with tempfile.TemporaryDirectory(dir=data) as work:
        root = Path(work)
        merged = root / 'merged.sqlite'
        shutil.copyfile(target, merged)
        results = {}
        for day, archive in zip(ARTIFACTS, archives, strict=True):
            original = root / 'original.sqlite'
            with zipfile.ZipFile(archive) as z:
                with z.open('branches.sqlite') as src, original.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
                audit = f'audits/{day}.md'
                if audit in z.namelist():
                    (root / f'{day}.md').write_bytes(z.read(audit))
            original_count, original_rows = coverage(original, day)
            if original_count != 251 or original_rows == 0:
                raise ValueError(f'No verified ranking rows in artifact: {day}')
            merge(original, merged)
            covered, rows = coverage(merged, day)
            if covered < original_count or rows < original_rows:
                raise ValueError(f'Incomplete merge: {day}')
            results[day] = {'artifact_id': ARTIFACTS[day], 'covered_stocks': covered, 'ranking_rows': rows}
            if not (root / f'{day}.md').exists():
                (root / f'{day}.md').write_text(
                    f'# 分點備份復原稽核｜{day}\n\n'
                    f'Actions 備份 {ARTIFACTS[day]}：已核對 {covered}／253 檔、{rows:,} 筆排行。'
                    '未取得的股票保留缺漏。\n', encoding='utf-8'
                )
        with sqlite3.connect(merged) as db:
            db.execute('VACUUM')
            day = next(reversed(ARTIFACTS))
            coverage_rows = db.execute(
                'SELECT code,status,rows,error FROM stock_status WHERE day=? ORDER BY code', (day,)
            ).fetchall()
            status = {
                'date': day, 'mode': 'actions_artifact_recovery', 'status': 'partial',
                'expected_stocks': 253, 'fixed_stocks': 163, 'supplemental_stocks': 90,
                'covered_stocks': results[day]['covered_stocks'], 'coverage_complete': False,
                'ranking_coverage_complete': False, 'source_blocked': False,
                'missing_stocks': [r[0] for r in coverage_rows if r[1] != 'ok'],
                'errors': [{'code': r[0], 'error': r[3]} for r in coverage_rows if r[1] != 'ok'],
                'complete_collection_days': db.execute(
                    "SELECT COUNT(*) FROM (SELECT day FROM stock_status WHERE status='ok' "
                    "GROUP BY day HAVING COUNT(*)=253)"
                ).fetchone()[0],
            }
            status['counts'] = {t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                                for t in ('ranking', 'history', 'queue')}
            status['observed_collection_days'] = db.execute(
                "SELECT COUNT(DISTINCT day) FROM stock_status WHERE status='ok'"
            ).fetchone()[0]
        if merged.stat().st_size >= LIMIT:
            raise RuntimeError('Merged SQLite reaches GitHub blob size limit')
        merged.replace(target)
        status_path = data / 'status.json'
        prior = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
        if prior.get('date', '') <= status['date']:
            status['database_bytes'] = target.stat().st_size
            status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
            with (data / 'coverage.csv').open('w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['date', 'code', 'status', 'rows', 'error'])
                writer.writerows((day, *row) for row in coverage_rows)
        audits = data / 'audits'
        audits.mkdir(exist_ok=True)
        for day in ARTIFACTS:
            report = root / f'{day}.md'
            if report.exists() and not (audits / report.name).exists():
                report.replace(audits / report.name)
        marker.write_text(json.dumps({'restored': results, 'unrecovered_dates': ['2026-11-03']},
                                     ensure_ascii=False, indent=2), encoding='utf-8')
        print(marker.read_text(encoding='utf-8'))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        restore([Path(p) for p in sys.argv[1:]])
    elif not (DATA / MARKER).exists():
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for artifact_id in ARTIFACTS.values():
                path = Path(tmp) / f'{artifact_id}.zip'
                with path.open('wb') as out:
                    subprocess.run(['gh', 'api', f'repos/{os.environ["GITHUB_REPOSITORY"]}'
                                    f'/actions/artifacts/{artifact_id}/zip'], stdout=out, check=True)
                paths.append(path)
            restore(paths)
