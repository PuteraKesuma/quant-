# =====================================================================================
#  STATUS - satu layar untuk menjawab "sistemnya jalan atau tidak?"
#
#  CATATAN ENCODING: pure ASCII, alasannya sama dengan watchdog_shadow.ps1.
#
#  Prinsipnya: JANGAN pernah menyimpulkan "aman" dari hal yang gampang hijau. Tiap baris
#  di sini memeriksa sesuatu yang benar-benar bisa gagal sendiri-sendiri:
#    - watchdog bisa mati sementara brain masih hidup (dan tak ada yang menghidupkan lagi)
#    - brain bisa hidup sementara xau_executor mati -> /health hijau, NOL order terkirim
#    - MT5 bisa jalan sementara Algo Trading mati -> semua hijau, NOL order terkirim
#    - semuanya bisa hijau sementara cadangan GitHub sudah basi berhari-hari
#
#  Dipanggil oleh CEK_TRADING.bat dan AUTO_TRADING_ON.bat.
# =====================================================================================
param([switch]$Ringkas)

$ErrorActionPreference = "SilentlyContinue"
$Py     = "C:\Program Files\Python311\python.exe"
$MonDir = "C:\Quant\_MONITOR"

function Baris($label, $status, $detail) {
    "  {0,-14} {1,-9} {2}" -f $label, $status, $detail
}

$now = Get-Date

# ---------- watchdog ----------
$alive = Join-Path $MonDir "watchdog_alive.txt"
if (Test-Path $alive) {
    $umur = ($now - (Get-Item $alive).LastWriteTime).TotalSeconds
    $isi  = (Get-Content $alive -First 1)
    if ($umur -le 120) { Baris "WATCHDOG" "HIDUP" ("detak {0:N0} detik lalu - {1}" -f $umur, $isi) }
    else               { Baris "WATCHDOG" "MACET" ("detak terakhir {0:N1} menit lalu - Task Scheduler harusnya menghidupkan dalam 5 menit" -f ($umur/60)) }
} else {
    Baris "WATCHDOG" "MATI" "belum pernah berdetak - jalankan AUTO_TRADING_ON.bat"
}

# ---------- brain ----------
$slots = ""
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 5
    $j = $r.Content | ConvertFrom-Json
    $slots = ($j.strategies | ForEach-Object { $_.name }) -join ","
    Baris "BRAIN" "HIDUP" ("uptime {0:N0} menit - slot: {1}" -f ($j.uptime_seconds/60), $slots)
} catch {
    Baris "BRAIN" "MATI" "port 8000 tidak menjawab - tidak ada sinyal yang dihitung"
}

# ---------- proses eksekusi ----------
function ProcUp($pattern) {
    return [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                  Where-Object { $_.CommandLine -match $pattern })
}
if (ProcUp "pipeline\.live\.xau_executor") {
    Baris "XAU_EXECUTOR" "HIDUP" "slot zrev/eterna/eterna_asli terkirim ke broker"
} else {
    Baris "XAU_EXECUTOR" "MATI" "brain tetap hitung TAPI nol order XAU terkirim - kegagalan diam"
}
if (ProcUp "pipeline\.live\.orb_stop_manager") {
    # Lot DIBACA dari config, jangan ditulis mati. Teks lama "lot 0.03 (bobot 43%)"
    # tertinggal dari susunan 4-sleeve dan masih tampil setelah lot diturunkan ke 0.01
    # pada 2026-08-11 - dashboard yang berbohong persis jenis kegagalan diam yang
    # sudah dua kali menggigit proyek ini.
    # Diparse dengan YAML, bukan Select-String -Context: blok komentar di atas `lot:`
    # panjangnya berubah-ubah, jadi pencarian berbasis jarak baris rapuh dan sempat
    # menampilkan "?" begitu komentar bertambah.
    $lot = & $Py -c "import yaml,io;c=yaml.safe_load(io.open(r'C:\Quant\config.yaml',encoding='utf-8'));print(next(('%.2f'%s['lot'] for s in c['live']['strategies'] if s['name']=='orb30_nas'),'?'))" 2>$null
    if (-not $lot) { $lot = "?" }
    Baris "ORB_MANAGER" "HIDUP" "sleeve ORB magic 920617 lot $lot"
} else {
    Baris "ORB_MANAGER" "MATI" "sleeve ORB berhenti"
}

# ---------- MT5 ----------
$mt5proc = Get-Process terminal64 -ErrorAction SilentlyContinue
if (-not $mt5proc) {
    Baris "MT5" "MATI" "terminal64.exe tidak jalan - semua sleeve buta"
} else {
    $pout  = & $Py (Join-Path $MonDir "mt5_probe.py") 2>&1
    $pcode = $LASTEXITCODE
    switch ($pcode) {
        0       { Baris "MT5" "SIAP"     ($pout -replace '^SEHAT\s+-\s+', '') }
        2       { Baris "MT5" "TERKUNCI" "Algo Trading MATI - NYALAKAN tombolnya di MT5, watchdog tidak bisa" }
        default { Baris "MT5" "BERMASALAH" ($pout | Select-Object -First 1) }
    }
}

# ---------- cadangan off-VPS ----------
$pushf = Join-Path $MonDir "last_push_ok.txt"
if (Test-Path $pushf) {
    $jam = ($now - (Get-Item $pushf).LastWriteTime).TotalHours
    $st  = if ($jam -le 36) { "AMAN" } else { "BASI" }
    Baris "BACKUP GITHUB" $st ("push sukses terakhir {0:N1} jam lalu - {1}" -f $jam, (Get-Content $pushf -First 1))
} else {
    Baris "BACKUP GITHUB" "BELUM" "belum ada catatan push sukses - kerja terbaru HANYA di VPS ini"
}

# ---------- scheduled task ----------
$t = Get-ScheduledTask -TaskName "Quant Watchdog" -ErrorAction SilentlyContinue
if ($t) {
    $i = $t | Get-ScheduledTaskInfo
    Baris "AUTO-START" $t.State ("{0}, terakhir jalan {1}" -f $t.Principal.LogonType, $i.LastRunTime)
} else {
    Baris "AUTO-START" "BELUM" "task 'Quant Watchdog' belum terpasang - jalankan PASANG_AUTOSTART.bat"
}

# ---------- auto-login ----------
$wl = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
if ($wl.AutoAdminLogon -eq "1") {
    $plain = if ($wl.DefaultPassword) { " (PERINGATAN: password plaintext di registry)" } else { "" }
    Baris "AUTO-LOGIN" "AKTIF" ("user {0}{1} - trading lanjut sendiri setelah reboot" -f $wl.DefaultUserName, $plain)
} else {
    Baris "AUTO-LOGIN" "MATI" "kalau VPS reboot, trading BERHENTI sampai ada yang RDP login"
}

if ($Ringkas) { return }

# ---------- posisi terbuka ----------
Write-Host ""
Write-Host "  POSISI TERBUKA"
$posOut = & $Py -c "import MetaTrader5 as m;m.initialize();p=m.positions_get() or [];print('  (tidak ada posisi terbuka)') if not p else [print('    magic %-8d %-10s %-5s %.2f lot  entry %.2f  pnl %+.2f' % (x.magic,x.symbol,'BUY' if x.type==0 else 'SELL',x.volume,x.price_open,x.profit)) for x in p];o=m.orders_get() or [];[print('    PENDING magic %-8d %-10s %.2f lot @ %.2f' % (x.magic,x.symbol,x.volume_initial,x.price_open)) for x in o];m.shutdown()" 2>&1
$posOut | ForEach-Object { Write-Host $_ }

# ---------- jurnal ----------
Write-Host ""
Write-Host "  15 BARIS TERAKHIR JURNAL"
Get-Content (Join-Path $MonDir "jurnal.md") -Tail 15 | ForEach-Object { Write-Host "  $_" }
Write-Host ""
