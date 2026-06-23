@echo off
setlocal
title ORB Trading Brain - JANGAN DITUTUP saat trading
rem %~dp0 = folder tempat file .bat ini berada (portable: jalan di mana saja / VPS)
cd /d "%~dp0"

echo.
echo ============================================================
echo            ORB TRADING BRAIN - LAUNCHER
echo ============================================================
echo  Langkah sebelum mulai:
echo    1. Pastikan MetaTrader 5 sudah DIBUKA dan LOGIN.
echo    2. Tombol "Algo Trading" di MT5 dalam keadaan ON (hijau).
echo    3. EA SignalExecutor terpasang di chart (NAS100 + XAUUSD).
echo ============================================================
echo.
echo  Menjalankan pre-flight check...
echo.

python -m pipeline.live.preflight
if errorlevel 1 (
    echo.
    echo  ^>^>^> ADA MASALAH di atas. Server TIDAK dijalankan.
    echo      Perbaiki dulu yang bertanda [FAIL], lalu jalankan lagi.
    echo      ^(Mesin/VPS baru? jalankan SETUP.bat dulu.^)
    echo.
    pause
    exit /b 1
)

echo.
echo  Pre-flight OK. Menjalankan BRAIN (signal server)...
echo  Biarkan jendela ini TERBUKA selama trading. Tutup = brain mati.
echo ------------------------------------------------------------
echo.
python -m pipeline.live.run_server

echo.
echo ------------------------------------------------------------
echo  Server berhenti. Tekan tombol apa saja untuk menutup.
pause >nul
endlocal
