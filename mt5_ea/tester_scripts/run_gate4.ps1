# BUKTI: efek REGIME GATE di KEEMPAT tahun, bukan cuma dua.
#
# Sebelumnya gate hanya terukur di 2023 dan 2026 karena backtest M5 every-tick
# makan berjam-jam; 2024 dan 2025 timeout dan terpaksa memakai estimasi Python
# yang meleset +/-$160 -- terlalu kasar untuk keputusan yang menyangkut uang.
#
# Model=1 (1-menit OHLC) menyelesaikan satu run dalam ~3 menit, bukan berjam-jam.
# Angkanya tidak akan sama dengan hasil Model=0, jadi JANGAN dibandingkan silang.
# Yang sah adalah perbandingan di dalam file ini sendiri: gate ON vs OFF, tahun
# demi tahun, di model dan input yang persis sama.
#
# Pertanyaannya satu: apakah gate menolong hanya di 2023, atau juga di tahun lain?
# Kalau dia merugikan di 2024 DAN 2025 seperti dugaan estimasi, preminya nyata dan
# harus disebut apa adanya sebelum akun live dipakai.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\gate4_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $expert, $from, $to, $model) {
    $gate = if ($tag -like "*off*") { "false" } else { "true" }
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
InpUseRegimeGate=$gate
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
foreach ($yr in @(2023, 2024, 2025, 2026)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    Run-Test "g4on_$yr"  "SemiMartiV10_Gated" "$yr.01.01" $to 1
    Run-Test "g4off_$yr" "SemiMartiV10_Gated" "$yr.01.01" $to 1
}

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
