@echo off
rem ============================================================================
rem  File lama. Dulu memanggil tools\install_autostart.ps1 yang HANYA memasang
rem  brain - versi itu tidak pernah terpasang dan sudah kalah lengkap: dia tidak
rem  menjaga xau_executor, orb_stop_manager, MT5, maupun watchdog itu sendiri.
rem
rem  Sengaja diarahkan ulang, bukan dihapus, supaya tidak ada dua mekanisme
rem  auto-start yang bertabrakan kalau ada yang terlanjur hafal nama file ini.
rem ============================================================================
echo.
echo  Installer ini sudah digantikan PASANG_AUTOSTART.bat
echo  ^(lebih lengkap: watchdog + executor + orbmgr + MT5 + auto-login^).
echo.
echo  Membuka yang baru...
echo.
call "%~dp0PASANG_AUTOSTART.bat"
