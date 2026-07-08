@echo off
setlocal
title Monthly Profit Governor
cd /d "%~dp0"
echo.
echo ============================================================
echo    MONTHLY PROFIT GOVERNOR  (bersyukur rule)
echo ============================================================
echo  Pauses new entries once month-to-date profit reaches 75 pct
echo  of the month forecast target. Open positions ride. Resets
echo  on the 1st. Writes _MONITOR\governor.json.
echo ============================================================
echo.
python -m pipeline.live.monthly_governor
echo.
echo  Governor stopped. Press any key to close.
pause >nul
endlocal
