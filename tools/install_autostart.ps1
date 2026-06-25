# ============================================================
#  install_autostart.ps1 — brain nyala sendiri setiap Windows login
#  Membuat shortcut START_TRADING.bat di folder Startup user.
#  Jalankan langsung:  powershell -ExecutionPolicy Bypass -File tools\install_autostart.ps1
#  Atau via INSTALL_AUTOSTART.bat (double-click).
#  Idempotent: aman dijalankan berkali-kali (shortcut ditimpa, bukan duplikat).
# ============================================================
$ErrorActionPreference = "Stop"

# Folder repo = parent dari folder tools\ tempat skrip ini berada.
$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $root "START_TRADING.bat"
if (-not (Test-Path $bat)) {
    Write-Host "[FAIL] START_TRADING.bat tidak ditemukan di $root" -ForegroundColor Red
    exit 1
}

$startup = [Environment]::GetFolderPath("Startup")          # shell:startup user ini
$lnk     = Join-Path $startup "ORB Trading Brain.lnk"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $bat
$sc.WorkingDirectory = $root          # supaya cd /d %~dp0 & path relatif benar
$sc.WindowStyle      = 1              # normal window (biar kelihatan kalau jalan)
$sc.Description      = "Auto-start ORB trading brain saat Windows login"
$sc.Save()

Write-Host "[ OK ] Auto-start terpasang:" -ForegroundColor Green
Write-Host "       $lnk" -ForegroundColor Gray
Write-Host "       -> $bat" -ForegroundColor Gray
Write-Host ""
Write-Host "Brain akan otomatis jalan setiap kali user ini login ke Windows." -ForegroundColor White
Write-Host "Untuk benar-benar 'tinggal nyala' setelah VPS reboot, aktifkan juga" -ForegroundColor White
Write-Host "auto-login Windows (jalankan: netplwiz) dan MT5 'save account / load last profile'." -ForegroundColor White
Write-Host ""
Write-Host "Mau lepas auto-start? Hapus file shortcut di atas (atau jalankan:" -ForegroundColor DarkGray
Write-Host "  Remove-Item `"$lnk`" )." -ForegroundColor DarkGray
