@echo off
title [R-Sangmoo Quant Terminal] Server Launcher

cd /d "%~dp0"

echo ======================================================================
echo   [R-Sangmoo Quant Terminal] Starting Backend Server...
echo   Dashboard URL: http://localhost:8000
echo ======================================================================
echo.
:: Launch browser after 2-second delay to ensure uvicorn binds to port 8000
start /b "" cmd /c "ping 127.0.0.1 -n 3 > nul & start http://localhost:8000"

echo Launching Server Process... (Keep this window open)
echo.

"C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" server.py
if %ERRORLEVEL% NEQ 0 (
    python server.py
)

pause
