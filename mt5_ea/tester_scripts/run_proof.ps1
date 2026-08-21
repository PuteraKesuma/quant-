# BUKTI: perbaikan basket-P&L Semi Marti tidak mengubah apa pun saat EA sendirian.
#
# MASALAHNYA SELAMA INI
# Backtest M5 every-tick (Model=0) makan berjam-jam; tiga percobaan timeout
# (70 menit untuk setahun, 25 menit untuk 6 minggu). Jadi perbaikan itu sempat
# terpasang live TANPA pernah diuji -- dan penalaran saya sendiri sudah terbukti
# bisa salah hari ini (cacat trailing yang saya buat, lalu saya temukan sendiri).
#
# CARANYA
# Bukan menunggu lebih lama, tapi mengubah cara menguji: Model=1 (1-menit OHLC)
# jauh lebih cepat, dan perbandingannya tetap SAH karena versi LAMA dan BARU
# dijalankan di model, periode, dan input yang persis sama. Yang diuji bukan
# "berapa profitnya" tapi "apakah keduanya berperilaku sama".
#
# APA YANG DIHARAPKAN
# Di tester EA berjalan SENDIRIAN, jadi equity-balance (versi lama) secara
# matematis identik dengan MyFloatingPnL() (versi baru): semua posisi adalah
# miliknya. Maka hasilnya HARUS sama persis. Kalau berbeda, berarti perbaikan
# saya menyentuh sesuatu di luar niat -- dan itu harus ketahuan SEKARANG, bukan
# setelah uang sungguhan masuk.
#
# Satu perbedaan memang DIHARAPKAN ada di logika trailing: versi lama tidak kebal
# terhadap penutupan leg, versi baru mem-baseline ulang puncaknya. Kalau muncul
# selisih, di situlah tempatnya -- dan arahnya harus MENGUNTUNGKAN versi baru
# (basket tidak lagi ditutup paksa saat leg #1 kena TP tetap $10).
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\proof_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $expert, $from, $to, $model) {
    $ini = "$dir\tester_$tag.ini"
@"
[Tester]
Expert=$expert
Symbol=XAUUSD
Period=M5
Model=$model
Optimization=0
FromDate=$from
ToDate=$to
ForwardMode=0
Deposit=1000
Currency=USD
Leverage=1:100
ExecutionMode=0
Visual=0
Report=report_$tag
ReplaceReport=1
ShutdownTerminal=1

[TesterInputs]
InpGlobalTP_USD=40
InpGlobalSL_USD=75
InpDebug=false
InpUseRegimeGate=true
"@ | Out-File $ini -Encoding ascii
    Remove-Item "$dir\report_$tag.htm" -ErrorAction SilentlyContinue
    "[$tag] $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    Start-Process -FilePath "$dir\terminal64.exe" -ArgumentList "/portable", "/config:$ini" | Out-Null
    $deadline = (Get-Date).AddMinutes(45)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        if (Test-Path "$dir\report_$tag.htm") { break }
    }
    if (Test-Path "$dir\report_$tag.htm") {
        "[$tag] DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    } else {
        "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8
    }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process metatester64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

# Q1 2026 -- cukup panjang untuk menghasilkan puluhan basket, cukup pendek untuk selesai.
Run-Test "proofOLD" "SemiMartiOLD"       "2026.01.01" "2026.04.01" 1
Run-Test "proofNEW" "SemiMartiV10_Gated" "2026.01.01" "2026.04.01" 1

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
