@echo off
cd /d C:\LobsterRadar
echo ==========================================
echo Lobster Radar Database Updater
echo ==========================================
if not exist venv\Scripts\python.exe (
  echo ERROR: venv\Scripts\python.exe not found.
  pause
  exit /b 1
)
venv\Scripts\python.exe lobster_update.py
echo.
echo Done. Logs: C:\LobsterRadar\logs
pause
