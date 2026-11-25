@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
title 당근 매물 모아보기 - 끄려면 이 창을 닫으세요
where py >nul 2>nul
if %errorlevel%==0 (
    py app.py
) else (
    python app.py
)
pause
