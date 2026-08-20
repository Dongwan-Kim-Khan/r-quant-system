@echo off
title [Al-Sangmoo Quant Terminal] Run Live Scanner

cd /d "%~dp0"

echo ======================================================================
echo   Running YouTube Live Stream Scanner & 3-Gate Quant Evaluation...
echo ======================================================================
echo.

"C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" al_sangmoo_daily_bot.py
if %ERRORLEVEL% NEQ 0 (
    python al_sangmoo_daily_bot.py
)

echo.
echo ======================================================================
echo   [Done] Scan complete and dashboard updated!
echo ======================================================================
pause