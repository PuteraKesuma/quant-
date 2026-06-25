@echo off
setlocal
rem Pasang auto-start: brain jalan sendiri tiap Windows login. Double-click saja.
cd /d "%~dp0"
echo.
echo ============================================================
echo   PASANG AUTO-START BRAIN (saat Windows login)
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_autostart.ps1"
echo.
pause
endlocal
