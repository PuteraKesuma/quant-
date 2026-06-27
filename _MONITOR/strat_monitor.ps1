# ============================================================
#  STRATEGY MONITOR — catat keputusan VISION (v2) + sinyal NAS100
#  ke _MONITOR\today_YYYYMMDD.md. Murah & aman:
#   - vision: baca vision_journal.jsonl (file, no token)
#   - NAS: GET /signals?symbol=NAS100 (cuma evaluasi ORB NAS, no token)
#   - tidak menyentuh MT5 (hindari kontensi dgn brain)
#  Cek tiap 10 menit. Jendela boleh ditutup tanpa efek ke trading.
# ============================================================
$ErrorActionPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "STRATEGY MONITOR (vision + NAS100)"
$Mon = "C:\Quant\_MONITOR"
$VJ  = "C:\Quant\vision_journal.jsonl"

function NowU { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm") }
function TodayFile { Join-Path $Mon ("today_" + (Get-Date).ToUniversalTime().ToString("yyyyMMdd") + ".md") }
function L($emoji, $msg) {
    $f = TodayFile
    if (-not (Test-Path $f)) {
        Set-Content $f ("# Monitor strategi " + (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd") + " (UTC)`n`nVision (tiap 30m) + NAS100 (NY 13:30/14:30 DST). Posisi/PnL dicek manual.`n") -Encoding utf8
    }
    Add-Content $f ("- **{0}Z** {1} {2}" -f (NowU), $emoji, $msg) -Encoding utf8
}

$lastVision = $null      # count of journal lines already logged
$lastNas = ""
L "▶️" "Monitor strategi mulai."

while ($true) {
    # --- VISION: log only NEW journal entries since monitor start ---
    if (Test-Path $VJ) {
        $lines = @(Get-Content $VJ)
        if ($null -eq $lastVision) { $lastVision = $lines.Count }   # skip backlog on first run
        for ($i = $lastVision; $i -lt $lines.Count; $i++) {
            try {
                $o = $lines[$i] | ConvertFrom-Json
                $r = [string]$o.reason; if ($r.Length -gt 150) { $r = $r.Substring(0, 150) + "..." }
                L "🔮VIS" "$($o.action) conf=$($o.confidence) — $r"
            } catch {}
        }
        $lastVision = $lines.Count
    }

    # --- NAS100: log when orb30_nas signal changes ---
    try {
        $s = ((Invoke-WebRequest 'http://127.0.0.1:8000/signals?symbol=NAS100' -UseBasicParsing -TimeoutSec 6).Content | ConvertFrom-Json).signals | Where-Object { $_.strategy -eq 'orb30_nas' }
        if ($s) {
            $key = "$($s.action)|$($s.signal_id)"
            if ($key -ne $lastNas) { L "📊NAS" "$($s.action)  ($($s.signal_id))"; $lastNas = $key }
        }
    } catch {}

    Start-Sleep -Seconds 600
}
