# UJI: apakah EA saya (SemiMartiV10_Gated) masih setara EA ASLI vendor?
#
# Pemilik akun menemukan bahwa angka +$948 yang sering saya kutip berasal dari
# Expert=SemiMartiV10 (EA asli) Model=0, sementara semua run terbaru saya memakai
# Expert=SemiMartiV10_Gated Model=1. DUA variabel berubah sekaligus, jadi selisih
# +$948 vs +$362 tidak bisa dikaitkan ke salah satunya.
#
# Bukti ekuivalensi yang saya punya sebelumnya membandingkan Gated-lama vs
# Gated-baru (118 deal identik). Itu TIDAK membuktikan Gated == asli.
#
# Di sini hanya EA yang berbeda: model, periode, dan semua input sama, gate OFF.
# Kalau hasilnya identik, modifikasi saya memang netral saat gate mati dan
# perbandingan-perbandingan sebelumnya tetap sah. Kalau berbeda, seluruh angka
# yang saya hasilkan dengan Gated harus dibaca ulang.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\asli_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $expert, $from, $to, $model) {
    $gate = "false"
    $sh = "9"
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
Run-Test "asli_2026" "SemiMartiV10" "2026.01.01" "2026.08.18" 1

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
