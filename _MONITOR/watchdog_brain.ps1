# ============================================================
#  BRAIN WATCHDOG  -  jaga signal server (brain) tetap UP
#  - cek http://127.0.0.1:8000/health tiap 30 dtk
#  - kalau DOWN 3x beruntun -> auto-restart via START_TRADING.bat
#  - catat semua ke _MONITOR\jurnal.md (buat dibaca manusia)
#  - sampel mentah ke _MONITOR\health_log.jsonl
#  JANGAN tutup jendela ini -> watchdog mati.
# ============================================================
$ErrorActionPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "BRAIN WATCHDOG - JANGAN DITUTUP"

$MonDir    = "C:\Quant\_MONITOR"
$Jurnal    = Join-Path $MonDir "jurnal.md"
$HealthLog = Join-Path $MonDir "health_log.jsonl"
$VisionJrn = "C:\Quant\vision_journal.jsonl"
$Bat       = "C:\Quant\START_TRADING.bat"
$AdvBat    = "C:\Quant\START_ADVISOR.bat"
$AdvJrn    = "C:\Quant\advisor_journal.jsonl"
$LiqBat    = "C:\Quant\START_LIQMGR.bat"
$OrbBat    = "C:\Quant\START_ORBMGR.bat"
$GovBat    = "C:\Quant\START_GOVERNOR.bat"
$Mt5Exe    = "C:\Program Files\MetaTrader 5\terminal64.exe"
$HealthUrl = "http://127.0.0.1:8000/health"
$TermDir   = Join-Path $env:APPDATA "MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075"

$Interval           = 30   # detik antar cek
$FailsToRestart     = 3    # gagal beruntun sebelum dianggap DOWN + restart
$HeartbeatMin       = 30   # menit antar baris "sehat" rutin di jurnal
$RestartCooldownMin = 3    # jeda minimum antar percobaan restart
$AdvCooldownMin     = 3    # jeda minimum antar relaunch advisor (non-kritis, insight-only)
$LiqCooldownMin     = 3    # jeda minimum antar relaunch liquidity manager (pasang order limit)
$OrbCooldownMin     = 3    # jeda minimum antar relaunch orb stop manager (pasang order stop)

function NowUtc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }
function J($emoji, $msg) {
    $line = "- **{0} UTC** {1} {2}" -f (NowUtc), $emoji, $msg
    Add-Content -Path $Jurnal -Value $line -Encoding utf8
    Write-Host $line
}
function AdvisorUp {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "pipeline\.live\.advisor" }
    return [bool]$p
}
function LiqMgrUp {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "pipeline\.live\.liquidity_manager" }
    return [bool]$p
}
function OrbMgrUp {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "pipeline\.live\.orb_stop_manager" }
    return [bool]$p
}
function GovUp {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match "pipeline\.live\.monthly_governor" }
    return [bool]$p
}
function AlgoDisabledNow {
    # true if the EA log's recent lines show orders rejected 10027 (global Algo Trading button OFF)
    $log = Join-Path $TermDir ("MQL5\Logs\" + (Get-Date).ToString("yyyyMMdd") + ".log")
    if (-not (Test-Path $log)) { return $false }
    $tail = Get-Content $log -Tail 12 -ErrorAction SilentlyContinue
    return [bool]($tail -match "auto trading disabled")
}
function EnableAlgo {
    # send Ctrl+E to the MT5 window to toggle the global Algo Trading button back ON
    $p = Get-Process terminal64 -ErrorAction SilentlyContinue
    if (-not $p) { return }
    try {
        $ws = New-Object -ComObject wscript.shell
        if ($ws.AppActivate($p.Id)) { Start-Sleep -Milliseconds 600; $ws.SendKeys('^e') }
    } catch { }
}

$fail        = 0
$down        = $false
$lastHb      = [datetime]::MinValue
$lastRestart = [datetime]::MinValue
$restarts    = 0
$advRestarts    = 0
$lastAdvRestart = [datetime]::MinValue
$advFail        = 0
$liqRestarts    = 0
$lastLiqRestart = [datetime]::MinValue
$liqFail        = 0
$orbRestarts    = 0
$lastOrbRestart = [datetime]::MinValue
$orbFail        = 0
$govRestarts    = 0
$lastGovRestart = [datetime]::MinValue
$govFail        = 0
$GovCooldownMin = 3
$eaFail         = 0    # EA-polling check: brain sehat TAPI ea:{} kosong = EA hilang dari chart
$lastAlgoFix     = [datetime]::MinValue
$AlgoCooldownMin = 2    # jeda antar kirim Ctrl+E (aktifkan Algo Trading)
$lastEaRestart  = [datetime]::MinValue
$EaFailsToRestart = 20   # 20 x 30 dtk = EA diam 10 menit (EA poll tiap detik, market buka/tutup)
$EaCooldownMin    = 30   # jeda antar restart MT5 via jalur ini

J "==>" "Watchdog START. Cek tiap $Interval dtk; restart setelah $FailsToRestart gagal beruntun."

while ($true) {
    $ok = $false; $detail = ""
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
        $ok = $true; $detail = $r.Content
    } catch {
        $detail = $_.Exception.Message
    }

    # sampel mentah
    $rec = @{ ts = (Get-Date).ToUniversalTime().ToString("o"); ok = $ok; detail = $detail } | ConvertTo-Json -Compress
    Add-Content -Path $HealthLog -Value $rec -Encoding utf8

    if ($ok) {
        $fail = 0
        if ($down) { J "OK " "Brain PULIH - health OK lagi."; $down = $false }

        if (((Get-Date) - $lastHb).TotalMinutes -ge $HeartbeatMin) {
            # ringkasan aktivitas advisor (baca file saja, tidak menyentuh MT5)
            $asum = "advisor_journal: belum ada entri"
            if (Test-Path $AdvJrn) {
                $alines = Get-Content $AdvJrn
                $last = ($alines | Select-Object -Last 1)
                if ($last -and $last.Length -gt 180) { $last = $last.Substring(0,180) + "..." }
                $asum = "advisor entries=$($alines.Count); terakhir: $last"
            }
            $astat = if (AdvisorUp) { "advisor UP" } else { "advisor DOWN" }
            J "HB " "Sehat (heartbeat). $astat. $asum"
            $lastHb = Get-Date
        }
    } else {
        $fail++
        if ($fail -ge $FailsToRestart) {
            if (-not $down) { $down = $true; J "!! " "Brain DOWN ($fail cek gagal beruntun). Error: $detail" }

            $mt5 = Get-Process -Name terminal64 -ErrorAction SilentlyContinue
            if (-not $mt5) {
                # MT5 mati: brain pasti gagal preflight. Coba hidupkan MT5 dulu (butuh
                # 'Save account information' di MT5 agar auto-login). Brain di-restart
                # siklus berikutnya setelah MT5 sempat connect.
                if (((Get-Date) - $lastRestart).TotalMinutes -ge $RestartCooldownMin) {
                    J "WARN" "MT5 (terminal64) TIDAK jalan - brain butuh MT5."
                    if (Test-Path $Mt5Exe) {
                        Start-Process -FilePath $Mt5Exe
                        J "MT5" "terminal64 diluncurkan ulang. Auto-login bergantung pada 'Save account information' di MT5."
                    } else {
                        J "ERR" "Path MT5 tidak ditemukan: $Mt5Exe (set manual di skrip)."
                    }
                    $lastRestart = Get-Date  # cooldown + throttle
                }
            } elseif (((Get-Date) - $lastRestart).TotalMinutes -ge $RestartCooldownMin) {
                $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
                if ($conn) {
                    J "CLN" "Bersihkan proses zombie di port 8000 (PID $($conn.OwningProcess))."
                    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
                }
                $restarts++; $lastRestart = Get-Date
                J "RST" "Restart brain #$restarts via START_TRADING.bat ..."
                Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $Bat -WorkingDirectory "C:\Quant"
                Start-Sleep -Seconds 20
                try {
                    Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
                    J "OK " "Brain ONLINE lagi setelah restart #$restarts."
                    $down = $false; $fail = 0
                } catch {
                    J "ERR" "Restart #$restarts belum sukses; coba lagi setelah cooldown $RestartCooldownMin mnt."
                }
            }
        }
    }

    # --- Shadow Advisor (non-kritis, insight-only): relaunch kalau prosesnya hilang ---
    # Butuh DOWN 2x beruntun (beri waktu boot saat logon) + cooldown, biar tak dobel.
    if (AdvisorUp) {
        $advFail = 0
    } else {
        $advFail++
        if ($advFail -ge 2 -and ((Get-Date) - $lastAdvRestart).TotalMinutes -ge $AdvCooldownMin) {
            $advRestarts++; $lastAdvRestart = Get-Date; $advFail = 0
            J "ADV" "Shadow Advisor tidak jalan (2 cek) - relaunch #$advRestarts via START_ADVISOR.bat ..."
            Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $AdvBat -WorkingDirectory "C:\Quant"
        }
    }

    # --- Liquidity Manager: RETIRED 2026-07-08 (weakest edge PF 1.33, bled in the gold downtrend;
    #     user simplified to one strategy per commodity: Z=XAU, ORB=NQ). NO relaunch. Do NOT
    #     re-enable without re-adding the relaunch block + START_LIQMGR startup shortcut. ---
    # (LiqMgrUp intentionally not watched)

    # --- EA polling check: 2026-07-05 VPS reboot membuat MT5 bangun TANPA EA di chart, semua
    #     monitor lain tetap "sehat" 2.5 hari. EA poll brain tiap detik (timer, 24/7) -> kalau
    #     brain UP tapi ea:{} kosong >= 10 mnt, profil chart kehilangan EA -> restart MT5
    #     (profil sekarang menyimpan SignalExecutor, restart = EA terpasang lagi). ---
    if ($ok) {
        $eaAlive = $false
        try {
            $hj = $detail | ConvertFrom-Json
            foreach ($p in @($hj.ea.PSObject.Properties)) { if ($p.Value.connected) { $eaAlive = $true } }
            if ($eaAlive -or ($hj.uptime_seconds -lt 120)) { $eaFail = 0 } else { $eaFail++ }
        } catch { }
        if ($eaFail -ge $EaFailsToRestart -and ((Get-Date) - $lastEaRestart).TotalMinutes -ge $EaCooldownMin) {
            $lastEaRestart = Get-Date; $eaFail = 0
            J "EA " "EA TIDAK polling >=10 mnt (brain UP) - restart MT5 utk muat ulang profil chart+EA. CEK juga proses python (IPC) kalau masih bermasalah."
            Stop-Process -Name terminal64 -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 5
            if (Test-Path $Mt5Exe) { Start-Process -FilePath $Mt5Exe }
        }
    }

    # --- Algo Trading toggle: an MT5 restart (VPS reboot / the EA-restart above) leaves the global
    #     "Algo Trading" button OFF -> every order fails 10027 SILENTLY (heartbeat still trading=ON).
    #     Found 2026-07-09 by the WMT dummy-signal test. Detect the 10027 in the EA log + Ctrl+E. ---
    if (AlgoDisabledNow -and ((Get-Date) - $lastAlgoFix).TotalMinutes -ge $AlgoCooldownMin) {
        $lastAlgoFix = Get-Date
        J "ALG" "Algo Trading MATI (order 10027 di log EA) - kirim Ctrl+E ke MT5 utk aktifkan lagi."
        EnableAlgo
    }

    # --- ORB Stop Manager (pasang order stop asli, magic 920617): relaunch kalau hilang ---
    if (OrbMgrUp) {
        $orbFail = 0
    } else {
        $orbFail++
        if ($orbFail -ge 2 -and ((Get-Date) - $lastOrbRestart).TotalMinutes -ge $OrbCooldownMin) {
            $orbRestarts++; $lastOrbRestart = Get-Date; $orbFail = 0
            J "ORB" "ORB Stop Manager tidak jalan (2 cek) - relaunch #$orbRestarts via START_ORBMGR.bat ..."
            Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $OrbBat -WorkingDirectory "C:\Quant"
        }
    }

    # --- Monthly Profit Governor (pause new entries at 75% of target): relaunch kalau hilang ---
    if (GovUp) {
        $govFail = 0
    } else {
        $govFail++
        if ($govFail -ge 2 -and ((Get-Date) - $lastGovRestart).TotalMinutes -ge $GovCooldownMin) {
            $govRestarts++; $lastGovRestart = Get-Date; $govFail = 0
            J "GOV" "Monthly Governor tidak jalan (2 cek) - relaunch #$govRestarts via START_GOVERNOR.bat ..."
            Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $GovBat -WorkingDirectory "C:\Quant"
        }
    }

    Start-Sleep -Seconds $Interval
}
