# =====================================================================================
#  HEARTBEAT LIVE - jendela pantau yang boleh dibiarkan terbuka terus.
#
#  CATATAN ENCODING: pure ASCII, alasannya sama dengan watchdog_shadow.ps1.
#
#  PENTING - INI CUMA PENONTON, BUKAN MESINNYA.
#  Jendela ini tidak menjalankan apa pun. Menutupnya TIDAK menghentikan trading.
#  Itu justru inti perbaikan 2026-08-11: dulu watchdog sendiri yang jalan di jendela
#  terminal, jadi terminal ditutup = seluruh rantai mati diam-diam. Sekarang mesinnya
#  ada di Task Scheduler (tak terlihat, tak bisa tertutup) dan jendela ini hanya
#  membacanya. Tutup kapan saja, buka kapan saja.
#
#  Jalankan: double-click LIHAT_HEARTBEAT.bat
# =====================================================================================
$ErrorActionPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "HEARTBEAT TRADING - aman ditutup, ini cuma penonton"

$Py     = "C:\Program Files\Python311\python.exe"
$MonDir = "C:\Quant\_MONITOR"
$Jeda   = 10

function Warna($teks, $baik) {
    if ($baik) { Write-Host $teks -ForegroundColor Green } else { Write-Host $teks -ForegroundColor Red }
}

$probeTiap   = 6          # probe MT5 tiap 6 putaran (~1 menit), jangan tiap 10 detik
$putaran     = 0
$mt5Teks     = "(memeriksa...)"
$mt5Baik     = $true

while ($true) {
    $putaran++
    Clear-Host
    Write-Host ""
    Write-Host "  ============================================================"
    Write-Host "   HEARTBEAT SISTEM TRADING        $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')) UTC"
    Write-Host "  ============================================================"
    Write-Host "   Jendela ini AMAN DITUTUP. Mesinnya di Task Scheduler."
    Write-Host ""

    # --- watchdog ---
    $alive = Join-Path $MonDir "watchdog_alive.txt"
    if (Test-Path $alive) {
        $u = ((Get-Date) - (Get-Item $alive).LastWriteTime).TotalSeconds
        Warna ("   WATCHDOG       {0}   detak {1:N0} detik lalu" -f $(if ($u -le 120) { "HIDUP " } else { "MACET " }), $u) ($u -le 120)
    } else {
        Warna "   WATCHDOG       MATI    belum pernah berdetak" $false
    }

    # --- brain ---
    $slots = ""
    $brainOk = $false
    try {
        $j = (Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 4).Content | ConvertFrom-Json
        $slots = ($j.strategies | ForEach-Object { $_.name }) -join ", "
        $brainOk = $true
        Warna ("   BRAIN          HIDUP   uptime {0:N0} menit" -f ($j.uptime_seconds / 60)) $true
    } catch {
        Warna "   BRAIN          MATI    port 8000 tidak menjawab" $false
    }

    # --- eksekutor ---
    function ProcUp($p) {
        return [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                      Where-Object { $_.CommandLine -match $p })
    }
    $xe = ProcUp "pipeline\.live\.xau_executor"
    $om = ProcUp "pipeline\.live\.orb_stop_manager"
    Warna ("   XAU_EXECUTOR   {0}" -f $(if ($xe) { "HIDUP " } else { "MATI   <- nol order XAU terkirim" })) $xe
    Warna ("   ORB_MANAGER    {0}" -f $(if ($om) { "HIDUP " } else { "MATI   <- sleeve terbesar berhenti" })) $om

    # --- MT5 (jarang, karena mahal) ---
    if ($putaran % $probeTiap -eq 1) {
        $pout = & $Py (Join-Path $MonDir "mt5_probe.py") 2>&1
        $pc = $LASTEXITCODE
        $mt5Baik = ($pc -eq 0)
        $mt5Teks = switch ($pc) {
            0       { "SIAP    " + ($pout -replace '^SEHAT\s+-\s+', '') }
            2       { "TERKUNCI Algo Trading MATI - nyalakan tombolnya di MT5" }
            default { "MASALAH " + ($pout | Select-Object -First 1) }
        }
    }
    Warna ("   MT5            {0}" -f $mt5Teks) $mt5Baik

    Write-Host ""
    Write-Host "   slot aktif : $slots"

    # --- sinyal terkini ---
    if ($brainOk) {
        Write-Host ""
        Write-Host "   SINYAL SEKARANG"
        try {
            $s = (Invoke-WebRequest -Uri "http://127.0.0.1:8000/signals?symbol=XAUUSD" -UseBasicParsing -TimeoutSec 20).Content | ConvertFrom-Json
            foreach ($g in $s.signals) {
                Write-Host ("     {0,-16} {1,-5} sl={2,-9} tp={3}" -f $g.strategy, $g.action, $g.sl, $g.tp)
            }
        } catch { Write-Host "     (brain masih menghitung bar...)" }
    }

    # --- jurnal ---
    Write-Host ""
    Write-Host "   JURNAL TERAKHIR"
    Get-Content (Join-Path $MonDir "jurnal.md") -Tail 6 | ForEach-Object {
        Write-Host ("     " + ($_ -replace '\*\*', '' -replace '^- ', ''))
    }

    Write-Host ""
    Write-Host "  ------------------------------------------------------------"
    Write-Host "   segar tiap $Jeda detik   |   Ctrl+C untuk keluar (aman)"
    Start-Sleep -Seconds $Jeda
}
