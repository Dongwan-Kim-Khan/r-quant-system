@echo off
title [R-Sangmoo Quant Terminal] Stop Server

echo ======================================================================
echo   Stopping server on port 8000...
echo ======================================================================

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /f /pid %%a > nul 2>&1
)

echo.
echo [Done] Server stopped successfully.
ping 127.0.0.1 -n 2 > nul
