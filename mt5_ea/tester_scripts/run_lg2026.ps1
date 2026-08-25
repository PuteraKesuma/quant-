# UJI: gate yang lebih LONGGAR -- permintaan pemilik akun (Semi Marti terlalu diam).
#
# Gate sekarang (entry x1.8 / tren x3.8) memblokir 67% waktu. Estimasi tingkat-basket
# menunjukkan setelan LEBIH LONGGAR justru lebih baik di 2023 DAN di total:
#     e1.8 t3.8 (sekarang) : blokir 67%, 2023 +$4,   total $862
#     e1.8 t5.0            : blokir 62%, 2023 +$143, total $1144
#     e1.0 t6.0            : blokir 55%, 2023 +$164, total $1268
# Tapi estimasi itu bergalat +/-$160 dan dipilih dari 7 kandidat, jadi belum layak
# dipakai. 2023 adalah tahun ujiannya -- di situlah gate harus membuktikan diri.
#
# Model=1 supaya selesai ~11 menit, bukan berjam-jam. SAH karena pembandingnya
# (g4on_2023, gate sekarang, Model=1, -$229.54) dijalankan di model yang sama.
# JANGAN bandingkan dengan angka Model=0 mana pun.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\lg2026_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $expert, $from, $to, $model) {
    $gate = "true"
    $me = if ($tag -like "*lg60*") { "1.0" } else { "1.8" }
    $mt = if ($tag -like "*lg60*") { "6.0" } else { "3.8" }
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
InpGateMultEntry=$me
InpGateMultTrend=$mt
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
Run-Test "lg60_2026" "SemiMartiV10_Gated" "2026.01.01" "2026.08.18" 1
Run-Test "cur_2026"  "SemiMartiV10_Gated" "2026.01.01" "2026.08.18" 1

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
