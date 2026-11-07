"""Publish and download-verify an immutable baseline of the raw branch evidence."""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'data'
DB = ROOT / 'branches.sqlite'
MANIFEST = ROOT / 'branch_archive_baseline.json'
TABLES = ('ranking', 'history', 'queue', 'stock_status', 'official_prices')


def inspect(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('SQLite integrity check failed')
        return {'sha256': digest.hexdigest(), 'bytes': path.stat().st_size,
                'counts': {t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in TABLES},
                'ranges': {t: db.execute(f'SELECT MIN(day),MAX(day) FROM {t}').fetchone()
                           for t in ('ranking', 'history', 'official_prices')}}


def archive():
    if MANIFEST.exists():
        return
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        meta = inspect(DB)
        tag = f'branch-baseline-{meta["sha256"][:16]}'
        name = 'branches.sqlite.gz'
        asset = tmp / name
        with DB.open('rb') as src, asset.open('wb') as raw, gzip.GzipFile(
                filename='', fileobj=raw, mode='wb', mtime=0) as out:
            shutil.copyfileobj(src, out)
        repo = os.environ['GITHUB_REPOSITORY']
        view = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo], capture_output=True)
        if view.returncode:
            subprocess.run(['gh', 'release', 'create', tag, str(asset), '--repo', repo,
                            '--target', os.environ['GITHUB_SHA'], '--title', '分點歷史基準封存',
                            '--notes', f'完整原始 SQLite。解壓後 SHA-256: {meta["sha256"]}'], check=True)
        check_dir = tmp / 'verify'
        check_dir.mkdir()
        subprocess.run(['gh', 'release', 'download', tag, '--repo', repo, '--pattern', name,
                        '--dir', str(check_dir)], check=True)
        check_db = check_dir / 'branches.sqlite'
        with gzip.open(check_dir / name, 'rb') as src, check_db.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        if inspect(check_db) != meta:
            raise ValueError('Release readback does not match source')
        meta.update(tag=tag, asset=name, compressed_bytes=asset.stat().st_size,
                    source_commit=os.environ['GITHUB_SHA'], download_verified=True)
        MANIFEST.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Verified baseline archive: {tag}')


if __name__ == '__main__':
    archive()
