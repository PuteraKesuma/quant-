@echo off
REM ============================================================================
REM  SMC limit manager (magic 920643) - pending LIMIT di zona Order Block
REM  dengan EXPIRY, dikirim langsung lewat MT5 Python API (EA tidak disentuh).
REM
REM  Normalnya TIDAK perlu dijalankan manual: watchdog_shadow.ps1 sudah
REM  menghidupkannya otomatis dan menyalakan ulang kalau mati. File ini untuk
REM  menjalankan di jendela sendiri saat mau MELIHAT lognya langsung.
REM
REM  dry_run diatur di config.yaml -> slot smc_xau -> params.dry_run
REM    true  = hanya mencatat "DRY-RUN PLACE ..." tanpa mengirim order
REM    false = mengirim order pending SUNGGUHAN ke broker
REM
REM  Slot ini ~17-20 order per TAHUN. Berminggu-minggu tanpa order itu NORMAL,
REM  bukan tanda rusak. Lihat _DOC/smc_temuan.md sebelum mengubah apa pun.
REM ============================================================================
cd /d C:\Quant
echo.
echo   SMC limit manager - magic 920643 - XAUUSD H4
echo   Ctrl+C untuk berhenti. Menutup jendela ini TIDAK mematikan sleeve lain.
echo.
python -m pipeline.live.smc_limit_manager
pause
