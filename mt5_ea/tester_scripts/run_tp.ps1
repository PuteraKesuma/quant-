# Sweep TP ratio -- membiarkan pemenang BERLARI, tanpa menyentuh stop.
#
# KENAPA ARAH INI
# Empat percobaan memendekkan stop (clamp 20/25/30/40/50 dan SKIP) semuanya kalah,
# pada profit MAUPUN drawdown, karena stop eterna = rentang 16 bar terakhir dan
# sudah menyesuaikan volatilitas sendiri. Arah sebaliknya belum pernah diuji:
# biarkan stopnya apa adanya, panjangkan TARGETnya.
#
# Ini masuk akal secara mekanis, bukan sekadar coba-coba: docstring EternaStrategy
# mencatat profit eterna SANGAT terkonsentrasi -- 10 trade terbaik (1.7%) memberi
# 85% dari seluruh untung, win rate cuma ~37%. Strategi yang hidup dari sedikit
# pemenang besar dirugikan oleh target yang memotong pemenang itu. Kalau benar,
# menaikkan rasio harus MENAIKKAN net meski win rate turun.
#
# 3.0 ikut diuji supaya terlihat bentuk kurva di KEDUA sisi 4.0. Kalau 4.0 ternyata
# di lereng (3.0 buruk, 5.0+ bagus) itu beda artinya dengan 4.0 di puncak sendirian.
#
# Rasio 4.0 tidak dijalankan ulang -- sudah ada sebagai report_cl0_<tahun>
# (InpMaxSLDist=0, TP ratio default 4.0):
#     2023 +20.08 | 2024 +57.35 | 2025 +273.44 | 2026 +725.99  = +1076.86
#     DD tahun-terburuk 23.73%
#
# ATURAN KEPUTUSAN, DIKUNCI SEBELUM HASIL:
#   adopsi hanya jika total net NAIK, DD tahun-terburuk TIDAK memburuk, dan
#   nilainya berdiri di DATARAN (tetangganya ikut bagus). Puncak sendirian ditolak.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\tp_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $from, $to, $ratio) {
    $ini = "$dir\tester_$tag.ini"
@"
[Tester]
Expert=EternaBot
Symbol=XAUUSD
Period=H1
Model=0
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
InpLot=0.01
InpMaxSLDist=0.0
InpTPRatio=$ratio
InpRiskCapUSD=70.0
InpDebug=false
InpHistBars=5000
"@ | Out-File $ini -Encoding ascii
    Remove-Item "$dir\report_$tag.htm" -ErrorAction SilentlyContinue
    "[$tag] $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    Start-Process -FilePath "$dir\terminal64.exe" -ArgumentList "/portable", "/config:$ini" | Out-Null
    $deadline = (Get-Date).AddMinutes(20)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 12
        if (Test-Path "$dir\report_$tag.htm") { break }
    }
    if (-not (Test-Path "$dir\report_$tag.htm")) { "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8 }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    # metatester64 adalah proses yang MENGHITUNG; terminal64 cuma induknya.
    # Membunuh induk saja meninggalkan agen yatim yang terus membakar CPU 100%
    # -- terjadi 2026-08-21, satu proses berjalan 68 menit setelah run di-timeout
    # dan memperlambat terminal LIVE. Difilter by Path supaya hanya menyentuh
    # instance portable di $dir, bukan MT5 yang dipakai trading.
    Get-Process metatester64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

foreach ($yr in @(2023, 2024, 2025, 2026)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    foreach ($r in @("3.0", "5.0", "6.0", "7.0")) {
        $t = $r -replace "\.0$", ""
        Run-Test "tp${t}_$yr" "$yr.01.01" $to $r
    }
}
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
