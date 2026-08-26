@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title [알상무 퀀트 터미널] Al-Sangmoo Quant Bot ^& Dashboard

cd /d "%~dp0"

echo ======================================================================
echo          알상무 17년 퀀트 프레임워크 터미널 (Global 60 Universe)
echo ======================================================================
echo  [1] 백엔드 포트(8000) 잔여 프로세스 점검 및 정리 중...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /f /pid %%a > nul 2>&1
)

echo  [2] 브라우저 대시보드 자동 연결 준비 중 (http://localhost:8000)...
start /b "" cmd /c "ping 127.0.0.1 -n 3 > nul & start http://localhost:8000"

echo  [3] KIS 증권사 API 게이트웨이 및 퀀트 엔진 기동 중...
echo ======================================================================
echo  * 대시보드 주소: http://localhost:8000
echo  * 터미널 종료 방법: 이 창에서 [Ctrl + C] 누르기 또는 창 닫기
echo ======================================================================
echo.

if exist "C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" (
    "C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" server.py
) else (
    python server.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [오류 발생] 서버가 예기치 않게 종료되었습니다.
    pause
)
