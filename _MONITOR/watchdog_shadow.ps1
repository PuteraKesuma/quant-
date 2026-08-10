# =====================================================================================
#  WATCHDOG - penjaga seluruh rantai eksekusi trading
#
#  CATATAN ENCODING (penting, jangan diubah):
#  Seluruh file ini sengaja ditulis TANPA karakter non-ASCII. PowerShell -File membaca
#  skrip memakai codepage sistem kalau tidak ada BOM; pada 2026-08-10 sebuah em-dash
#  di komentar berubah jadi mojibake dan MEMECAH sintaks -> watchdog gagal start diam-diam
#  sementara semua proses lain terlihat normal. Pakai tanda hubung biasa saja.
#
#  Yang dijaga (empat-empatnya wajib hidup, kalau satu mati ada sleeve yang berhenti):
#    1. BRAIN            pipeline.live.run_server      - sumber sinyal
#    2. XAU_EXECUTOR     pipeline.live.xau_executor    - PENGGANTI EA MQL5; mengirim order
#                        untuk slot XAU (zrev/eterna/eterna_asli). Kalau mati, brain tetap
#                        menghitung dan /health tetap hijau TAPI tidak ada order terkirim.
#                        Ini kegagalan paling berbahaya karena tidak terlihat di mana pun.
#    3. ORB_STOP_MANAGER pipeline.live.orb_stop_manager - sleeve ORB (bobot terbesar 43%,
#                        lot 0.03). Juga tidak butuh EA.
#    4. METATRADER 5     sumber bar M1; kalau mati semuanya buta.
#
#  SENGAJA TIDAK dijalankan: advisor (membakar kredit API Anthropic), liquidity_manager,
#  monthly_governor. Jangan pakai watchdog_brain.ps1 yang lama - dia menghidupkan semua itu.
#
#  Jalankan:
#    powershell -ExecutionPolicy Bypass -File C:\Quant\_MONITOR\watchdog_shadow.ps1
# =====================================================================================
$ErrorActionPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "WATCHDOG TRADING - JANGAN DITUTUP"

$Py        = "C:\Program Files\Python311\python.exe"
$Root      = "C:\Quant"
$MonDir    = "C:\Quant\_MONITOR"
$Jurnal    = Join-Path $MonDir "jurnal.md"
$HealthLog = Join-Path $MonDir "health_log.jsonl"
$SigLog    = Join-Path $MonDir "eterna_shadow.jsonl"
$Mt5Exe    = "C:\Program Files\MetaTrader 5\terminal64.exe"
$HealthUrl = "http://127.0.0.1:8000/health"
$SignalUrl = "http://127.0.0.1:8000/signals?symbol=XAUUSD"

$Interval           = 30
$FailsToRestart     = 3
$RestartCooldownMin = 3
$ProcCooldownMin    = 3
$Mt5CooldownMin     = 5
$HeartbeatMin       = 30
$SignalPollMin      = 15

function NowUtc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }

function J($tag, $msg) {
    $line = "- **{0} UTC** {1} [wd] {2}" -f (NowUtc), $tag, $msg
    Add-Content -Path $Jurnal -Value $line -Encoding utf8
    Write-Host $line
}

function ProcUp($pattern) {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match $pattern }
    return [bool]$p
}

function StartPy($module, $outName, $errName) {
    Start-Process -FilePath $Py -ArgumentList "-m", $module `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $MonDir $outName) `
        -RedirectStandardError  (Join-Path $MonDir $errName)
}

function Mt5Up { return [bool](Get-Process terminal64 -ErrorAction SilentlyContinue) }

# --- state ---
$fails          = 0
$lastRestart    = (Get-Date).AddHours(-1)
$lastXauTry     = (Get-Date).AddHours(-1)
$lastOrbTry     = (Get-Date).AddHours(-1)
$lastMt5Try     = (Get-Date).AddHours(-1)
$lastHeartbeat  = (Get-Date).AddHours(-1)
$lastSignalPoll = (Get-Date).AddHours(-1)
$lastAction     = ""

J "ON " "Watchdog START. Menjaga: BRAIN + XAU_EXECUTOR + ORB_STOP_MANAGER + MT5."

while ($true) {
    $now = Get-Date

    # ---------- 1. MetaTrader 5 ----------
    if (-not (Mt5Up)) {
        if (($now - $lastMt5Try).TotalMinutes -ge $Mt5CooldownMin) {
            J "ERR" "MetaTrader 5 MATI - semua sleeve kehilangan sumber bar. Menjalankan ulang..."
            Start-Process -FilePath $Mt5Exe
            $lastMt5Try = $now
        }
    }

    # ---------- 2. brain sehat? ----------
    $healthy = $false
    $slots = ""
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) {
            $healthy = $true
            $j = $r.Content | ConvertFrom-Json
            $slots = ($j.strategies | ForEach-Object { $_.name }) -join ","
            $rec = @{ ts = (NowUtc); up = $j.uptime_seconds; slots = $slots } | ConvertTo-Json -Compress
            Add-Content -Path $HealthLog -Value $rec -Encoding utf8
        }
    } catch { }

    if ($healthy) {
        if ($fails -ge $FailsToRestart) { J "OK " "Brain PULIH - health OK lagi." }
        $fails = 0
    } else {
        $fails++
        if ($fails -eq 1) { J "WRN" "Health gagal (1). Menunggu konfirmasi..." }
        if ($fails -ge $FailsToRestart) {
            if (($now - $lastRestart).TotalMinutes -ge $RestartCooldownMin) {
                if (ProcUp "pipeline\.live\.run_server") {
                    J "CLN" "Proses brain ada tapi health mati - menghentikan proses lama."
                    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                        Where-Object { $_.CommandLine -match "pipeline\.live\.run_server" } |
                        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
                    Start-Sleep -Seconds 3
                }
                J "RST" ("Brain DOWN ({0} cek gagal) - menjalankan ulang..." -f $fails)
                StartPy "pipeline.live.run_server" "brain_out.log" "brain_err.log"
                $lastRestart = $now
            }
        }
    }

    # ---------- 3. xau_executor (pengganti EA) ----------
    if (-not (ProcUp "pipeline\.live\.xau_executor")) {
        if (($now - $lastXauTry).TotalMinutes -ge $ProcCooldownMin) {
            J "ERR" "xau_executor MATI - slot XAU tidak mengirim order sama sekali. Menghidupkan ulang..."
            StartPy "pipeline.live.xau_executor" "xauexec_out.log" "xauexec_err.log"
            $lastXauTry = $now
        }
    }

    # ---------- 4. orb_stop_manager ----------
    if (-not (ProcUp "pipeline\.live\.orb_stop_manager")) {
        if (($now - $lastOrbTry).TotalMinutes -ge $ProcCooldownMin) {
            J "ERR" "orb_stop_manager MATI - sleeve ORB (43% bobot) tidak jalan. Menghidupkan ulang..."
            StartPy "pipeline.live.orb_stop_manager" "orbmgr_out.log" "orbmgr_err.log"
            $lastOrbTry = $now
        }
    }

    # ---------- 5. rekam sinyal berkala ----------
    if ($healthy -and ($now - $lastSignalPoll).TotalMinutes -ge $SignalPollMin) {
        try {
            $s = (Invoke-WebRequest -Uri $SignalUrl -UseBasicParsing -TimeoutSec 60).Content | ConvertFrom-Json
            foreach ($sig in $s.signals) {
                $rec = @{
                    ts = (NowUtc); strategy = $sig.strategy; action = $sig.action
                    sl = $sig.sl; tp = $sig.tp; signal_id = $sig.signal_id
                } | ConvertTo-Json -Compress
                Add-Content -Path $SigLog -Value $rec -Encoding utf8
                if ($sig.action -ne $lastAction) {
                    J "SIG" ("{0}: {1} -> {2}  sl={3} tp={4}" -f `
                        $sig.strategy, $lastAction, $sig.action, $sig.sl, $sig.tp)
                    $lastAction = $sig.action
                }
            }
        } catch { }
        $lastSignalPoll = $now
    }

    # ---------- 6. heartbeat ----------
    if (($now - $lastHeartbeat).TotalMinutes -ge $HeartbeatMin) {
        $mt5txt = "MT5 UP";     if (-not (Mt5Up)) { $mt5txt = "MT5 DOWN" }
        $xetxt  = "xauexec UP"; if (-not (ProcUp "pipeline\.live\.xau_executor"))    { $xetxt  = "xauexec DOWN" }
        $orbtxt = "orbmgr UP";  if (-not (ProcUp "pipeline\.live\.orb_stop_manager")) { $orbtxt = "orbmgr DOWN" }
        J "HB " ("Sehat. slots={0}. {1}. {2}. {3}." -f $slots, $mt5txt, $xetxt, $orbtxt)
        $lastHeartbeat = $now
    }

    Start-Sleep -Seconds $Interval
}
