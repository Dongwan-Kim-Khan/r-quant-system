@echo off
title [Al-Sangmoo Quant Terminal] Server Launcher

cd /d "%~dp0"

echo ======================================================================
echo   [Al-Sangmoo Quant Terminal] Starting Backend Server...
echo   Dashboard URL: http://localhost:8000
echo ======================================================================
echo.
echo [1/2] Opening Web Browser...
start "" "http://localhost:8000"

echo [2/2] Launching Server Process... (Keep this window open)
echo.

"C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" server.py
if %ERRORLEVEL% NEQ 0 (
    python server.py
)

pause