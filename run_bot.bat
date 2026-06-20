@echo off
cd /d "%~dp0"
echo Starting Expense Tracker Bot...
python bot.py
echo.
echo Bot stopped. Press any key to close this window.
pause >nul
