@echo off
cd /d "%~dp0"
echo Installing... (first time only)
pip install -r requirements.txt
playwright install chromium
echo.
echo Done. Next time, just double-click the launcher bat.
pause
