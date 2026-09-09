@echo off
chcp 65001 >nul
cd /d C:\LobsterRadar
echo ==========================================
echo       Lobster Radar Environment Check
echo ==========================================
echo.
echo [1] Python:
venv\Scripts\python.exe --version
echo.
echo [2] Packages:
venv\Scripts\python.exe -c "import pandas,yfinance,requests; print('pandas',pandas.__version__); print('yfinance',yfinance.__version__); print('requests',requests.__version__)"
echo.
echo [3] Folder:
dir /b
echo.
pause
