@echo off
REM Audit keamanan sistem: proses, watchdog, autologon, MT5, risiko, cadangan,
REM kredit API, disk. Menandai temuan sebagai OK / PERHATIAN / BAHAYA.
cd /d C:\Quant
python _MONITOR\audit_sistem.py
echo.
echo ================== AKTIVITAS HARI INI ==================
python _MONITOR\cek_hari_ini.py
pause
