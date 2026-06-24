@echo off
setlocal
title INSTALL EA - salin SignalExecutor ke MetaTrader 5
cd /d "%~dp0"
echo ============================================================
echo   PASANG EA ke MetaTrader 5 (otomatis)
echo   Pastikan MT5 sudah dibuka + login minimal sekali.
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_ea.ps1"
echo.
pause
endlocal
