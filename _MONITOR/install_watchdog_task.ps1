# =====================================================================================
#  PASANG AUTO-START - mendaftarkan Scheduled Task "Quant Watchdog"
#
#  CATATAN ENCODING: pure ASCII, alasannya sama dengan watchdog_shadow.ps1.
#
#  MASALAH YANG DIPECAHKAN (insiden 2026-08-10):
#  Watchdog menjaga brain/xauexec/orbmgr, tapi TIDAK ADA yang menjaga watchdog. Dia
#  dijalankan dari jendela terminal; terminal ditutup -> seluruh rantai mati dan diam
#  15 jam tanpa satu trade pun. Task inilah lapis di atas watchdog.
#
#  DUA KEPUTUSAN YANG TIDAK BOLEH DIUBAH TANPA PAHAM AKIBATNYA:
#
#  1. Repetisi tiap 5 menit + MultipleInstances = IgnoreNew.
#     Inilah penjaga-nya-penjaga, dan sengaja TANPA kode tambahan. Kalau watchdog
#     hidup, mutex di dalamnya membuat instance baru keluar diam-diam (IgnoreNew juga
#     menahan di level scheduler). Kalau watchdog mati - dibunuh, crash, atau ikut mati
#     bersama terminal - tick 5 menit berikutnya menghidupkannya. Jendela kerugian
#     maksimum jadi 5 menit, bukan semalaman.
#
#  2. LogonType = Interactive, BUKAN S4U.
#     MT5 Python API menempel ke terminal64.exe lewat sesi Windows yang sama. Proses
#     di sesi 0 (S4U / "run whether user is logged on or not") TIDAK bisa menjangkau
#     terminal64 milik sesi desktop -> mt5.initialize() gagal dan tidak ada order yang
#     terkirim, sementara semua status terlihat hijau. Konsekuensinya: harus ada sesi
#     yang login. Itu sebabnya auto-login Windows dipasang (lihat PASANG_AUTOSTART.bat).
#
#  Skrip ini idempoten: aman dijalankan berulang, task lama ditimpa.
#
#  Jalankan:
#    powershell -ExecutionPolicy Bypass -File C:\Quant\_MONITOR\install_watchdog_task.ps1
# =====================================================================================
$ErrorActionPreference = "Stop"

$TaskName = "Quant Watchdog"
$Script   = "C:\Quant\_MONITOR\watchdog_shadow.ps1"
$User     = "$env:COMPUTERNAME\Administrator"

if (-not (Test-Path $Script)) { throw "Tidak ketemu: $Script" }

Write-Host ""
Write-Host "=============================================================="
Write-Host "  PASANG AUTO-START TRADING"
Write-Host "=============================================================="
Write-Host ""

# ---------- repetisi 5 menit ----------
# Durasi 10 tahun, BUKAN [TimeSpan]::MaxValue. New-ScheduledTaskTrigger menerima
# MaxValue tanpa protes, tapi Register-ScheduledTask menolaknya di belakang layar
# ("Duration:P99999999DT23H59M59S ... out of range") - jenis kegagalan yang cuma
# muncul saat pendaftaran, bukan saat pembuatan trigger. Lagipula repetisi ini
# dihitung ulang tiap login, jadi 10 tahun praktis sama dengan selamanya.
$bantu = New-ScheduledTaskTrigger -Once -At (Get-Date) `
         -RepetitionInterval (New-TimeSpan -Minutes 5) `
         -RepetitionDuration (New-TimeSpan -Days 3650)
$rep = $bantu.Repetition

$aksi = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`"" `
        -WorkingDirectory "C:\Quant"

$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $User
$tLogon.Repetition = $rep

$tBoot = New-ScheduledTaskTrigger -AtStartup
$tBoot.Delay = "PT3M"          # beri MT5 dan jaringan waktu bangun dulu
$tBoot.Repetition = $rep

$principal = New-ScheduledTaskPrincipal -UserId $User `
             -LogonType Interactive -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $aksi `
    -Trigger @($tLogon, $tBoot) -Principal $principal -Settings $settings `
    -Description "Menjaga watchdog trading tetap hidup. Repetisi 5 menit + IgnoreNew = penjaga-nya-penjaga. Interactive karena MT5 API butuh sesi desktop." `
    -Force | Out-Null

Write-Host "  [ OK ] Task '$TaskName' terpasang."
Write-Host "         trigger  : saat login + saat boot (delay 3 menit)"
Write-Host "         repetisi : tiap 5 menit selama 10 tahun, IgnoreNew"
Write-Host "         akun     : $User (Interactive, hak tertinggi)"
Write-Host ""

# ---------- perbaiki principal task lama ----------
# 'Quant Auto Backup' berjalan S4U dan itulah sebab push GitHub GAGAL 2026-08-10:
# S4U tidak punya akses ke Windows Credential Manager, jadi git tidak menemukan token.
# Interactive memperbaikinya. Harganya: task hanya jalan saat ada sesi login - yang
# selalu benar begitu auto-login aktif, dan StartWhenAvailable mengejar jadwal yang
# terlewat kalau ternyata tidak.
foreach ($nm in @("Quant Auto Backup", "Quant RSI2 Sleeve")) {
    $t = Get-ScheduledTask -TaskName $nm -ErrorAction SilentlyContinue
    if ($null -eq $t) {
        Write-Host "  [ -- ] Task '$nm' tidak ada, dilewati."
        continue
    }
    if ($t.Principal.LogonType -eq "Interactive") {
        Write-Host "  [ OK ] Task '$nm' sudah Interactive."
        continue
    }
    $s = $t.Settings
    $s.StartWhenAvailable = $true
    Set-ScheduledTask -TaskName $nm -Principal $principal -Settings $s | Out-Null
    Write-Host "  [ OK ] Task '$nm' diubah $($t.Principal.LogonType) -> Interactive (supaya bisa baca Credential Manager)."
}

Write-Host ""
Write-Host "  Status sekarang:"
Get-ScheduledTask | Where-Object { $_.TaskName -match "^Quant " } |
    ForEach-Object {
        $i = $_ | Get-ScheduledTaskInfo
        "    {0,-20} {1,-10} {2,-12} terakhir {3}" -f `
            $_.TaskName, $_.State, $_.Principal.LogonType, $i.LastRunTime
    } | Write-Host

Write-Host ""
Write-Host "  Berikutnya: jalankan AUTO_TRADING_ON.bat untuk memulai sekarang juga."
Write-Host ""
