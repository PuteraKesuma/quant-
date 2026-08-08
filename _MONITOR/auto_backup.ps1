# =====================================================================================
#  AUTO BACKUP — supaya keamanan kerja TIDAK bergantung pada ingatan siapa pun.
#
#  Latar: 2026-08-06 user kehilangan VPS beserta ~3 minggu kerja yang belum ter-commit.
#  Yang selamat cuma karena ada ZIP off-VPS yang kebetulan dibuat manual. Skrip ini
#  menghapus ketergantungan pada "kebetulan" itu.
#
#  Tiap kali jalan:
#    1. Commit otomatis apa pun yang belum ter-commit di C:\Quant (jangan sampai kerja
#       menganggur di working tree seperti 63 file riset yang nyaris hilang kemarin).
#    2. Buat git bundle SEGAR (seluruh riwayat, semua branch) ke Downloads.
#    3. Buat ZIP kode+riset+jurnal (TANPA folder data 1 GB yang bisa ditarik ulang
#       dari Dukascopy) ke Downloads.
#    4. Coba push ke GitHub. Kalau kredensial belum ada, dicatat sebagai PERINGATAN
#       supaya terlihat di jurnal — bukan gagal diam-diam.
#    5. Simpan hanya 5 cadangan terbaru; sisanya dibuang supaya disk tidak penuh.
#
#  PERINGATAN: ZIP berisi .env dengan ANTHROPIC_API_KEY asli. File ini untuk ditarik
#  ke komputer pribadi, JANGAN diunggah ke mana pun.
#
#  Jalankan manual:
#    powershell -ExecutionPolicy Bypass -File C:\Quant\_MONITOR\auto_backup.ps1
# =====================================================================================
$ErrorActionPreference = "SilentlyContinue"

$Git    = "C:\Program Files\Git\cmd\git.exe"
$Root   = "C:\Quant"
$Dest   = "C:\Users\Administrator\Downloads"
$Jurnal = "C:\Quant\_MONITOR\jurnal.md"
$Stage  = Join-Path $env:TEMP "quant_bk_stage"
$Keep   = 5

function NowUtc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }
function J($tag, $msg) {
    $line = "- **{0} UTC** {1} [backup] {2}" -f (NowUtc), $tag, $msg
    Add-Content -Path $Jurnal -Value $line -Encoding utf8
    Write-Host $line
}

$stamp = Get-Date -Format "yyyyMMdd-HHmm"
J "BK " "Mulai cadangan otomatis."

# ---------- 1. commit apa pun yang menganggur ----------
Push-Location $Root
$dirty = & $Git status --porcelain
if ($dirty) {
    $n = ($dirty | Measure-Object).Count
    & $Git add -A
    & $Git commit -q -m "auto-backup $stamp : simpan $n perubahan yang belum ter-commit"
    J "CMT" "$n perubahan otomatis di-commit (jangan biarkan kerja menganggur)."
}
$head = (& $Git rev-parse --short HEAD)

# ---------- 2. bundle ----------
$bundle = Join-Path $Dest "quant-backup-$stamp.bundle"
& $Git bundle create $bundle --all 2>&1 | Out-Null
if (Test-Path $bundle) {
    $mb = [math]::Round((Get-Item $bundle).Length / 1MB, 2)
    J "BK " "Bundle dibuat: $(Split-Path $bundle -Leaf) ($mb MB, HEAD $head)."
} else {
    J "ERR" "GAGAL membuat bundle."
}

# ---------- 3. ZIP kode (tanpa data 1 GB) ----------
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
robocopy $Root $Stage /E /XD data __pycache__ .pytest_cache .git /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
if (Test-Path $bundle) { Copy-Item $bundle (Join-Path $Stage "GIT-RIWAYAT-LENGKAP.bundle") -Force }
$zip = Join-Path $Dest "QUANT-BACKUP-$stamp.zip"
Compress-Archive -Path "$Stage\*" -DestinationPath $zip -CompressionLevel Optimal -Force
if (Test-Path $zip) {
    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 2)
    J "BK " "ZIP dibuat: $(Split-Path $zip -Leaf) ($mb MB). TARIK KE PC LOKAL."
}
Remove-Item $Stage -Recurse -Force

# ---------- 4. coba push GitHub ----------
$env:GIT_TERMINAL_PROMPT = "0"
$ahead = & $Git rev-list --count origin/vps-zrev-live..HEAD 2>$null
& $Git push origin HEAD:vps-zrev-live 2>&1 | Out-Null
$ahead2 = & $Git rev-list --count origin/vps-zrev-live..HEAD 2>$null
if ($ahead2 -eq "0") {
    J "OK " "Push GitHub BERHASIL - riwayat aman di luar VPS."
} else {
    J "WRN" "Push GitHub GAGAL (kredensial belum tersimpan). $ahead2 commit masih HANYA di VPS ini. Cadangan off-VPS bergantung pada ZIP di Downloads."
}
Pop-Location

# ---------- 5. buang cadangan lama ----------
foreach ($pat in @("quant-backup-*.bundle", "QUANT-BACKUP-*.zip")) {
    $old = Get-ChildItem $Dest -Filter $pat | Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep
    foreach ($f in $old) { Remove-Item $f.FullName -Force; J "CLN" "Cadangan lama dibuang: $($f.Name)" }
}

J "BK " "Selesai. HEAD $head."
