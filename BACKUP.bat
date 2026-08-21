@echo off
setlocal
title BACKUP - cadangan lengkap untuk dibawa keluar VPS
cd /d "%~dp0"
echo ============================================================
echo   BUAT CADANGAN LENGKAP
echo   Hasilnya folder bertanggal di Desktop. PINDAHKAN keluar VPS.
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_backup.ps1"
echo.
pause
endlocal
