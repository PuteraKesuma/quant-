# Lengkapi bukti REGIME GATE Semi Marti: 2024 dan 2025 (2023 & 2026 sudah ada).
#
# KENAPA INI YANG DIKERJAKAN
# Kelemahan portofolio ternyata terkonsentrasi di SATU tahun, bukan tersebar:
#     2023  eterna +43   Semi Marti -468   -> gabungan -425
#     2024  eterna +242  Semi Marti +129
#     2025  eterna +281  Semi Marti +619
#     2026  eterna +665  Semi Marti +948
# Semi Marti bukan pelengkap tahun datar eterna -- keduanya gagal di 2023 yang
# sama. Jadi memperbaiki 2023 bernilai jauh lebih besar daripada mencari
# strategi ketiga.
#
# Gate sudah diukur di dua tahun dan hasilnya persis bentuk yang dicari:
#     2023  -468 DD 54.8%  ->  -164 DD 27.2%   (rugi -$305, DD dibelah dua)
#     2026  +948 PF 1.87   ->  +670 PF 3.91    (profit -$278, PF hampir 2x)
# Impas di profit, jauh lebih aman di risiko. Tapi dua tahun bukan bukti --
# empat hipotesis mati hari ini karena bagus di satu tahun saja.
#
# ATURAN KEPUTUSAN, DIKUNCI SEBELUM HASIL:
#   pasang gate hanya jika DD tahun-terburuk turun JELAS dan total 4 tahun tidak
#   turun lebih dari 15%. Kalau gate merusak 2024 atau 2025 seperti dia merusak
#   2026, biayanya terlalu mahal untuk perlindungan satu tahun.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\gate4y_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $from, $to, $gate) {
    $ini = "$dir\tester_$tag.ini"
@"
[Tester]
Expert=SemiMartiV10_Gated
Symbol=XAUUSD
Period=M5
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
InpGlobalTP_USD=40
InpGlobalSL_USD=75
InpDebug=false
InpUseRegimeGate=$gate
"@ | Out-File $ini -Encoding ascii
    Remove-Item "$dir\report_$tag.htm" -ErrorAction SilentlyContinue
    "[$tag] $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    Start-Process -FilePath "$dir\terminal64.exe" -ArgumentList "/portable", "/config:$ini" | Out-Null
    $deadline = (Get-Date).AddMinutes(40)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 20
        if (Test-Path "$dir\report_$tag.htm") { break }
    }
    if (-not (Test-Path "$dir\report_$tag.htm")) { "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8 }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

Run-Test "2024_gate" "2024.01.01" "2024.12.31" "true"
Run-Test "2025_gate" "2025.01.01" "2025.12.31" "true"

"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
