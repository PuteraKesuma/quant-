@echo off
REM Daily 3-sleeve book: refresh signals then reconcile live positions (JPY live, gold/NAS shadow).
set POLARS_SKIP_CPU_CHECK=1
echo ===== %DATE% %TIME% ===== >> "C:\Quant\_MONITOR\daily_book.log"
"C:\Program Files\Python311\python.exe" "C:\Quant\research\daily_sleeve.py"   >> "C:\Quant\_MONITOR\daily_book.log" 2>&1
"C:\Program Files\Python311\python.exe" "C:\Quant\research\daily_executor.py" >> "C:\Quant\_MONITOR\daily_book.log" 2>&1
"C:\Program Files\Python311\python.exe" "C:\Quant\research\reversal_sleeve.py" >> "C:\Quant\_MONITOR\daily_book.log" 2>&1
