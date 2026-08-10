# =====================================================================================
#  WATCHDOG SHADOW — khusus fase shadow-test eterna (2026-08-07)
#
#  Bedanya dengan watchdog_brain.ps1 (yang lama): watchdog lama juga menghidupkan
#  ADVISOR, LIQMGR, ORBMGR, dan GOVERNOR. Di fase ini semuanya TIDAK dipakai, dan
#  advisor bahkan membakar kredit API Anthropic tiap entry. Jadi jangan pakai yang
#  lama sebelum seluruh book dijalankan lagi.
#
#  Yang dijaga di sini HANYA dua:
#    1. BRAIN (pipeline.live.run_server) — sumber sinyal
#    2. MetaTrader 5 — sumber bar M1; kalau mati, brain buta
#
#  TIDAK ada order yang dikirim selama EA belum di-attach ke chart.
#
#  Jalankan:
#    powershell -ExecutionPolicy Bypass -File C:\Quant\_MONITOR\watchdog_shadow.ps1
# =====================================================================================
$ErrorActionPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "WATCHDOG SHADOW (eterna) - JANGAN DITUTUP"

$Py        = "C:\Program Files\Python311\python.exe"
$Root      = "C:\Quant"
$MonDir    = "C:\Quant\_MONITOR"
$Jurnal    = Join-Path $MonDir "jurnal.md"
$HealthLog = Join-Path $MonDir "health_log.jsonl"
$SigLog    = Join-Path $MonDir "eterna_shadow.jsonl"
$OutLog    = Join-Path $MonDir "brain_out.log"
$ErrLog    = Join-Path $MonDir "brain_err.log"
$Mt5Exe    = "C:\Program Files\MetaTrader 5\terminal64.exe"
$HealthUrl = "http://127.0.0.1:8000/health"
$SignalUrl = "http://127.0.0.1:8000/signals?symbol=XAUUSD"

$Interval           = 30    # detik antar cek
$FailsToRestart     = 3     # gagal beruntun sebelum dianggap DOWN
$RestartCooldownMin = 3     # jeda minimum antar percobaan restart brain
$Mt5CooldownMin     = 5     # jeda minimum antar percobaan start MT5
$HeartbeatMin       = 30    # menit antar baris "sehat" di jurnal
$SignalPollMin      = 15    # menit antar perekaman sinyal ke eterna_shadow.jsonl

function NowUtc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }

function J($tag, $msg) {
    $line = "- **{0} UTC** {1} [shadow] {2}" -f (NowUtc), $tag, $msg
    Add-Content -Path $Jurnal -Value $line -Encoding utf8
    Write-Host $line
}

function BrainUp {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "pipeline\.live\.run_server" }
    return [bool]$p
}

function OrbMgrUp {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "pipeline\.live\.orb_stop_manager" }
    return [bool]$p
}

function StartOrbMgr {
    Start-Process -FilePath $Py -ArgumentList "-m", "pipeline.live.orb_stop_manager" `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput "$MonDir\orbmgr_out.log" -RedirectStandardError "$MonDir\orbmgr_err.log"
}

function Mt5Up {
    return [bool](Get-Process terminal64 -ErrorAction SilentlyContinue)
}

function StartBrain {
    Start-Process -FilePath $Py -ArgumentList "-m", "pipeline.live.run_server" `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
}

# --- state ---
$fails          = 0
$lastRestart    = (Get-Date).AddHours(-1)
$lastMt5Try     = (Get-Date).AddHours(-1)
$lastHeartbeat  = (Get-Date).AddHours(-1)
$lastSignalPoll = (Get-Date).AddHours(-1)
$lastOrbTry     = (Get-Date).AddHours(-1)
$lastAction     = ""
$OrbCooldownMin = 3

J "ON " "Watchdog START. Menjaga: BRAIN + MT5 + ORB_STOP_MANAGER. (advisor/liqmgr/governor SENGAJA tidak dijalankan — advisor membakar kredit API Anthropic.)"

while ($true) {
    $now = Get-Date

    # ---------- 1. MT5 hidup? (brain butuh bar M1 darinya) ----------
    if (-not (Mt5Up)) {
        if (($now - $lastMt5Try).TotalMinutes -ge $Mt5CooldownMin) {
            J "ERR" "MetaTrader 5 MATI - brain kehilangan sumber bar. Menjalankan ulang..."
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
                if (BrainUp) {
                    J "CLN" "Brain proses ada tapi health mati - menghentikan proses lama."
                    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                        Where-Object { $_.CommandLine -match "pipeline\.live\.run_server" } |
                        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
                    Start-Sleep -Seconds 3
                }
                J "RST" "Brain DOWN ($fails cek gagal) - menjalankan ulang..."
                StartBrain
                $lastRestart = $now
            }
        }
    }

    # ---------- 2b. ORB stop manager hidup? ----------
    # Sleeve ORB (bobot TERBESAR, lot 0.03) TIDAK lewat EA — manager ini menempatkan
    # pending STOP langsung via MT5 Python API. Kalau dia mati, ORB hilang total dan
    # tidak ada tanda apa pun di /health (health hanya memantau slot brain).
    if (-not (OrbMgrUp)) {
        if (($now - $lastOrbTry).TotalMinutes -ge $OrbCooldownMin) {
            J "ERR" "orb_stop_manager MATI - sleeve ORB (43% bobot) tidak jalan. Menghidupkan ulang..."
            StartOrbMgr
            $lastOrbTry = $now
        }
    }

    # ---------- 3. rekam sinyal berkala (bukti shadow: apa yang DIPUTUSKAN brain) ----------
    if ($healthy -and ($now - $lastSignalPoll).TotalMinutes -ge $SignalPollMin) {
        try {
            $s = (Invoke-WebRequest -Uri $SignalUrl -UseBasicParsing -TimeoutSec 30).Content | ConvertFrom-Json
            foreach ($sig in $s.signals) {
                $rec = @{
                    ts = (NowUtc); strategy = $sig.strategy; action = $sig.action
                    sl = $sig.sl; tp = $sig.tp; signal_id = $sig.signal_id
                } | ConvertTo-Json -Compress
                Add-Content -Path $SigLog -Value $rec -Encoding utf8
                if ($sig.action -ne $lastAction) {
                    J "SIG" ("eterna_xau: {0} -> {1}  sl={2} tp={3}  ({4})" -f `
                        $lastAction, $sig.action, $sig.sl, $sig.tp, $sig.signal_id)
                    $lastAction = $sig.action
                }
            }
        } catch { }
        $lastSignalPoll = $now
    }

    # ---------- 4. heartbeat berkala ----------
    if (($now - $lastHeartbeat).TotalMinutes -ge $HeartbeatMin) {
        $mt5txt = "MT5 UP"
        if (-not (Mt5Up)) { $mt5txt = "MT5 DOWN" }
        $orbtxt = "orbmgr UP"
        if (-not (OrbMgrUp)) { $orbtxt = "orbmgr DOWN" }
        J "HB " ("Sehat. slots={0}. {1}. {2}. sinyal terakhir={3}" -f $slots, $mt5txt, $orbtxt, $lastAction)
        $lastHeartbeat = $now
    }

    Start-Sleep -Seconds $Interval
}
