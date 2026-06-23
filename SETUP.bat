@echo off
setlocal
title SETUP - jalankan SEKALI di mesin / VPS baru
cd /d "%~dp0"

echo ============================================================
echo   SETUP ORB TRADING - mesin / VPS baru
echo ============================================================
echo.

rem --- 1. cek Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Python tidak ditemukan.
    echo        Install Python 3.11+ dari https://www.python.org/downloads/
    echo        dan centang "Add Python to PATH" saat install.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version') do echo [ OK ] %%v

rem --- 2. install dependencies ---
echo.
echo  Meng-install dependencies (requirements.txt)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [FAIL] Gagal install dependencies. Cek koneksi internet.
    pause
    exit /b 1
)

rem --- 3. siapkan .env ---
echo.
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [ OK ] Dibuat file .env dari template.
    echo        ^>^>^> SEKARANG EDIT .env dan isi ANTHROPIC_API_KEY kamu! ^<^<^<
) else (
    echo [ OK ] File .env sudah ada (tidak ditimpa).
)

echo.
echo ============================================================
echo   SETUP SELESAI. Checklist terakhir di VPS:
echo ============================================================
echo   1. Edit .env  -^>  isi ANTHROPIC_API_KEY
echo   2. Install MetaTrader 5, login akun, Algo Trading ON
echo   3. MT5: Tools ^> Options ^> Expert Advisors ^> izinkan
echo      WebRequest untuk URL  http://127.0.0.1:8000
echo   4. Pasang EA SignalExecutor di chart NAS100 + XAUUSD
echo   5. Double-click START_TRADING.bat
echo ============================================================
echo.
echo  Cek instalasi sekarang? (jalankan pre-flight)
python -m pipeline.live.preflight
echo.
pause
endlocal
