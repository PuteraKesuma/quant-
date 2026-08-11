@echo off
setlocal
title PASANG AUTO-START - jalankan SEKALI
cd /d "%~dp0"

echo.
echo ============================================================
echo    PASANG AUTO-START TRADING - jalankan SEKALI saja
echo ============================================================
echo.
echo  Ada TIGA lapis perlindungan, masing-masing menutup kegagalan
echo  yang berbeda:
echo.
echo    Lapis 1  Task Scheduler tiap 5 menit  -^> watchdog mati /
echo             terminal ditutup
echo    Lapis 2  watchdog tiap 30 detik       -^> brain / executor /
echo             orbmgr / MT5 crash
echo    Lapis 3  auto-login Windows           -^> VPS reboot
echo.
echo  Skrip ini memasang lapis 1 dan 2 otomatis. Lapis 3 butuh
echo  password Windows kamu, jadi kamu yang ketik sendiri nanti.
echo.
pause

echo.
echo ------------------------------------------------------------
echo  LAPIS 1 + 2
echo ------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_MONITOR\install_watchdog_task.ps1"
if errorlevel 1 (
    echo.
    echo  [FAIL] Pemasangan task gagal. Jalankan .bat ini sebagai Administrator.
    echo.
    pause
    exit /b 1
)

echo.
echo ------------------------------------------------------------
echo  LAPIS 3 - AUTO-LOGIN WINDOWS
echo ------------------------------------------------------------
echo.
echo  KENAPA PERLU: MT5 dan MT5 Python API wajib berada di sesi
echo  desktop. Kalau VPS reboot dan tidak ada yang login lewat RDP,
echo  tidak ada sesi sama sekali - trading berhenti sampai ada
echo  manusia yang login. Auto-login menutup lubang itu.
echo.

if not exist "%~dp0tools\Autologon64.exe" (
    echo  [FAIL] Autologon64.exe belum ada di folder tools\
    echo         Unduh dari https://download.sysinternals.com/files/AutoLogon.zip
    echo         lalu taruh Autologon64.exe di %~dp0tools\
    echo.
    pause
    exit /b 1
)

echo  Membuka Autologon Sysinternals. Isi begini:
echo.
echo      Username : Administrator
echo      Domain   : %COMPUTERNAME%
echo      Password : password Windows kamu
echo.
echo  lalu klik Enable.
echo.
echo  Password disimpan TERENKRIPSI di LSA secret Windows - tidak
echo  ditulis plaintext ke registry, dan tidak pernah lewat chat.
echo.
pause
start "" "%~dp0tools\Autologon64.exe"

echo.
echo  Setelah kamu klik Enable, tekan tombol apa saja untuk verifikasi.
pause >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'; if($w.AutoAdminLogon -eq '1'){ Write-Host ('  [ OK ] Auto-login AKTIF untuk ' + $w.DefaultUserName); if($w.DefaultPassword){Write-Host '  [WARN] password plaintext ada di registry - harusnya tidak ada'}else{Write-Host '  [ OK ] tidak ada password plaintext di registry'} } else { Write-Host '  [FAIL] Auto-login BELUM aktif - buka lagi Autologon64.exe dan klik Enable' }"

echo.
echo ------------------------------------------------------------
call "%~dp0CEK_TRADING.bat" ringkas
echo ------------------------------------------------------------
echo.
echo  Selesai. Mulai trading: AUTO_TRADING_ON.bat
echo.
pause
endlocal
