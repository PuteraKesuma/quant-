@echo off
setlocal
title HIDUPKAN AUTO TRADING
cd /d "%~dp0"

echo.
echo ============================================================
echo             HIDUPKAN AUTO TRADING
echo ============================================================
echo.
echo  Yang dijalankan: watchdog lewat Task Scheduler, lalu watchdog
echo  sendiri yang menghidupkan brain, xau_executor, orb_stop_manager
echo  dan MT5 kalau ada yang mati.
echo.
echo  JENDELA INI BOLEH DITUTUP. Trading TIDAK terikat padanya - itu
echo  justru bug yang bikin 2026-08-10 kosong tanpa trade semalaman.
echo.

schtasks /query /tn "Quant Watchdog" >nul 2>&1
if errorlevel 1 (
    echo  [FAIL] Task "Quant Watchdog" belum terpasang.
    echo         Jalankan PASANG_AUTOSTART.bat dulu ^(sekali saja^).
    echo.
    pause
    exit /b 1
)

echo  Mengaktifkan task...
schtasks /change /tn "Quant Watchdog" /enable >nul
echo  Menjalankan watchdog...
schtasks /run /tn "Quant Watchdog" >nul

echo  Menunggu rantai naik ^(sampai 90 detik^)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; for($i=0;$i -lt 30;$i++){ try{ if((Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 4).StatusCode -eq 200){$ok=$true;break} }catch{}; Start-Sleep -Seconds 3 }; if($ok){Write-Host '  [ OK ] brain menjawab /health'}else{Write-Host '  [WARN] brain belum menjawab - watchdog masih mencoba, cek lagi 2 menit lagi'}"

echo.
echo ------------------------------------------------------------
call "%~dp0CEK_TRADING.bat" ringkas
echo ------------------------------------------------------------
echo.
echo  Selesai. Tutup jendela ini kapan saja.
echo.
pause
endlocal
