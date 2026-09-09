@echo off
chcp 65001 >nul
cd /d C:\LobsterRadar
echo.
echo ==========================================
echo       Lobster Radar Database Updater
echo ==========================================
echo.
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] 找不到 venv\Scripts\python.exe
    echo 請確認 Python 虛擬環境已建立。
    pause
    exit /b 1
)
venv\Scripts\python.exe lobster_update.py
echo.
echo 執行完成。詳細紀錄請看 C:\LobsterRadar\logs
pause
