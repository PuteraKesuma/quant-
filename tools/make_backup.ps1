# ============================================================
#  make_backup.ps1 — cadangan LENGKAP yang bisa dibawa keluar VPS.
#
#  Jalankan lewat BACKUP.bat, atau:
#      powershell -ExecutionPolicy Bypass -File tools\make_backup.ps1
#
#  Menghasilkan satu folder bertanggal berisi:
#     quant-backup.bundle   seluruh riwayat git (kode, config, EA, preset, riset)
#     presets\              file .set (juga ada di bundle; disalin terpisah supaya
#                           bisa dipakai langsung tanpa clone)
#     .env                  HANYA kalau ada -- berisi RAHASIA
#     RESTORE.txt           cara memulihkannya
#
#  KENAPA BUNDLE, BUKAN ZIP FOLDER
#  Bundle git berisi seluruh RIWAYAT, bukan cuma keadaan terakhir. Kalau sebuah
#  perubahan ternyata merusak, kamu bisa mundur ke commit mana pun. Zip folder
#  hanya menyimpan satu foto -- kalau foto itu diambil setelah kerusakan, tidak
#  ada yang bisa dipulihkan.
# ============================================================
param(
    [string]$Name = "ALGO CUAN"     # nama dasar file ZIP
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$desktop = [Environment]::GetFolderPath("Desktop")
$dest = Join-Path $desktop "$Name`_$stamp"
$zip = "$dest.zip"

New-Item -ItemType Directory -Path $dest -Force | Out-Null
Write-Host "Menyiapkan: $dest" -ForegroundColor Cyan
Write-Host ""

# --- 1. bundle git (seluruh riwayat) ---
Push-Location $root
try {
    $bundle = Join-Path $dest "quant-backup.bundle"
    git bundle create $bundle --all 2>&1 | Out-Null
    if (Test-Path $bundle) {
        $mb = [math]::Round((Get-Item $bundle).Length / 1MB, 1)
        Write-Host "  [ OK ] quant-backup.bundle  ($mb MB, seluruh riwayat git)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] gagal membuat bundle git" -ForegroundColor Red
    }

    # peringatkan kalau ada perubahan yang belum di-commit -- bundle TIDAK memuatnya
    $dirty = git status --porcelain 2>$null | Where-Object { $_ -notmatch '^\?\?' }
    if ($dirty) {
        Write-Host "  [WARN] ada perubahan yang BELUM di-commit; bundle tidak memuatnya:" -ForegroundColor Yellow
        $dirty | Select-Object -First 8 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkYellow }
        Write-Host "         commit dulu kalau perubahan itu penting." -ForegroundColor DarkYellow
    }
} finally { Pop-Location }

# --- 2. EA + preset, disalin UTUH supaya bisa dipakai tanpa meng-clone bundle ---
#     Saat memulihkan dalam keadaan panik, membuka satu folder jauh lebih cepat
#     daripada memasang git dulu. Isinya toh sudah ada di bundle juga.
$ea = Join-Path $root "mt5_ea"
if (Test-Path $ea) {
    Copy-Item $ea (Join-Path $dest "mt5_ea") -Recurse -Force
    Write-Host "  [ OK ] mt5_ea\  (EA + preset siap pakai)" -ForegroundColor Green
}

# --- 2b. dokumen & alat yang paling dibutuhkan saat memulihkan ---
foreach ($f in @("RECOVERY.md", "config.yaml", "requirements.txt")) {
    $src = Join-Path $root $f
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $dest $f) -Force
        Write-Host "  [ OK ] $f" -ForegroundColor Green
    }
}
$vs = Join-Path $root "tools\verify_system.py"
if (Test-Path $vs) {
    New-Item -ItemType Directory -Path (Join-Path $dest "tools") -Force | Out-Null
    Copy-Item $vs (Join-Path $dest "tools\verify_system.py") -Force
    Write-Host "  [ OK ] tools\verify_system.py" -ForegroundColor Green
}

# --- 3. .env kalau ada (RAHASIA) ---
$env_file = Join-Path $root ".env"
$has_env = Test-Path $env_file
if ($has_env) {
    Copy-Item $env_file (Join-Path $dest ".env") -Force
    Write-Host "  [ OK ] .env  << BERISI RAHASIA" -ForegroundColor Yellow
}

# --- 4. petunjuk pemulihan ---
@"
CARA MEMULIHKAN
===============

1. Install Python 3.11 dan MetaTrader 5 di mesin baru.

2. Pulihkan kode dari bundle:

       git clone quant-backup.bundle C:\Quant
       cd C:\Quant
       pip install -r requirements.txt

   (Bundle berisi SELURUH riwayat, jadi 'git log' tetap utuh dan kamu bisa
    mundur ke commit mana pun kalau ada yang rusak.)

3. Ikuti C:\Quant\RECOVERY.md dari langkah 2 dan seterusnya.

4. Kalau file .env ikut di folder ini, salin ke C:\Quant\.env
   Catatan: .env HANYA dipakai slot 'vision' yang saat ini semuanya mati.
   Sistem trading (eterna + Semi Marti) jalan tanpa itu.

5. Verifikasi sebelum ditinggal:

       python tools\verify_system.py


PERINGATAN
==========
$(if ($has_env) { "Folder ini BERISI .env dengan API key. Jangan unggah ke tempat publik,`njangan kirim lewat chat, jangan simpan di cloud yang dibagikan." } else { "Folder ini tidak berisi rahasia (.env tidak ada di repo)." })

Dibuat: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
"@ | Set-Content (Join-Path $dest "RESTORE.txt") -Encoding UTF8

Write-Host "  [ OK ] RESTORE.txt" -ForegroundColor Green

# --- 5. jadikan satu file ZIP, lalu buang foldernya ---
Write-Host ""
Write-Host "Mengompres..." -ForegroundColor Cyan
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $dest "*") -DestinationPath $zip -CompressionLevel Optimal

# Verifikasi ZIP-nya benar-benar bisa dibuka. Cadangan yang belum diuji bukan
# cadangan -- kalau file rusak, kita ingin tahu SEKARANG, bukan saat VPS hilang.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$entries = 0
try {
    $za = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $entries = $za.Entries.Count
    $za.Dispose()
} catch {
    Write-Host "  [FAIL] ZIP tidak bisa dibaca ulang: $_" -ForegroundColor Red
}

if ($entries -gt 0) {
    Remove-Item $dest -Recurse -Force
    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host ""
    Write-Host "SELESAI." -ForegroundColor White
    Write-Host "  $zip" -ForegroundColor Green
    Write-Host "  $mb MB, $entries file, terverifikasi bisa dibuka" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "ZIP gagal diverifikasi -- folder $dest DIBIARKAN sebagai cadangan." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Sekarang PINDAHKAN file itu KELUAR dari VPS ini." -ForegroundColor Yellow
Write-Host "Cadangan yang tersimpan di mesin yang sama tidak menolong kalau mesinnya hilang." -ForegroundColor Yellow
if ($has_env) {
    Write-Host ""
    Write-Host "ZIP ini berisi .env (RAHASIA). Jangan unggah ke tempat publik." -ForegroundColor Red
}
Write-Host ""
