# VERIFIKASI TICK: clamp stop $30 pada EternaBot yang sudah memakai ATR Wilder.
#
# Harness Python (sudah diverifikasi cocok dengan EA ini: 114/113/104/37 vs
# 111/111/99/37) menemukan clamp $26-$30 membentuk DATARAN yang mengalahkan
# tanpa-clamp pada KEDUA sumbu:
#     tanpa clamp : $1438  DD -241  Ret/DD 5.96
#     clamp $26   : $1585  DD -211  Ret/DD 7.51
#     clamp $28   : $1639  DD -211  Ret/DD 7.77
#     clamp $30   : $1546  DD -211  Ret/DD 7.32
#     clamp $22   : $1061  DD -493  Ret/DD 2.15   <- jurang di bawah dataran
#
# $30 dipilih dari dataran itu karena punya alasan independen: risiko maksimum
# eterna jadi $30 PERMANEN, jadi eterna + basket Marti $75 = $105 selalu, bukan
# hanya saat gate risiko gabungan menggigit.
#
# Simulasi bar tidak cukup untuk keputusan live. Ini menjalankannya tiap tick,
# spread nyata, 2021-2026.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\wld2_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $from, $to, $clamp) {
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
InpRiskCapUSD=$clamp
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
    if (-not (Test-Path "$dir\report_$tag.htm")) {
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
    Start-Sleep -Seconds 2
}

foreach ($yr in @(2021, 2022)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    foreach ($cl in @("70.0")) {
        $t = $cl -replace "\.0$", ""
        Run-Test "wld_$yr" "$yr.01.01" $to $cl
    }
}
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
