@echo off
setlocal
title LIQUIDITY MANAGER - pasang & update limit order asli (magic 920625)
cd /d "%~dp0"

echo.
echo ============================================================
echo        LIQUIDITY MANAGER  (real pending LIMIT orders)
echo ============================================================
echo  Proses ini pasang & terus re-price ORDER LIMIT ASLI di band
echo  Supertrend (magic 920625) lewat MT5 langsung. EA tidak dipakai.
echo    - UPTREND  -> BUY_LIMIT di support band
echo    - DOWNTREND-> SELL_LIMIT di resistance band
echo    - 1 posisi; cancel pending saat trend flip / posisi terbuka
echo  Butuh: MT5 terbuka + login. dry_run diatur di config.yaml.
echo ============================================================
echo.
python -m pipeline.live.liquidity_manager

echo.
echo ------------------------------------------------------------
echo  Manager berhenti. Tekan tombol apa saja untuk menutup.
pause >nul
endlocal
