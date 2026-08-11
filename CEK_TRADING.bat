@echo off
setlocal
title CEK STATUS TRADING
cd /d "%~dp0"

if /i "%~1"=="ringkas" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_MONITOR\status.ps1" -Ringkas
    exit /b 0
)

echo.
echo ============================================================
echo                 STATUS SISTEM TRADING
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_MONITOR\status.ps1"
echo ============================================================
echo.
echo  Kalau ada baris MATI / MACET: watchdog biasanya memperbaiki
echo  sendiri dalam 5 menit. Kalau lewat 10 menit masih MATI, baru
echo  jalankan AUTO_TRADING_ON.bat.
echo.
echo  Baris TERKUNCI cuma bisa kamu yang benerin: nyalakan tombol
echo  "Algo Trading" di MT5. Watchdog sengaja tidak restart untuk
echo  ini - restart tidak akan menyalakan tombolnya.
echo.
pause
endlocal
