@echo off
chcp 65001 > nul
title [알상무 퀀트 터미널] 로컬 서버 가동기
cd /d "%~dp0"

echo ======================================================================
echo    🏛️  알상무 17년 퀀트 & 거시 스탠스 터미널 서버 가동 중...
echo    👉  대시보드 주소: http://localhost:8000
echo    👉  동일 Wi-Fi 모바일 접속: http://[내PC_IP]:8000
echo ======================================================================
echo.
echo [1/2] 웹 브라우저 자동 실행 중...
start "" http://localhost:8000

echo [2/2] FastAPI 백엔드 서버 시작...
python server.py
if %ERRORLEVEL% NEQ 0 (
    "C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" server.py
)

pause
