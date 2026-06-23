@echo off
setlocal
title Cek Chart Vision - screenshot terbaru
cd /d "%~dp0"

echo.
echo ============================================================
echo   CEK CHART VISION
echo   Mengambil screenshot chart terbaru (butuh MT5 terbuka),
echo   menyimpannya ke _DOC\vision\, lalu membukanya otomatis.
echo ============================================================
echo.

python -m pipeline.vision.snapshot

echo.
echo  Selesai. Tekan tombol apa saja untuk menutup.
pause >nul
endlocal
