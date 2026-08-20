# Verifikasi tick-sungguhan: EternaVote (>=7/8 suara) vs EternaBot (tunggal, live).
#
# research/eterna_ensemble_final.py (simulasi bar Python) menemukan voting >=7/8
# mengalahkan konfigurasi tunggal: net $2325 vs $1699, Ret/DD 4.80 vs 3.97, dan
# berdiri di DATARAN (6/8, 7/8, 8/8 semua serupa) bukan puncak tunggal.
#
# Simulasi bar tidak cukup untuk keputusan deploy. Skrip ini menjalankan logika
# yang sama lewat mesin yang sama dengan Semi Marti: tiap tick, spread nyata,
# urutan fill nyata.
#
# 2024 SENGAJA disertakan: di situ Python bilang voting KALAH ($83 vs $204).
# Menguji hanya tahun yang menang bukan verifikasi, itu seleksi. Kalau tester
# juga menunjukkan voting kalah di 2024, itu justru bukti kedua sisi sepakat.
#
# Baseline EternaBot cap $70 yang sudah ada: 2023 +20.08 | 2025 +273.44 | 2026 +725.99
# jadi hanya 2024 yang perlu dijalankan ulang untuk EternaBot.
#
# Menunggu FILE REPORT, bukan hilangnya proses -- agen tester butuh puluhan detik
# untuk muncul, dan menganggap "proses belum ada" = "selesai" pernah membunuh run
# di tengah jalan (bug run_2025.ps1).
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\vote_progress.txt"
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
        Start-Sleep -Seconds 20
        if (Test-Path "$dir\report_$tag.htm") { break }
    }
    if (Test-Path "$dir\report_$tag.htm") {
        "[$tag] DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    } else {
        "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8
    }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

# EternaVote: ambang 0.875 = >=7/8 suara bersih (tengah dataran)
foreach ($yr in @(2023, 2024, 2025, 2026)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    Run-Test "vote$yr" "EternaVote" "$yr.01.01" $to "InpThreshold=0.875"
}

# EternaBot 2024 -- satu-satunya baseline yang belum ada
Run-Test "bot2024" "EternaBot" "2024.01.01" "2024.12.31" ""

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
