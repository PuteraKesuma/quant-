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
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dest = Join-Path ([Environment]::GetFolderPath("Desktop")) "QUANT-BACKUP_$stamp"

New-Item -ItemType Directory -Path $dest -Force | Out-Null
Write-Host "Folder cadangan: $dest" -ForegroundColor Cyan
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

# --- 2. preset (dipisah supaya bisa langsung dipakai) ---
$pr = Join-Path $root "mt5_ea\presets"
if (Test-Path $pr) {
    Copy-Item $pr (Join-Path $dest "presets") -Recurse -Force
    Write-Host "  [ OK ] presets\" -ForegroundColor Green
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
Write-Host ""
Write-Host "SELESAI." -ForegroundColor White
Write-Host ""
Write-Host "Sekarang PINDAHKAN folder itu KELUAR dari VPS ini." -ForegroundColor Yellow
Write-Host "Cadangan yang tersimpan di mesin yang sama tidak menolong kalau mesinnya hilang." -ForegroundColor Yellow
if ($has_env) {
    Write-Host ""
    Write-Host "Folder ini berisi .env (RAHASIA). Jangan unggah ke tempat publik." -ForegroundColor Red
}
Write-Host ""
