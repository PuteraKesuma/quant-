# Sweep RISK CAP di keempat tahun -- memperdalam satu-satunya pengungkit yang terbukti.
#
# Cap adalah bentuk SKIP: trade yang stop strukturnya lebih lebar dari cap TIDAK
# diambil sama sekali. Beda dari clamp, yang tetap masuk dengan stop dipendekkan.
# Clamp sudah diuji dan kalah di semua nilai; cap 90->70 justru MENANG (+$282 dan
# drawdown lebih rendah), tapi optimumnya belum pernah dicari di keempat tahun.
#
# Mekanismenya masuk akal: stop eterna = rentang 16 bar terakhir, jadi stop yang
# sangat lebar berarti bar-bar terakhir kacau -- sinyal berkualitas rendah. Cap
# menyaringnya. Dan karena cap bersifat dollar TETAP, dia otomatis tidak menggigit
# saat volatilitas rendah (2023: stop terlebar hanya ~$19.6) dan menggigit sering
# saat volatilitas tinggi (2026) -- membatasi persis ketika perlu.
#
# Acuan cap $70 sudah ada sebagai report_cl0_<tahun>:
#     2023 +20.08 | 2024 +57.35 | 2025 +273.44 | 2026 +725.99  = +1076.86
#     DD tahun-terburuk 23.73%
#
# ATURAN KEPUTUSAN, DIKUNCI SEBELUM HASIL:
#   adopsi hanya jika total net NAIK, DD tahun-terburuk TIDAK memburuk, dan
#   nilainya berdiri di DATARAN. Menang hanya di satu tahun = ditolak (itu yang
#   membunuh TP 3.0: total tertinggi, tapi seluruh keunggulannya dari 2025 saja).
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\cap4y_progress.txt"
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

foreach ($yr in @(2023, 2024, 2025, 2026)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    foreach ($cl in @("40.0", "50.0", "60.0")) {
        $t = $cl -replace "\.0$", ""
        Run-Test "cap${t}_$yr" "$yr.01.01" $to $cl
    }
}
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
