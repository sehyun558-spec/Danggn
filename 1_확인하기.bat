@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if %errorlevel%==0 (
    py check.py
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [!] 파이썬이 설치되어 있지 않아요. 설명서의 "파이썬 설치하기"를 먼저 해 주세요.
    ) else (
        python check.py
    )
)
echo.
pause
