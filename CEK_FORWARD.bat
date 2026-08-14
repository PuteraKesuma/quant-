@echo off
REM Pemantau forward test: kemajuan tiap sleeve vs harapan backtest,
REM plus cek kriteria HENTIKAN yang ditetapkan di _DOC/forward_test.md
cd /d C:\Quant
python _MONITOR\cek_forward.py
pause
