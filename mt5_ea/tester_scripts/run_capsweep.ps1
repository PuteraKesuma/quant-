# Risk-cap sweep for eterna: can drawdown come down without losing profit?
#
# The combined-equity study found eterna is the LARGER drawdown contributor
# (-368 vs Semi Marti's -278), so the biggest lever sits there, not on the gate.
# eterna's per-trade loss is bounded by governor.max_risk_per_trade, currently
# $90 -- which on a $517 account is 17% in a single trade.
#
# Lowering the cap mechanically shrinks each loss. The open question is what it
# costs: eterna's profit is extremely concentrated (10 best trades = 85% of all
# profit), so if the big winners happen to be the wide-stop trades, a tighter cap
# guts the edge. If they are not, the cap is close to free risk reduction.
#
# This sweeps 2026 first because it is fast (H1, ~90s per run). Any candidate
# that looks good here MUST then be checked on 2023 and 2025 -- three hypotheses
# already died today from being fitted to a single favourable year.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\capsweep_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

foreach ($cap in @(30, 50, 70, 90)) {
    $ini = "$dir\tester_cap$cap.ini"
@"
[Tester]
Expert=EternaBot
Symbol=XAUUSD
Period=H1
Model=0
Optimization=0
FromDate=2026.01.01
ToDate=2026.08.18
ForwardMode=0
Deposit=1000
Currency=USD
Leverage=1:100
ExecutionMode=0
Visual=0
Report=report_cap$cap
ReplaceReport=1
ShutdownTerminal=1

[TesterInputs]
InpLot=0.01
InpRiskCapUSD=$cap.0
InpDebug=false
InpHistBars=5000
"@ | Out-File $ini -Encoding ascii

    "[cap $cap] launching $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    Start-Process -FilePath "$dir\terminal64.exe" -ArgumentList "/portable", "/config:$ini" | Out-Null

    $deadline = (Get-Date).AddMinutes(12)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 15
        if (Test-Path "$dir\report_cap$cap.htm") { break }
    }
    if (Test-Path "$dir\report_cap$cap.htm") {
        "[cap $cap] DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    } else {
        "[cap $cap] TIMEOUT" | Out-File $log -Append -Encoding utf8
    }
    Get-Process terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
