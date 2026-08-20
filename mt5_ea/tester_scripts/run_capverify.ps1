# Out-of-sample check for the risk-cap finding.
#
# On 2026, cap $70 dominated the live $90 setting on BOTH axes: net +725.99 vs
# +477.89 and maxDD 15.44% vs 26.43%. A TIGHTER risk cap producing MORE profit
# can only mean one thing -- in that particular year the wide-stop trades
# happened to lose. Whether that is a property of the strategy or a property of
# 2026 is exactly what a single year cannot tell you, and three hypotheses have
# already died today from being fitted to one favourable year.
#
# So: run both caps over 2023 (the year Semi Marti lost badly) and 2025. The cap
# only earns a change if it holds up in all three.
$ErrorActionPreference = "SilentlyContinue"
$dir = "C:\Quant\mt5_tester"
$log = "$dir\capverify_progress.txt"
"START $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8

foreach ($yr in @(2023, 2025)) {
    foreach ($cap in @(70, 90)) {
        $tag = "${yr}_c$cap"
        $ini = "$dir\tester_$tag.ini"
@"
[Tester]
Expert=EternaBot
Symbol=XAUUSD
Period=H1
Model=0
Optimization=0
FromDate=$yr.01.01
ToDate=$yr.12.31
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
InpRiskCapUSD=$cap.0
InpDebug=false
InpHistBars=5000
"@ | Out-File $ini -Encoding ascii

        "[$tag] launching $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
        Start-Process -FilePath "$dir\terminal64.exe" -ArgumentList "/portable", "/config:$ini" | Out-Null
        $deadline = (Get-Date).AddMinutes(15)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 15
            if (Test-Path "$dir\report_$tag.htm") { break }
        }
        if (Test-Path "$dir\report_$tag.htm") {
            "[$tag] DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
        } else {
            "[$tag] TIMEOUT" | Out-File $log -Append -Encoding utf8
        }
        Get-Process terminal64 -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -like "$dir*" } | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }
}
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
