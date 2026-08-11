# =====================================================================================
#  TES REBOOT - membuktikan lapis 3 (auto-login) benar-benar bekerja.
#
#  CATATAN ENCODING: pure ASCII, alasannya sama dengan watchdog_shadow.ps1.
#
#  KENAPA SKRIP INI ADA, bukan diperiksa manual:
#  Sesi Claude Code berjalan DI DALAM VPS ini, jadi reboot mematikannya juga. Tidak ada
#  siapa pun yang bisa mengamati menit-menit pertama setelah boot - justru menit yang
#  paling menentukan. Skrip ini yang mengamati, lalu menulis laporannya ke file.
#
#  Yang dibuktikan, berurutan dari yang paling gampang dipalsukan ke yang paling keras:
#    1. Windows login SENDIRI tanpa RDP           -> ada sesi interaktif setelah boot
#    2. Task Scheduler menjalankan watchdog        -> file detak segar
#    3. Watchdog menghidupkan seluruh rantai       -> brain/xauexec/orbmgr/MT5 hidup
#    4. RANTAI BENAR-BENAR MENGIRIM ORDER          -> ada deal magic 920699 SETELAH boot
#  Nomor 4 yang paling penting. Tiga yang pertama bisa hijau semua sementara nol order
#  terkirim; itu persis jenis kegagalan diam yang jadi alasan semua ini dibangun.
#
#  Dijalankan otomatis oleh task "Quant Tes Reboot" (AtStartup, sekali pakai).
# =====================================================================================
$ErrorActionPreference = "SilentlyContinue"

$Py     = "C:\Program Files\Python311\python.exe"
$MonDir = "C:\Quant\_MONITOR"
$Out    = Join-Path $MonDir "hasil_tes_reboot.md"
$Jurnal = Join-Path $MonDir "jurnal.md"

function NowUtc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }
function J($tag, $msg) {
    Add-Content -Path $Jurnal -Value ("- **{0} UTC** {1} [tes-reboot] {2}" -f (NowUtc), $tag, $msg) -Encoding utf8
}

$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
J "ON " "Mulai verifikasi pasca-reboot. Boot pada $boot."

$baris = New-Object System.Collections.Generic.List[string]
$baris.Add("# Hasil tes reboot")
$baris.Add("")
$baris.Add("Boot terakhir : $boot")
$baris.Add("Laporan dibuat: $(NowUtc) UTC")
$baris.Add("")

# ---------- 1. sesi interaktif ada? ----------
# Kalau auto-login gagal, Windows berhenti di layar login dan tidak ada sesi Active.
$sesi = (query session 2>$null) -join "`n"
$adaSesi = $sesi -match "Active"
$baris.Add("## 1. Auto-login")
if ($adaSesi) {
    $baris.Add("- **LULUS** - ada sesi interaktif aktif tanpa ada yang RDP.")
} else {
    $baris.Add("- **GAGAL** - tidak ada sesi Active. Windows kemungkinan berhenti di layar login,")
    $baris.Add("  artinya MT5 tidak punya desktop dan seluruh rantai tidak bisa jalan.")
}
$baris.Add("")
$baris.Add('```')
$baris.Add($sesi)
$baris.Add('```')
$baris.Add("")

# ---------- 2 & 3. tunggu rantai naik ----------
$baris.Add("## 2. Watchdog + rantai")
$sehat = $false
for ($i = 0; $i -lt 60; $i++) {          # sampai 10 menit
    try {
        if ((Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) {
            $sehat = $true; break
        }
    } catch { }
    Start-Sleep -Seconds 10
}
$detikSampaiSehat = ((Get-Date) - $boot).TotalSeconds

function ProcUp($pattern) {
    return [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                  Where-Object { $_.CommandLine -match $pattern })
}
$alive   = Join-Path $MonDir "watchdog_alive.txt"
$umurWd  = if (Test-Path $alive) { ((Get-Date) - (Get-Item $alive).LastWriteTime).TotalSeconds } else { 99999 }

$baris.Add("- watchdog  : " + $(if ($umurWd -le 120) { "**HIDUP** (detak $([math]::Round($umurWd)) detik lalu)" } else { "**MATI** (detak $([math]::Round($umurWd/60,1)) menit lalu)" }))
$baris.Add("- brain     : " + $(if ($sehat) { "**HIDUP** ($([math]::Round($detikSampaiSehat/60,1)) menit setelah boot)" } else { "**MATI** - /health tidak menjawab dalam 10 menit" }))
$baris.Add("- xauexec   : " + $(if (ProcUp "pipeline\.live\.xau_executor")    { "**HIDUP**" } else { "**MATI**" }))
$baris.Add("- orbmgr    : " + $(if (ProcUp "pipeline\.live\.orb_stop_manager") { "**HIDUP**" } else { "**MATI**" }))

$pout  = & $Py (Join-Path $MonDir "mt5_probe.py") 2>&1
$pcode = $LASTEXITCODE
$baris.Add("- MT5       : " + $(switch ($pcode) {
    0       { "**SIAP** - $pout" }
    2       { "**TERKUNCI** - Algo Trading mati, nol order bisa terkirim" }
    default { "**BERMASALAH** - $pout" }
}))
$baris.Add("")

# ---------- 4. bukti paling keras: order sungguhan setelah boot ----------
$baris.Add("## 3. Order sungguhan setelah boot (magic 920699)")
$bootEpoch = [int][double]::Parse((Get-Date $boot -UFormat %s))
$deals = ""
for ($i = 0; $i -lt 48; $i++) {          # sampai 8 menit menunggu siklus dummy
    $deals = & $Py -c @"
import MetaTrader5 as m, datetime as dt
m.initialize()
d = m.history_deals_get(dt.datetime.now()-dt.timedelta(hours=2), dt.datetime.now()+dt.timedelta(hours=6)) or []
d = [x for x in d if x.magic == 920699 and x.time >= $bootEpoch]
for x in d:
    print('%s  %-4s %-3s  %.2f lot @ %.2f  pnl %+.2f  %s' % (
        dt.datetime.utcfromtimestamp(x.time).strftime('%H:%M:%S'),
        'BUY' if x.type == 0 else 'SELL', 'IN' if x.entry == 0 else 'OUT',
        x.volume, x.price, x.profit, x.comment))
m.shutdown()
"@ 2>&1
    if (($deals | Measure-Object -Line).Lines -ge 2) { break }
    Start-Sleep -Seconds 10
}
if (($deals | Measure-Object -Line).Lines -ge 2) {
    $baris.Add("- **LULUS** - rantai brain -> xau_executor -> MT5 mengirim order sendiri setelah reboot,")
    $baris.Add("  tanpa ada manusia yang login atau menjalankan apa pun.")
} elseif ($deals) {
    $baris.Add("- **SEBAGIAN** - ada order masuk tapi round-trip belum lengkap saat laporan dibuat.")
} else {
    $baris.Add("- **GAGAL** - tidak ada deal magic 920699 setelah boot. Rantai TIDAK mengeksekusi.")
}
$baris.Add("")
$baris.Add('```')
$baris.Add($(if ($deals) { $deals } else { "(kosong)" }))
$baris.Add('```')
$baris.Add("")

# ---------- vonis ----------
$lulus = $adaSesi -and $sehat -and ($umurWd -le 120) -and ($pcode -eq 0) -and (($deals | Measure-Object -Line).Lines -ge 2)
$baris.Add("## Vonis")
$baris.Add($(if ($lulus) {
    "**SEMUA LULUS.** Lapis 3 terbukti: VPS reboot -> Windows login sendiri -> watchdog naik -> rantai hidup -> order terkirim. Tidak ada satu langkah pun yang butuh manusia."
} else {
    "**ADA YANG GAGAL.** Baca bagian di atas yang bertanda GAGAL. Jangan anggap sistem ini tahan reboot sampai semuanya lulus."
}))
$baris.Add("")
$baris.Add("Jangan lupa: `flowtest_dummy` (magic 920699) masih NYALA dan membakar spread tiap siklus. Matikan di config.yaml begitu tes ini selesai dibaca.")

Set-Content -Path $Out -Value ($baris -join "`r`n") -Encoding utf8
J $(if ($lulus) { "OK " } else { "ERR" }) "Verifikasi pasca-reboot selesai. Hasil: $(if ($lulus) { 'SEMUA LULUS' } else { 'ADA YANG GAGAL' }). Laporan: $Out"

# task ini sekali pakai - matikan supaya tidak jalan lagi tiap boot berikutnya
Disable-ScheduledTask -TaskName "Quant Tes Reboot" -ErrorAction SilentlyContinue | Out-Null
