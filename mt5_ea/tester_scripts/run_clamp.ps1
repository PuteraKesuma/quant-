# Sweep CLAMP jarak stop, dijalankan di MT5 Strategy Tester (tick sungguhan).
#
# KENAPA DI TESTER, BUKAN PYTHON
# Harness Python ternyata diberi data BOLONG dan hasilnya tidak bisa dipakai:
#   bar H1 2026-02-16 00:00..10:00  ->  copy_rates_range: 2 bar
#                                       duckdb          : 1 bar
#                                       copy_rates_from_pos / tester: 9 bar
# Supertrend bersifat path-dependent, jadi bar yang hilang menggeser seluruh
# jalur band -- itulah sebab EA mencatat 110 trade di 2023 sementara Python 71.
# Tester adalah acuan yang benar, dan cukup cepat (~60 detik per tahun), jadi
# sweep ini dijalankan langsung di sana.
#
# YANG DIUJI
# InpMaxSLDist = CLAMP: trade tetap diambil, jarak stop dipendekkan ke nilai itu.
# TP = InpTPRatio x jarak yang DIPAKAI, jadi clamp 30 + TP 1:4 -> TP 120.
# Bentuk SKIP sengaja TIDAK diuji: menolak trade yang stopnya lebih lebar dari $30
# membuat EA berhenti total di regime volatil (emas ~4500 di 2026, tidak ada stop
# struktur di bawah 30 -> nol trade sepanjang tahun terbaiknya).
#
# clamp 0 = mati = perilaku sekarang (stop struktur penuh, cap $70), sebagai acuan.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\clamp_progress.txt"
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
InpMaxSLDist=$clamp
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
    if (-not (Test-Path "$dir\report_$tag.htm")) {
        "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8
    }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

foreach ($yr in @(2023, 2024, 2025, 2026)) {
    $to = if ($yr -eq 2026) { "2026.08.18" } else { "$yr.12.31" }
    foreach ($cl in @("0.0", "20.0", "25.0", "30.0", "40.0", "50.0")) {
        $t = $cl -replace "\.0$", ""
        Run-Test "cl${t}_$yr" "$yr.01.01" $to $cl
    }
}
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
