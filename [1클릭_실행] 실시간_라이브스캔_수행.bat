@echo off
chcp 65001 > nul
title [알상무 퀀트] 실시간 유튜브 라이브 스캔 및 차트 갱신
cd /d "%~dp0"

echo ======================================================================
echo    🚀 알상무 바이킹스 최신 라이브 스캔 + 3단계 게이트 퀀트 분석 시작...
echo ======================================================================
echo.
python al_sangmoo_daily_bot.py
if %ERRORLEVEL% NEQ 0 (
    "C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" al_sangmoo_daily_bot.py
)

echo.
echo ======================================================================
echo    ✅ 스캔 및 대시보드 데이터 갱신 완료!
echo ======================================================================
pause
