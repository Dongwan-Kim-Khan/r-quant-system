@echo off
chcp 65001 > nul
title 알상무 퀀트 서버 백그라운드 가동
cd /d "D:\코딩\Playground\al_sangmoo_project"
echo ========================================================
echo   🏛️ 알상무 퀀트 실전 포트폴리오 백엔드 서버 가동 중...
echo   👉 대시보드 주소: http://localhost:8000
echo ========================================================
"C:\Users\kdw58\AppData\Local\Programs\Python\Python313\python.exe" server.py
