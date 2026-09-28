@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title [알상무 퀀트 터미널 v3.0 - C-2 Production Engine (34/33/33 + QQQ Proxy + Dual Stop -7%% EOD / -10%% Emerg)]

cd /d "%~dp0"

echo ======================================================================
echo   알상무 퀀트 터미널 v3.0 - C-2 Production Engine
echo   [34/33/33 Equal Sizing + QQQ Proxy / Dual Stop -7.0%% EOD, -10.0%% Emerg]
echo ======================================================================
echo  [1] 백엔드 포트(8000) 잔여 프로세스 점검 및 정리 중...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /f /pid %%a > nul 2>&1
)

REM Unique boot token forces Chrome to navigate instead of focusing a stale localhost tab.
set BOOT_ID=%RANDOM%%RANDOM%

echo  [2] 서버가 8000 포트를 열면 최신 대시보드를 엽니다 (캐시 우회)...
start /b "" cmd /c "for /L %%i in (1,1,45) do (netstat -ano | findstr :8000 | findstr LISTENING >nul && (start "" http://localhost:8000/?boot=%BOOT_ID% & exit /b) & ping 127.0.0.1 -n 2 >nul)"

echo  [3] KIS 증권사 API 게이트웨이 및 C-2 퀀트 엔진 기동 중...
echo ======================================================================
echo  * 대시보드 주소: http://localhost:8000  (frontend/index.html)
echo  * 엔진 스펙:    Dual Stop: EOD -7.0%% / Emerg -10.0%% / Trail +18%% (ATR 3.0)
echo  * 슬롯 비중:    강세장 34%% / 33%% / 33%% (약세장 25%% / 25%%)
echo  * 기존 탭이 열려 있어도 ?boot= 주소로 새 화면이 로드됩니다
echo  * 터미널 종료 방법: 이 창에서 [Ctrl + C] 누르기 또는 창 닫기
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
        echo [오류] Python 실행 파일을 찾을 수 없습니다.
        echo        Python 3.12/3.13 설치 후 PATH 등록 또는 위 경로를 확인하세요.
        pause
        exit /b 1
    )
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [오류 발생] 서버가 예기치 않게 종료되었습니다.
    pause
)
