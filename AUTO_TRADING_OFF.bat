@echo off
setlocal
title MATIKAN AUTO TRADING
cd /d "%~dp0"

echo.
echo ============================================================
echo             MATIKAN AUTO TRADING
echo ============================================================
echo.
echo  Yang DIMATIKAN : watchdog, brain, xau_executor, orb_stop_manager
echo                   dan auto-start-nya ^(task tidak akan hidup lagi
echo                   sendiri sampai kamu jalankan AUTO_TRADING_ON^).
echo.
echo  Yang TIDAK disentuh : MetaTrader 5.
echo    Posisi yang sedang terbuka TETAP dijaga SL/TP di sisi broker,
echo    jadi mematikan sistem ini tidak membuat posisi jadi telanjang.
echo    Tapi entry BARU dan pembalikan arah berhenti total.
echo.

set /p JWB="  Yakin mau matikan? ketik YA lalu Enter: "
if /i not "%JWB%"=="YA" (
    echo.
    echo  Dibatalkan. Tidak ada yang diubah.
    echo.
    pause
    exit /b 0
)

echo.
echo  Mematikan auto-start...
schtasks /end    /tn "Quant Watchdog" >nul 2>&1
schtasks /change /tn "Quant Watchdog" /disable >nul 2>&1

echo  Menghentikan proses...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'pipeline\.live\.(run_server|xau_executor|orb_stop_manager)' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('  dihentikan pid ' + $_.ProcessId) }; Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -match '-File .*watchdog_shadow' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('  watchdog dihentikan pid ' + $_.ProcessId) }"

echo.
echo  Sistem MATI. MT5 dibiarkan terbuka.
echo  Hidupkan lagi: AUTO_TRADING_ON.bat
echo.
pause
endlocal
