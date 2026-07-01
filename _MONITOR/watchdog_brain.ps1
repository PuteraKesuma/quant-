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
$Mt5Exe    = "C:\Program Files\MetaTrader 5\terminal64.exe"
$HealthUrl = "http://127.0.0.1:8000/health"

$Interval           = 30   # detik antar cek
$FailsToRestart     = 3    # gagal beruntun sebelum dianggap DOWN + restart
$HeartbeatMin       = 30   # menit antar baris "sehat" rutin di jurnal
$RestartCooldownMin = 3    # jeda minimum antar percobaan restart
$AdvCooldownMin     = 3    # jeda minimum antar relaunch advisor (non-kritis, insight-only)

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

$fail        = 0
$down        = $false
$lastHb      = [datetime]::MinValue
$lastRestart = [datetime]::MinValue
$restarts    = 0
$advRestarts    = 0
$lastAdvRestart = [datetime]::MinValue
$advFail        = 0

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

    Start-Sleep -Seconds $Interval
}
