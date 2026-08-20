@echo off
chcp 65001 > nul
title [알상무 퀀트 터미널] 서버 종료
echo ======================================================================
echo    🛑 알상무 퀀트 서버(포트 8000)를 안전하게 종료합니다...
echo ======================================================================

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /f /pid %%a > nul 2>&1
)

echo.
echo ✅ 알상무 퀀트 서버가 정상적으로 종료되었습니다.
timeout /t 2 > nul
