# UJI: filter mana yang sebenarnya menahan Semi Marti dari trade harian?
#
# Pemilik akun ingin Semi Marti trade tiap hari. Pengukuran menunjukkan itu TIDAK
# tercapai bahkan dengan gate dimatikan total -- tanpa gate pun dia cuma aktif
# 52% hari kerja (2023) dan 67% (2026). Jadi yang membatasi bukan gate saja.
#
# Tersangka lain, dan dua-duanya input EA:
#   InpUseNewsFilter      blokir 60 menit sebelum + 30 menit sesudah berita USD
#   InpRequireBreakConfirm wajib ada pullback lalu tembus ulang sebelum entry
#
# Gate dibiarkan di setelan LONGGAR yang baru terverifikasi (e1.0/t6.0, 2023 +$55).
# Yang diubah hanya satu filter per run, supaya jelas mana yang menyumbang apa.
# 2023 dipilih karena di situlah kelonggaran paling berbahaya -- kalau sebuah
# filter aman dimatikan, dia harus aman di tahun terburuk.
#
# Model=1, sama seperti pembanding lg60_2023 (+$55.49). JANGAN dibandingkan
# dengan angka Model=0.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\filter_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $expert, $from, $to, $model) {
    $gate = "true"
    $me = "1.0"
    $mt = "6.0"
    $news  = if ($tag -like "*nonews*" -or $tag -like "*both*") { "false" } else { "true" }
    $brk   = if ($tag -like "*nobrk*"  -or $tag -like "*both*") { "false" } else { "true" }
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
InpUseNewsFilter=$news
InpRequireBreakConfirm=$brk
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
Run-Test "f_nonews_2023" "SemiMartiV10_Gated" "2023.01.01" "2023.12.31" 1
Run-Test "f_nobrk_2023"  "SemiMartiV10_Gated" "2023.01.01" "2023.12.31" 1
Run-Test "f_both_2023"   "SemiMartiV10_Gated" "2023.01.01" "2023.12.31" 1

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
