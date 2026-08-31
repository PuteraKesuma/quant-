# ASTUNGKARA_CUAN - backup sistem trading.
#
#   powershell -File C:\Quant\tools\backup_astungkara.ps1
#
# APA YANG MASUK
#   seluruh C:\Quant  : kode, config, preset, EA, tes, riset, dokumen, .git
#   _MONITOR          : jurnal (.json/.jsonl) -- catatan basket, governor, regime
#   report backtest   : hanya run acuan (gate 4 tahun, rt*, z_*), bukan 161-nya
#   terminal MT5 live : MQL5\Experts dan MQL5\Presets, supaya .set dan .ex5 yang
#                       BENAR-BENAR dipakai ikut tersimpan -- bukan cuma salinan
#                       di repo, karena keduanya pernah berbeda
#
# APA YANG TIDAK, dan kenapa
#   mt5_tester cache  : 4,4 GB tick. MT5 mengunduh ulang sendiri; menyimpannya
#                       membuat backup mustahil dipindahkan.
#   data\Level_0_Raw  : 280 MB harga mentah, bisa ditarik ulang dari MT5.
#   *.log             : brain.out/err ~9 MB, tumbuh terus, tidak bisa dipulihkan
#                       jadi apa pun. Jurnal .jsonl TETAP disimpan.
#   __pycache__, .pytest_cache : hasil bangunan.
#
# PERINGATAN RAHASIA
#   Backup ini MEMUAT .env. Itu memang perlu supaya sistemnya bisa dipulihkan,
#   tapi artinya berkas ZIP ini TIDAK BOLEH diunggah ke tempat publik -- bukan
#   ke GitHub, bukan ke Drive yang dibagikan, bukan ke grup chat.
$ErrorActionPreference = "Stop"

$src   = "C:\Quant"
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$nama  = "ASTUNGKARA_CUAN_$stamp"
$tujuan = Join-Path ([Environment]::GetFolderPath("Desktop")) "$nama.zip"
$stage = Join-Path $env:TEMP "astungkara_$stamp\$nama"

Write-Host "menyiapkan $nama ..." -ForegroundColor Cyan
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

# --- 1. inti repo, tanpa yang berat -----------------------------------------
$xd = @("$src\mt5_tester", "$src\data", "$src\_MONITOR")
robocopy $src "$stage\Quant" /E /NFL /NDL /NJH /NJS /NP `
    /XD $xd "__pycache__" ".pytest_cache" ".venv" "venv" "node_modules" `
    /XF "*.pyc" | Out-Null

# --- 2. _MONITOR: jurnal saja, log dibuang ----------------------------------
New-Item -ItemType Directory -Path "$stage\Quant\_MONITOR" -Force | Out-Null
Get-ChildItem "$src\_MONITOR" -File |
    Where-Object { $_.Extension -in ".json", ".jsonl", ".csv", ".txt" -and $_.Length -lt 20MB } |
    ForEach-Object { Copy-Item $_.FullName "$stage\Quant\_MONITOR\" -Force }

# --- 3. report acuan + skrip tester -----------------------------------------
New-Item -ItemType Directory -Path "$stage\Quant\mt5_tester" -Force | Out-Null
Get-ChildItem "$src\mt5_tester" -File |
    Where-Object {
        $_.Extension -in ".ps1", ".ini" -or
        ($_.Name -like "report_gt2*.htm") -or ($_.Name -like "report_rt*.htm") -or
        ($_.Name -like "report_z_*.htm") -or ($_.Name -like "*progress.txt")
    } | ForEach-Object { Copy-Item $_.FullName "$stage\Quant\mt5_tester\" -Force }
if (Test-Path "$src\mt5_tester\MQL5\Experts") {
    New-Item -ItemType Directory -Path "$stage\Quant\mt5_tester\MQL5\Experts" -Force | Out-Null
    Copy-Item "$src\mt5_tester\MQL5\Experts\*.mq5" "$stage\Quant\mt5_tester\MQL5\Experts\" -Force -EA SilentlyContinue
}

# --- 4. terminal MT5 live: EA & preset yang BENAR-BENAR dipakai -------------
$term = "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5"
foreach ($sub in @("Experts", "Presets")) {
    if (Test-Path "$term\$sub") {
        $d = "$stage\MT5_terminal_live\$sub"
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Copy-Item "$term\$sub\*" $d -Recurse -Force -EA SilentlyContinue
    }
}

# --- 5. catatan pemulihan ---------------------------------------------------
@"
ASTUNGKARA_CUAN - $stamp

ISI
  Quant\                 seluruh kode, config, preset, tes, riset, .git
  Quant\_MONITOR\        jurnal basket / governor / regime (log dibuang)
  Quant\mt5_tester\      report acuan + skrip + .ini (cache tick TIDAK ikut)
  MT5_terminal_live\     Experts & Presets dari terminal yang benar-benar jalan

BERISI RAHASIA
  Quant\.env memuat kredensial. JANGAN unggah ZIP ini ke tempat publik.

CARA PULIH
  1. Kembalikan Quant\ ke C:\Quant
  2. pip install -r requirements.txt
  3. Salin MT5_terminal_live\Experts\*.ex5 dan Presets\*.set ke
     %APPDATA%\MetaQuotes\Terminal\<ID>\MQL5\
  4. Pasang SemiMartiV10_Gated di chart XAUUSD M5, F7 -> Load ->
     SemiMartiV10_FINAL.set -> OK   (WAJIB tekan Load; tanpa itu MT5 memakai
     input lama yang tersimpan per-chart)
  5. Jalankan brain dan governor, lalu python tools\verify_system.py
     Pastikan akun yang tampil 28908348 FBS-Real, BUKAN akun demo tester.

  Cache tick MT5 akan terunduh sendiri saat backtest pertama dijalankan.
"@ | Out-File "$stage\BACA-DULU.txt" -Encoding utf8

# --- 6. jadikan ZIP ---------------------------------------------------------
# ZipFile::CreateFromDirectory, BUKAN Compress-Archive: Compress-Archive
# melewatkan folder tersembunyi, sehingga .git (8,8 MB riwayat penuh) diam-diam
# tidak ikut -- backup terlihat berhasil padahal riwayatnya hilang.
if (Test-Path $tujuan) { Remove-Item $tujuan -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    (Split-Path $stage -Parent), $tujuan,
    [System.IO.Compression.CompressionLevel]::Optimal, $false)

# Pastikan .git benar-benar masuk -- kalau tidak, katakan, jangan diam.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zz = [System.IO.Compression.ZipFile]::OpenRead($tujuan)
$gitN = ($zz.Entries | Where-Object { $_.FullName -match '[\\/]\.git[\\/]' }).Count
$zz.Dispose()

$n = (Get-ChildItem $stage -Recurse -File | Measure-Object).Count
$mb = [math]::Round((Get-Item $tujuan).Length / 1MB, 2)
Remove-Item (Split-Path $stage -Parent) -Recurse -Force -EA SilentlyContinue

Write-Host ""
Write-Host "SELESAI  $tujuan" -ForegroundColor Green
Write-Host "  $n berkas, $mb MB"
if ($gitN -gt 0) {
    Write-Host "  riwayat .git ikut: $gitN objek" -ForegroundColor Green
} else {
    Write-Host "  PERINGATAN: .git TIDAK ikut -- riwayat hanya ada di GitHub" -ForegroundColor Red
}
Write-Host ""
Write-Host "  BERISI .env -- jangan diunggah ke tempat publik." -ForegroundColor Yellow
