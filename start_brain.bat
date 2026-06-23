@echo off
title ORB Signal Server (BRAIN) - keep this window open
cd /d "%~dp0"
echo ============================================================
echo  Starting ORB Signal Server (the BRAIN).
echo  Keep this window OPEN while trading. Close it = brain off.
echo  Make sure MT5 is open and Algo Trading is ON.
echo  (Tip: pakai START_TRADING.bat untuk versi + pre-flight check.)
echo ============================================================
python -m pipeline.live.run_server
echo.
echo Server stopped. Press any key to close.
pause >nul
