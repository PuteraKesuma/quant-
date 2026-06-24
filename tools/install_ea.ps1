# Copy SignalExecutor (.ex5 + .mq5) into every MT5 terminal's MQL5\Experts folder.
# Run via INSTALL_EA.bat (double-click). MT5 must have been opened at least once.
$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot          # repo root (parent of \tools)
$ea_mq5 = Join-Path $root "mt5_ea\SignalExecutor.mq5"
$ea_ex5 = Join-Path $root "mt5_ea\SignalExecutor.ex5"

$base = Join-Path $env:APPDATA "MetaQuotes\Terminal"
if (-not (Test-Path $base)) {
    Write-Host "[FAIL] MT5 belum pernah dijalankan (folder Terminal tidak ada)." -ForegroundColor Red
    Write-Host "       Buka MetaTrader 5 dulu, login, lalu jalankan ini lagi."
    exit 1
}

$terminals = Get-ChildItem $base -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName "MQL5\Experts") }
if (-not $terminals) {
    Write-Host "[FAIL] Tidak menemukan folder MQL5\Experts di terminal mana pun." -ForegroundColor Red
    Write-Host "       Pastikan MT5 (bukan MT4) sudah dibuka minimal sekali."
    exit 1
}

$n = 0
foreach ($t in $terminals) {
    $dest = Join-Path $t.FullName "MQL5\Experts"
    Copy-Item $ea_mq5 $dest -Force
    if (Test-Path $ea_ex5) { Copy-Item $ea_ex5 $dest -Force }
    Write-Host "[ OK ] EA disalin ke: $dest" -ForegroundColor Green
    $n++
}
Write-Host ""
Write-Host "Selesai ($n terminal). Langkah berikutnya di MT5:" -ForegroundColor Cyan
Write-Host "  1. Navigator (Ctrl+N) > klik kanan 'Expert Advisors' > Refresh"
Write-Host "  2. Drag 'SignalExecutor' ke chart XAUUSD dan ke chart US100"
Write-Host "  3. Isi inputs sesuai panduan, centang 'Allow Algo Trading', OK"
