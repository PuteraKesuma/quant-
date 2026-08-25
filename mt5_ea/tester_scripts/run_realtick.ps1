# BACKTEST TICK ASLI (Model=4) + latensi 100 ms -- konfigurasi LIVE apa adanya.
#
# KENAPA DIULANG DENGAN MODEL INI
# Semua run beberapa jam terakhir memakai Model=1 (OHLC 1-menit) yang saya pilih
# demi kecepatan. Pemilik akun benar bahwa itu tidak realistis. Diukur di periode
# yang sama persis (Juli 2026, EA sama, delay sama, 53 trade di kedua run):
#     Model 4 tick asli : +$136.43  PF 1.87  DD  7.67%
#     Model 1 OHLC      :  +$46.94  PF 1.23  DD 11.75%
# Model=1 meremehkan profit 65% dan melebihkan drawdown 53%. Jumlah trade identik,
# jadi sinyalnya sama -- yang berbeda murni kualitas eksekusi. Arah perbandingan
# antar-baris Model=1 tetap sah, tapi angka mutlaknya menyesatkan dan tidak boleh
# dipakai untuk keputusan yang menyangkut uang.
#
# ExecutionMode=100 adalah ASUMSI latensi VPS-ke-broker 100 ms, bukan hasil
# pengukuran. Konservatif untuk VPS; kalau angka sebenarnya diketahui, ubah di sini.
#
# Setelan mengikuti LIVE: gate OFF, jam server 9-23, TP $40 / SL $75, lot 0.01.
#
# LAMBAT: ~30 menit per bulan, jadi ~6 jam per tahun, ditambah unduhan tick
# (Juli 2026 saja 52 MB). Deadline dibuat 8 jam per run dan pembersihan proses
# TIDAK boleh berjalan sebelum report ada -- pembatalan di tengah unduhan tick
# menghasilkan report kosong, dan itu sudah terjadi sekali hari ini.
#
# Dua tahun yang menentukan:
#   2026 = tahun terbaik  -> potensi sebenarnya
#   2023 = tahun terburuk -> risiko sebenarnya
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\realtick_progress.txt"
"START $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $from, $to) {
    $ini = "$dir\tester_$tag.ini"
@"
[Tester]
Expert=SemiMartiV10_Gated
Symbol=XAUUSD
Period=M5
Model=4
Optimization=0
FromDate=$from
ToDate=$to
ForwardMode=0
Deposit=1000
Currency=USD
Leverage=1:100
ExecutionMode=100
Visual=0
Report=report_$tag
ReplaceReport=1
ShutdownTerminal=1

[TesterInputs]
InpGlobalTP_USD=40
InpGlobalSL_USD=75
InpDebug=false
InpUseRegimeGate=false
InpStartHour=9
InpEndHour=23
"@ | Out-File $ini -Encoding ascii
    Remove-Item "$dir\report_$tag.htm" -ErrorAction SilentlyContinue

    "[$tag] mulai $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    Start-Process -FilePath "$dir\terminal64.exe" -ArgumentList "/portable", "/config:$ini" | Out-Null

    $deadline = (Get-Date).AddHours(8)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 60
        if (Test-Path "$dir\report_$tag.htm") { break }
    }
    if (Test-Path "$dir\report_$tag.htm") {
        "[$tag] SELESAI $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    } else {
        "[$tag] TIMEOUT 8 jam" | Out-File $log -Append -Encoding utf8
    }

    # bersihkan HANYA setelah report ada / deadline lewat -- lihat catatan di atas
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process metatester64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
}

Run-Test "rt_2026" "2026.01.01" "2026.08.18"
Run-Test "rt_2023" "2023.01.01" "2023.12.31"

"ALL DONE $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File $log -Append -Encoding utf8
