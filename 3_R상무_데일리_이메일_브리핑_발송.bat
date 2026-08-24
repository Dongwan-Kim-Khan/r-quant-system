@echo off
title [R-Sangmoo Quant Terminal] Daily Briefing & Email Dispatcher

cd /d "%~dp0"

echo ======================================================================
echo   [R-Sangmoo Quant Terminal] Executing Daily Quant & Email Dispatch...
echo ======================================================================
echo.

"C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" al_sangmoo_daily_bot.py
if %ERRORLEVEL% NEQ 0 (
    python al_sangmoo_daily_bot.py
)

echo.
echo ======================================================================
echo   Daily briefing generation and email dispatch completed.
echo ======================================================================
pause
