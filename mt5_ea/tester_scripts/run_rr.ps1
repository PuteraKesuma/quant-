# Spesifikasi yang diminta: SL $25-30, TP $70-100.
#
# Tidak perlu input baru -- TP = InpTPRatio x jarak stop yang DIPAKAI, dan
# InpMaxSLDist memaksa jarak itu. Jadi kombinasinya:
#     SL 25 x rasio 2.8   -> TP  70
#     SL 25 x rasio 4.0   -> TP 100
#     SL 30 x rasio 2.333 -> TP  70
#     SL 30 x rasio 3.333 -> TP 100
#
# TUJUANNYA BUKAN profit eterna sendirian. Tujuannya membatasi RISIKO GABUNGAN
# saat eterna dan Semi Marti terbuka bersamaan:
#     sekarang : eterna $70 + basket Marti $75 = $145 = 27% dari ekuitas $538
#     dengan SL 30: $30 + $75 = $105 = 19.5%
# Jadi yang diukur di sini adalah BERAPA PROFIT YANG HILANG untuk menurunkan
# eksposur itu -- bukan apakah variannya "menang".
#
# 2023 tidak dijalankan: stop struktur terlebar tahun itu hanya ~$19.6 (kerugian
# terbesar -19.58), jadi clamp 25/30 tidak pernah menggigit -- hasilnya pasti
# sama dengan OFF ($20.08), sudah diukur.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\rr_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

function Run-Test($tag, $from, $to, $clamp, $ratio) {
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
InpMaxSLDist=$clamp
InpTPRatio=$ratio
InpRiskCapUSD=70.0
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
    if (-not (Test-Path "$dir\report_$tag.htm")) { "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8 }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# label = sl<jarak>tp<target>
$combos = @(
    @("sl25tp70",  "25.0", "2.8"),
    @("sl25tp100", "25.0", "4.0"),
    @("sl30tp70",  "30.0", "2.3333"),
    @("sl30tp100", "30.0", "3.3333")
)

foreach ($yr in @(2024, 2025, 2026)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    foreach ($c in $combos) {
        Run-Test "$($c[0])_$yr" "$yr.01.01" $to $c[1] $c[2]
    }
}
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
