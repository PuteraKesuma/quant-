@echo off
REM 3rd sleeve: US100 RSI-2 reversal (magic 920633). Runs once/day after the US100 daily bar closes.
set POLARS_SKIP_CPU_CHECK=1
echo ===== %DATE% %TIME% ===== >> "C:\Quant\_MONITOR\reversal.log"
"C:\Program Files\Python311\python.exe" "C:\Quant\research\reversal_sleeve.py" >> "C:\Quant\_MONITOR\reversal.log" 2>&1
