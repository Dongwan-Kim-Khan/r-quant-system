@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title [R-Quant System - Production Engine Terminal]

cd /d "%~dp0"

echo ======================================================================
echo   R-Quant System Terminal - C-2 Production Engine
echo   [34/33/33 Sizing + Cash Proxy / Dual Stop -7.0%% EOD, -10.0%% Emerg]
echo ======================================================================
echo  [1] Checking and terminating stale processes on port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /f /pid %%a > nul 2>&1
)

REM Unique boot token forces browser navigation without cache collision.
set BOOT_ID=%RANDOM%%RANDOM%

echo  [2] Waiting for server on port 8000 and launching browser...
start /b "" cmd /c "for /L %%i in (1,1,45) do (netstat -ano | findstr :8000 | findstr LISTENING >nul && (start "" http://localhost:8000/?boot=%BOOT_ID% & exit /b) & ping 127.0.0.1 -n 2 >nul)"

echo  [3] Starting FastAPI backend server...
echo ======================================================================
echo  * Web Terminal:  http://localhost:8000  (frontend/index.html)
echo  * Stop-Loss:     Dual Stop: EOD -7.0%% / Emergency -10.0%% / Trail +18%% (ATR 3.0)
echo  * Slot Sizing:   Bull 34%% / 33%% / 33%% (Bear 25%% / 25%%)
echo  * Termination:   Press [Ctrl + C] or close this console window
echo ======================================================================
echo.

REM Python path fallback: prefer local 3.13, then 3.12, then PATH python
set "PYEXE="
if exist "C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" (
    set "PYEXE=C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe"
) else if exist "C:\Users\kdw58\AppData\Local\Programs\Python\Python312\python.exe" (
    set "PYEXE=C:\Users\kdw58\AppData\Local\Programs\Python\Python312\python.exe"
) else if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
) else if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
)

if defined PYEXE (
    echo  [Python] %PYEXE%
    "%PYEXE%" server.py
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo  [Python] PATH python
        python server.py
    ) else (
        echo.
        echo [ERROR] Python executable not found.
        echo         Please install Python 3.11+ and ensure it is registered in PATH.
        pause
        exit /b 1
    )
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Server exited unexpectedly.
    pause
)
