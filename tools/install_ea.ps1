# ============================================================
#  install_ea.ps1 — salin EA + PRESET ke semua terminal MT5 yang terpasang.
#
#  Dipanggil oleh INSTALL_EA.bat, atau langsung:
#      powershell -ExecutionPolicy Bypass -File tools\install_ea.ps1
#  Idempotent: aman dijalankan berkali-kali.
#
#  PRESET IKUT DISALIN, DAN ITU BUKAN SEKADAR KENYAMANAN.
#  Default SemiMartiV10_Gated adalah InpGlobalSL_USD = 0 -- artinya TIDAK ADA
#  basket stop loss sama sekali, martingale tanpa batas bawah. InpDebug juga
#  default true, yang pernah menghasilkan log 768MB dalam hitungan menit.
#  Memasang EA ini tanpa memuat preset adalah cara tercepat kehilangan akun.
# ============================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # repo root (parent dari \tools)

$experts = @("SignalExecutor", "SemiMartiV10_Gated")
$presets = @("SemiMartiV10_GATED.set", "SemiMartiV10_SAFE.set")

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
    $dstEx = Join-Path $t.FullName "MQL5\Experts"
    $dstPr = Join-Path $t.FullName "MQL5\Presets"
    if (-not (Test-Path $dstPr)) { New-Item -ItemType Directory -Path $dstPr -Force | Out-Null }
    Write-Host ""
    Write-Host "terminal: $($t.Name)" -ForegroundColor Cyan

    foreach ($e in $experts) {
        $gotEx5 = $false
        foreach ($ext in @("ex5", "mq5")) {
            $src = Join-Path $root "mt5_ea\$e.$ext"
            if (Test-Path $src) {
                Copy-Item $src $dstEx -Force
                Write-Host "  [ OK ] $e.$ext" -ForegroundColor Green
                if ($ext -eq "ex5") { $gotEx5 = $true }
                $n++
            }
        }
        if (-not $gotEx5) {
            Write-Host "  [WARN] $e.ex5 tidak ada -- kompilasi dulu .mq5 di MetaEditor (F7)" -ForegroundColor Yellow
        }
    }

    foreach ($p in $presets) {
        $src = Join-Path $root "mt5_ea\presets\$p"
        if (Test-Path $src) {
            Copy-Item $src $dstPr -Force
            Write-Host "  [ OK ] preset $p" -ForegroundColor Green
            $n++
        } else {
            Write-Host "  [WARN] preset $p tidak ada di repo" -ForegroundColor Yellow
        }
    }
}

Write-Host ""
Write-Host "$n file tersalin." -ForegroundColor White
Write-Host ""
Write-Host "LANGKAH MANUAL BERIKUTNYA (GUI MT5, tidak bisa diotomatiskan):" -ForegroundColor White
Write-Host "  1. Navigator (Ctrl+N) > klik kanan 'Expert Advisors' > Refresh" -ForegroundColor Gray
Write-Host "  2. Chart XAUUSD M30 -> drag 'SignalExecutor'   (tanpa preset)" -ForegroundColor Gray
Write-Host "  3. Chart XAUUSD M5  -> drag 'SemiMartiV10_Gated'" -ForegroundColor Gray
Write-Host "     -> di dialog inputs tekan LOAD -> pilih SemiMartiV10_GATED.set" -ForegroundColor Yellow
Write-Host "     JANGAN lewati LOAD: default EA ini InpGlobalSL_USD=0 = martingale" -ForegroundColor Red
Write-Host "     tanpa batas kerugian." -ForegroundColor Red
Write-Host "  4. Nyalakan 'Algo Trading' di toolbar." -ForegroundColor Gray
Write-Host "  5. Verifikasi:  python tools\verify_system.py" -ForegroundColor Gray
Write-Host ""
