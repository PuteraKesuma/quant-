# ============================================================
#  bootstrap_vps.ps1  —  "tinggal tempel sekali" di VPS baru
#  Jalankan di PowerShell VPS (Administrator):
#     irm https://raw.githubusercontent.com/PuteraKesuma/quant-/main/bootstrap_vps.ps1 | iex
#
#  Otomatis:  install Python -> download kode -> install dependency -> buat .env
#  Sisa manual (broker-specific): install + login MT5, pasang EA.
# ============================================================

$ErrorActionPreference = "Stop"
$InstallDir = "C:\Quant"          # lokasi kode di VPS (path pendek, tanpa OneDrive)
$RepoZip    = "https://github.com/PuteraKesuma/quant-/archive/refs/heads/main.zip"
$PyVersion  = "3.11.9"
$PyUrl      = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-amd64.exe"

function Say($m) { Write-Host "`n>>> $m" -ForegroundColor Cyan }
function Ok ($m) { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[WARN] $m" -ForegroundColor Yellow }

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SETUP OTOMATIS ORB TRADING di VPS" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# --- 1. Python ---------------------------------------------------
Say "Cek Python..."
$python = $null
try { $null = & python --version 2>$null; if ($LASTEXITCODE -eq 0) { $python = "python" } } catch {}
if (-not $python) {
    Say "Python belum ada — meng-install $PyVersion (silent)..."
    $tmp = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest -Uri $PyUrl -OutFile $tmp
    Start-Process -FilePath $tmp -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    # refresh PATH di sesi ini
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
    try { $null = & python --version 2>$null; if ($LASTEXITCODE -eq 0) { $python = "python" } } catch {}
    if (-not $python) { Warn "Python ter-install tapi PATH belum kebaca. TUTUP PowerShell, buka lagi sebagai Administrator, jalankan ulang baris irm tadi."; return }
}
Ok ("Python: " + (& python --version))

# --- 2. Download kode (ZIP, tanpa perlu git) ---------------------
Say "Mengunduh kode dari GitHub..."
$zip = Join-Path $env:TEMP "quant-main.zip"
Invoke-WebRequest -Uri $RepoZip -OutFile $zip
$extractTmp = Join-Path $env:TEMP "quant-extract"
if (Test-Path $extractTmp) { Remove-Item $extractTmp -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $extractTmp -Force
$srcDir = Get-ChildItem $extractTmp -Directory | Select-Object -First 1   # quant--main

# pindahkan ke C:\Quant (jaga .env lama kalau sudah ada)
$envBackup = $null
if (Test-Path (Join-Path $InstallDir ".env")) {
    $envBackup = Get-Content (Join-Path $InstallDir ".env") -Raw
    Warn ".env lama ditemukan — akan dipertahankan."
}
if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
Move-Item $srcDir.FullName $InstallDir
Remove-Item $zip,$extractTmp -Recurse -Force -ErrorAction SilentlyContinue
Ok "Kode tersimpan di $InstallDir"

Set-Location $InstallDir

# --- 3. Install dependency --------------------------------------
Say "Meng-install dependency Python (bisa beberapa menit)..."
& python -m pip install --upgrade pip
& python -m pip install -r (Join-Path $InstallDir "requirements.txt")
Ok "Dependency selesai."

# --- 4. Siapkan .env -------------------------------------------
Say "Menyiapkan file .env..."
$envPath = Join-Path $InstallDir ".env"
if ($envBackup) {
    Set-Content -Path $envPath -Value $envBackup -Encoding utf8
    Ok ".env lama dikembalikan (API key kamu aman)."
} else {
    Copy-Item (Join-Path $InstallDir ".env.example") $envPath -Force
    Ok ".env dibuat dari template — perlu diisi API key."
}

# --- 5. Selesai -------------------------------------------------
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  INSTALASI OTOMATIS SELESAI" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host @"

SISA 3 LANGKAH MANUAL (sekali saja):

  1. ISI API KEY
     Notepad akan terbuka. Ganti baris ANTHROPIC_API_KEY= dengan
     key kamu (sk-ant-api03-...), lalu Save & tutup.
     (Lewati kalau tidak pakai slot vision.)

  2. MT5 (install + login + izinkan koneksi)
     - Install MetaTrader 5 dari FBS, login akun, klik Algo Trading ON.
     - Tools > Options > Expert Advisors > centang Allow WebRequest,
       tambah URL:  http://127.0.0.1:8000
     - Pasang EA SignalExecutor di chart NAS100 (US100) dan XAUUSD.

  3. PLAY
     Buka folder $InstallDir, double-click  START_TRADING.bat

"@ -ForegroundColor White

Start-Process notepad.exe $envPath
Ok "Buka folder kode..."
Start-Process explorer.exe $InstallDir
