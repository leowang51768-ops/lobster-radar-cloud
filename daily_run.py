from pathlib import Path
from datetime import datetime
import subprocess
import shutil
import sqlite3
import sys

BASE = Path(r"C:\LobsterRadar")
PY = BASE / "venv" / "Scripts" / "python.exe"
DB = BASE / "lobster_radar.db"

def run_script(name):
    print(f"Running {name}...")
    subprocess.run(
        [str(PY), str(BASE / name)],
        cwd=str(BASE),
        check=True
    )

run_script("lobster_update.py")
run_script("retry_failed.py")

con = sqlite3.connect(DB)
try:
    failed = con.execute(
        "SELECT COUNT(*) FROM failed_list"
    ).fetchone()[0]
finally:
    con.close()

cloud = Path.home() / "\u6211\u7684\u96f2\u7aef\u786c\u789f" / "LobsterRadar"
cloud.mkdir(parents=True, exist_ok=True)

shutil.copy2(DB, cloud / "lobster_radar.db")
shutil.copy2(BASE / "progress.json", cloud / "progress.json")

daily_backup = cloud / f"lobster_radar_{datetime.now():%Y%m%d}.db"
shutil.copy2(DB, daily_backup)

print(f"Daily update finished. failed_list={failed}")
print(f"Cloud backup: {cloud}")

if failed != 0:
    sys.exit(2)
