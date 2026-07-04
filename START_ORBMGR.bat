@echo off
setlocal
title ORB STOP MANAGER - pasang STOP order asli di batas opening range (magic 920617)
cd /d "%~dp0"

echo.
echo ============================================================
echo        ORB STOP MANAGER  (real pending STOP orders)
echo ============================================================
echo  Proses ini pasang ORDER STOP ASLI di batas NY opening range
echo  (magic 920617) lewat MT5 langsung, biar entry FILL DI LEVEL
echo  bukan di close M1 (dulu EA slip +4.75..+16.30 pt/trade).
echo    - trend NAIK  -> BUY_STOP di range high
echo    - trend TURUN -> SELL_STOP di range low
echo    - 1 trade/sesi; breakeven +0.5R; tutup di 20:00 UTC
echo  Logika SAMA persis slot orb30_nas (trade tak berubah). EA tak dipakai.
echo  Butuh: MT5 terbuka + login. dry_run diatur di config.yaml.
echo ============================================================
echo.
python -m pipeline.live.orb_stop_manager

echo.
echo ------------------------------------------------------------
echo  Manager berhenti. Tekan tombol apa saja untuk menutup.
pause >nul
endlocal
