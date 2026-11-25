@echo off
chcp 65001 >nul
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup')+'\danggn.lnk')"
echo 자동 실행을 껐어요. 이제 컴퓨터를 켜도 저절로 시작되지 않아요.
pause
