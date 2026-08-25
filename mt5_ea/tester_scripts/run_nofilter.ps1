# RISET: jendela jam Indonesia vs jendela server yang sedang dipakai.
#
# Filter jam EA memakai TimeCurrent() = waktu SERVER broker (UTC+3), bukan waktu
# lokal. Jadi setelan 9-23 yang berjalan sekarang sebenarnya:
#     server 09:00-23:00  =  WITA 14:00-04:00  =  WIB 13:00-03:00
# Pemilik akun mengira itu jam 09:00-23:00 Indonesia. Selisihnya 5 jam, dan yang
# lebih penting: jendela server mencakup London+New York, sedangkan jendela WITA
# 09-23 (= server 04:00-18:00) menggeser ke Asia+London dan melewatkan sebagian
# besar New York. Ini bukan menggeser angka -- ini mengganti sesi pasar.
#
# Keputusan pemilik: BIARKAN yang sekarang, riset saja yang jam Indonesia.
# Jadi tidak ada yang disentuh di live; ini murni pengukuran.
#
# Gate OFF di semua run, sesuai setelan live sekarang. Yang berbeda hanya jamnya.
# Model=1 supaya selesai ~11 menit/run; pembandingnya (g4off_2023, -$627.24)
# dijalankan di model yang sama.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\nofilter_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $expert, $from, $to, $model) {
    $gate = "false"
    $sh = "0"
    $eh = "23"
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
InpStartHour=$sh
InpEndHour=$eh
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
Run-Test "nf_2023" "SemiMartiV10_Gated" "2023.01.01" "2023.12.31" 1
Run-Test "nf_2026" "SemiMartiV10_Gated" "2026.01.01" "2026.08.18" 1


"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
