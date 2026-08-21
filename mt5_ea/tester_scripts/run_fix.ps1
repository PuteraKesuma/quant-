# Jalankan ulang SETELAH perbaikan siklus handle indikator.
#
# Pola lama di kedua EA: iATR() -> CopyBuffer() -> IndicatorRelease(), semuanya di
# dalam OnTick, tiap tick. Handle indikator MT5 dihitung ASINKRON, jadi CopyBuffer
# tepat setelah pembuatan bisa mengembalikan buffer yang belum siap. ATR bernilai 0
# membuat SupertrendDirs melewati bar (a <= 0.0 -> continue) dan status band rusak.
#
# Gejalanya terukur: EternaBot 2026 mengambil 42 trade, sementara logika yang sama
# di Python hanya 27 -- dan entry-entry ekstra itu terjadi di bar yang MELANGGAR
# gate trennya sendiri (align=False). Diperiksa: bukan sumber data (duckdb vs
# broker sama saja), bukan panjang warmup (500/5000/penuh identik), bukan cara
# menyemai ATR (SMA vs ewm identik).
#
# Kalau setelah perbaikan jumlah trade turun mendekati 27, penyebabnya terkonfirmasi
# dan SEMUA angka EternaBot/EternaVote sebelumnya harus dibuang -- termasuk sweep
# risk cap 90->70 yang sudah terlanjur dipasang live.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\fix_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $expert, $from, $to, $extra) {
    $ini = "$dir\tester_$tag.ini"
    $body = @"
[Tester]
Expert=$expert
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
InpRiskCapUSD=70.0
InpDebug=false
InpHistBars=5000
$extra
"@
    $body | Out-File $ini -Encoding ascii
    Remove-Item "$dir\report_$tag.htm" -ErrorAction SilentlyContinue
    "[$tag] launching $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    Start-Process -FilePath "$dir\terminal64.exe" -ArgumentList "/portable", "/config:$ini" | Out-Null
    $deadline = (Get-Date).AddMinutes(30)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 15
        if (Test-Path "$dir\report_$tag.htm") { break }
    }
    if (Test-Path "$dir\report_$tag.htm") {
        "[$tag] DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    } else {
        "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8
    }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    # metatester64 adalah proses yang MENGHITUNG; terminal64 cuma induknya.
    # Membunuh induk saja meninggalkan agen yatim yang terus membakar CPU 100%
    # -- terjadi 2026-08-21, satu proses berjalan 68 menit setelah run di-timeout
    # dan memperlambat terminal LIVE. Difilter by Path supaya hanya menyentuh
    # instance portable di $dir, bukan MT5 yang dipakai trading.
    Get-Process metatester64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

foreach ($yr in @(2023, 2024, 2025, 2026)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    Run-Test "fixbot$yr"  "EternaBot"  "$yr.01.01" $to ""
    Run-Test "fixvote$yr" "EternaVote" "$yr.01.01" $to "InpThreshold=0.875"
}

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
