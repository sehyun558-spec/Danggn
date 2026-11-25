@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 컴퓨터를 켤 때 당근 매물 모아보기가 저절로 켜지도록 설정할게요...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\danggn.lnk'); $s.TargetPath='%~dp02_시작하기.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (
    echo [!] 설정하지 못했어요. 이 창을 캡처해서 Claude에게 보내 주세요.
) else (
    echo 완료! 이제 컴퓨터를 켜면 자동으로 시작돼요.
    echo 끄고 싶으면 4_자동실행_끄기.bat 을 실행하세요.
)
pause
