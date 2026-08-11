# =====================================================================================
#  PASANG KREDENSIAL GITHUB - supaya auto_backup.ps1 bisa push tanpa tangan manusia.
#
#  CATATAN ENCODING: pure ASCII, alasannya sama dengan watchdog_shadow.ps1.
#
#  KENAPA TIDAK PAKAI GIT CREDENTIAL MANAGER:
#  GCM 2.9.0 di VPS ini sudah dua kali mengecewakan. 2026-08-08 dia macet setelah
#  CreateClient tanpa terminal interaktif. 2026-08-10 token yang tersimpan HILANG
#  begitu saja dari Windows Credential Manager (cmdkey /list kosong), dan akibatnya
#  auto-backup gagal push diam-diam sementara semua status lain hijau. Untuk sistem
#  yang tujuan utamanya "jangan sampai kerja hilang lagi", helper yang bisa lupa
#  sendiri adalah pilihan yang salah.
#
#  KONSEKUENSI YANG HARUS DISADARI:
#  Token disimpan sebagai TEKS BIASA di C:\Quant\.git\gh-credentials. Siapa pun yang
#  bisa membaca file itu bisa push/pull ke repo. Mitigasinya:
#    - ACL dikunci: pewarisan dimatikan, hanya Administrators + SYSTEM yang boleh baca
#    - letaknya DI DALAM .git, yang di-exclude robocopy saat membuat ZIP backup, jadi
#      token tidak ikut tersebar ke ZIP di Downloads
#    - scope token cuma `repo`
#  Kalau token bocor: cabut di https://github.com/settings/applications lalu jalankan
#  ulang device flow.
#
#  Jalankan (butuh file token di %TEMP%\gh_token.txt hasil device flow):
#    powershell -ExecutionPolicy Bypass -File C:\Quant\_MONITOR\pasang_kredensial_github.ps1
# =====================================================================================
$ErrorActionPreference = "Stop"

$Git      = "C:\Program Files\Git\cmd\git.exe"
$Root     = "C:\Quant"
$CredFile = "C:\Quant\.git\gh-credentials"
$TokFile  = Join-Path $env:TEMP "gh_token.txt"
$User     = "PuteraKesuma"

if (-not (Test-Path $TokFile)) { throw "Token tidak ada di $TokFile - jalankan device flow dulu." }
$tok = (Get-Content $TokFile -Raw).Trim()
if ($tok.Length -lt 20) { throw "Isi token tidak masuk akal (panjang $($tok.Length))." }

# ---------- 1. tulis file kredensial ----------
Set-Content -Path $CredFile -Value ("https://{0}:{1}@github.com" -f $User, $tok) -Encoding ascii -NoNewline
Write-Host "  [ OK ] File kredensial ditulis: $CredFile"

# ---------- 2. kunci izinnya ----------
& icacls $CredFile /inheritance:r /grant:r "BUILTIN\Administrators:(F)" "NT AUTHORITY\SYSTEM:(F)" | Out-Null
Write-Host "  [ OK ] ACL dikunci: hanya Administrators + SYSTEM."

# ---------- 3. arahkan git ke helper file, khusus github.com ----------
# Nilai kosong lebih dulu = RESET daftar helper untuk host ini. Tanpa baris itu, GCM
# tetap ikut antre di depan dan bisa menggantung lagi seperti 2026-08-08.
Push-Location $Root
& $Git config credential.https://github.com.helper ""
& $Git config --add credential.https://github.com.helper "store --file=C:/Quant/.git/gh-credentials"
$helpers = & $Git config --get-all credential.https://github.com.helper
Pop-Location
Write-Host "  [ OK ] Helper git untuk github.com: $($helpers -join ' | ')"

Write-Host ""
Write-Host "  Uji push sekarang dengan: powershell -File C:\Quant\_MONITOR\auto_backup.ps1"
Write-Host ""
