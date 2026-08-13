# =====================================================================================
#  WATCHDOG - penjaga seluruh rantai eksekusi trading
#
#  CATATAN ENCODING (penting, jangan diubah):
#  Seluruh file ini sengaja ditulis TANPA karakter non-ASCII. PowerShell -File membaca
#  skrip memakai codepage sistem kalau tidak ada BOM; pada 2026-08-10 sebuah em-dash
#  di komentar berubah jadi mojibake dan MEMECAH sintaks -> watchdog gagal start diam-diam
#  sementara semua proses lain terlihat normal. Pakai tanda hubung biasa saja.
#
#  Yang dijaga (empat-empatnya wajib hidup, kalau satu mati ada sleeve yang berhenti):
#    1. BRAIN            pipeline.live.run_server      - sumber sinyal
#    2. XAU_EXECUTOR     pipeline.live.xau_executor    - PENGGANTI EA MQL5; mengirim order
#                        untuk slot XAU (zrev/eterna/eterna_asli). Kalau mati, brain tetap
#                        menghitung dan /health tetap hijau TAPI tidak ada order terkirim.
#                        Ini kegagalan paling berbahaya karena tidak terlihat di mana pun.
#    3. ORB_STOP_MANAGER pipeline.live.orb_stop_manager - sleeve ORB (bobot terbesar 43%,
#                        lot 0.03). Juga tidak butuh EA.
#    4. METATRADER 5     sumber bar M1; kalau mati semuanya buta.
#
#  SENGAJA TIDAK dijalankan: liquidity_manager (pensiun),
#  monthly_governor. Jangan pakai watchdog_brain.ps1 yang lama - dia menghidupkan semua itu.
#
#  ------------------------------------------------------------------------------------
#  SIAPA YANG MENJAGA WATCHDOG (ditambahkan 2026-08-11)
#  Insiden 2026-08-10: watchdog dijalankan dari jendela terminal. Terminal ditutup ->
#  watchdog mati -> brain/xauexec/orbmgr ikut mati dan TIDAK ADA yang menghidupkan lagi.
#  Jurnal berhenti 10:19 UTC dan sisa hari itu kosong tanpa satu trade pun.
#  Sekarang skrip ini HANYA dijalankan lewat Scheduled Task "Quant Watchdog", yang
#  berulang tiap 5 menit selamanya dengan MultipleInstances=IgnoreNew. Mutex di bawah
#  yang membuat pengulangan itu aman: instance kedua keluar diam-diam, jadi tick 5 menit
#  itu murni berfungsi sebagai penjaga-nya-penjaga.
#
#  Jalankan (normalnya JANGAN manual, pakai AUTO_TRADING_ON.bat):
#    powershell -ExecutionPolicy Bypass -File C:\Quant\_MONITOR\watchdog_shadow.ps1
# =====================================================================================
$ErrorActionPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "WATCHDOG TRADING - JANGAN DITUTUP"

$Py        = "C:\Program Files\Python311\python.exe"
$Root      = "C:\Quant"
$MonDir    = "C:\Quant\_MONITOR"
$Jurnal    = Join-Path $MonDir "jurnal.md"
$HealthLog = Join-Path $MonDir "health_log.jsonl"
$SigLog    = Join-Path $MonDir "eterna_shadow.jsonl"
$AliveFile = Join-Path $MonDir "watchdog_alive.txt"
$PushFile  = Join-Path $MonDir "last_push_ok.txt"
$Probe     = Join-Path $MonDir "mt5_probe.py"
$Mt5Exe    = "C:\Program Files\MetaTrader 5\terminal64.exe"
$HealthUrl = "http://127.0.0.1:8000/health"
$SignalUrl = "http://127.0.0.1:8000/signals?symbol=XAUUSD"

$Interval           = 30
$FailsToRestart     = 3
$RestartCooldownMin = 3
$ProcCooldownMin    = 3
$Mt5CooldownMin     = 5
$HeartbeatMin       = 30
$SignalPollMin      = 15
$ProbeMin           = 5      # cek MT5 benar-benar bisa trading (bukan cuma prosesnya ada)
$GuardCheckMin      = 2      # seberapa sering log brain dipindai untuk pemblokiran eterna
$LockedWarnMin      = 30     # jangan spam jurnal saat Algo Trading mati
$PushCheckHours     = 6      # seberapa sering umur push GitHub diperiksa
$PushStaleHours     = 36     # lewat ini, cadangan off-VPS dianggap basi

function NowUtc { (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") }

function J($tag, $msg) {
    $line = "- **{0} UTC** {1} [wd] {2}" -f (NowUtc), $tag, $msg
    Add-Content -Path $Jurnal -Value $line -Encoding utf8
    Write-Host $line
}

# ---------------------------------------------------------------------------------
#  MUTEX SINGLETON - inti dari pola "task berulang tiap 5 menit".
#  Kalau sudah ada watchdog hidup, instance ini keluar TENANG (exit 0) tanpa menulis
#  apa pun ke jurnal; kalau tidak ada, instance ini yang mengambil alih.
#  AbandonedMutexException WAJIB ditangkap: kalau watchdog sebelumnya dibunuh paksa
#  (persis skenario terminal ditutup), mutex ditinggalkan dalam keadaan abandoned dan
#  WaitOne melempar exception - tapi kepemilikan TETAP diberikan. Tanpa catch ini,
#  watchdog justru tidak akan pernah bangkit setelah crash: kebalikan dari tujuannya.
# ---------------------------------------------------------------------------------
$mutex = New-Object System.Threading.Mutex($false, "Global\QuantWatchdogShadow")
try {
    $punya = $mutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $punya = $true
}
if (-not $punya) { exit 0 }

function ProcUp($pattern) {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match $pattern }
    return [bool]$p
}

function StartPy($module, $outName, $errName) {
    Start-Process -FilePath $Py -ArgumentList "-m", $module `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $MonDir $outName) `
        -RedirectStandardError  (Join-Path $MonDir $errName)
}

function Mt5Up { return [bool](Get-Process terminal64 -ErrorAction SilentlyContinue) }

# --- state ---
$fails          = 0
$probeFails     = 0
$lastRestart    = (Get-Date).AddHours(-1)
$lastXauTry     = (Get-Date).AddHours(-1)
$lastOrbTry     = (Get-Date).AddHours(-1)
$lastSmcTry     = (Get-Date).AddHours(-1)
$lastAdvTry     = (Get-Date).AddHours(-1)
$lastMt5Try     = (Get-Date).AddHours(-1)
$lastHeartbeat  = (Get-Date).AddHours(-1)
$lastSignalPoll = (Get-Date).AddHours(-1)
$lastProbe      = (Get-Date).AddHours(-1)
$lastGuardCheck = (Get-Date).AddHours(-1)
$guardCount     = -1         # -1 = belum pernah dipindai; jangan banjiri jurnal saat start
$lastLockedWarn = (Get-Date).AddHours(-24)
$lastPushCheck  = (Get-Date).AddHours(-24)
$lastAction     = ""

J "ON " ("Watchdog START (pid {0}, dipanggil Scheduled Task). Menjaga: BRAIN + XAU_EXECUTOR + ORB_STOP_MANAGER + MT5." -f $PID)

while ($true) {
    $now = Get-Date

    # ---------- 0. file detak: bukti hidup yang bisa dibaca dari luar ----------
    # CEK_TRADING.bat membaca umur file ini. Umur > 2 menit = watchdog macet/mati,
    # dan itu terbaca TANPA harus menebak-nebak dari daftar proses.
    Set-Content -Path $AliveFile -Value ("{0} UTC pid={1}" -f (NowUtc), $PID) -Encoding ascii

    # ---------- 1. MetaTrader 5 ----------
    if (-not (Mt5Up)) {
        if (($now - $lastMt5Try).TotalMinutes -ge $Mt5CooldownMin) {
            J "ERR" "MetaTrader 5 MATI - semua sleeve kehilangan sumber bar. Menjalankan ulang..."
            Start-Process -FilePath $Mt5Exe
            $lastMt5Try = $now
            $probeFails = 0
        }
    }

    # ---------- 2. brain sehat? ----------
    $healthy = $false
    $slots = ""
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) {
            $healthy = $true
            $j = $r.Content | ConvertFrom-Json
            $slots = ($j.strategies | ForEach-Object { $_.name }) -join ","
            $rec = @{ ts = (NowUtc); up = $j.uptime_seconds; slots = $slots } | ConvertTo-Json -Compress
            Add-Content -Path $HealthLog -Value $rec -Encoding utf8
        }
    } catch { }

    if ($healthy) {
        if ($fails -ge $FailsToRestart) { J "OK " "Brain PULIH - health OK lagi." }
        $fails = 0
    } else {
        $fails++
        if ($fails -eq 1) { J "WRN" "Health gagal (1). Menunggu konfirmasi..." }
        if ($fails -ge $FailsToRestart) {
            if (($now - $lastRestart).TotalMinutes -ge $RestartCooldownMin) {
                if (ProcUp "pipeline\.live\.run_server") {
                    J "CLN" "Proses brain ada tapi health mati - menghentikan proses lama."
                    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                        Where-Object { $_.CommandLine -match "pipeline\.live\.run_server" } |
                        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
                    Start-Sleep -Seconds 3
                }
                J "RST" ("Brain DOWN ({0} cek gagal) - menjalankan ulang..." -f $fails)
                StartPy "pipeline.live.run_server" "brain_out.log" "brain_err.log"
                $lastRestart = $now
            }
        }
    }

    # ---------- 3. xau_executor (pengganti EA) ----------
    if (-not (ProcUp "pipeline\.live\.xau_executor")) {
        if (($now - $lastXauTry).TotalMinutes -ge $ProcCooldownMin) {
            J "ERR" "xau_executor MATI - slot XAU tidak mengirim order sama sekali. Menghidupkan ulang..."
            StartPy "pipeline.live.xau_executor" "xauexec_out.log" "xauexec_err.log"
            $lastXauTry = $now
        }
    }

    # ---------- 4. orb_stop_manager ----------
    if (-not (ProcUp "pipeline\.live\.orb_stop_manager")) {
        if (($now - $lastOrbTry).TotalMinutes -ge $ProcCooldownMin) {
            J "ERR" "orb_stop_manager MATI - sleeve ORB (43% bobot) tidak jalan. Menghidupkan ulang..."
            StartPy "pipeline.live.orb_stop_manager" "orbmgr_out.log" "orbmgr_err.log"
            $lastOrbTry = $now
        }
    }

    # ---------- 4b. smc_limit_manager ----------
    # Sleeve SMC (magic 920643): pending LIMIT di zona Order Block dengan expiry.
    # Kalau mati, tidak ada yang memasang ATAU membatalkan order -> pending lama bisa
    # menggantung sampai expiry-nya sendiri. Broker tetap menghormati SL/TP/expiry, jadi
    # ini tidak berbahaya, tapi sleeve-nya berhenti menghasilkan sinyal baru.
    if (-not (ProcUp "pipeline\.live\.smc_limit_manager")) {
        if (($now - $lastSmcTry).TotalMinutes -ge $ProcCooldownMin) {
            J "ERR" "smc_limit_manager MATI - sleeve SMC tidak memasang zona baru. Menghidupkan ulang..."
            StartPy "pipeline.live.smc_limit_manager" "smcmgr_out.log" "smcmgr_err.log"
            $lastSmcTry = $now
        }
    }

    # ---------- 4c. advisor (pembaca berita/sentimen) ----------
    # Dinyalakan 2026-08-13 atas permintaan user. READ-ONLY: dia tidak pernah
    # memasang, memblokir, atau menutup order - hanya mencatat verdict di samping
    # trade. Jadi kalau dia mati, TRADING TIDAK TERPENGARUH sama sekali; yang hilang
    # cuma catatan konteksnya. Karena itu dia dijaga, tapi tidak pernah memicu ERR.
    if (-not (ProcUp "pipeline\.live\.advisor")) {
        if (($now - $lastAdvTry).TotalMinutes -ge $ProcCooldownMin) {
            J "RST" "advisor MATI - catatan berita/sentimen berhenti (trading TIDAK terpengaruh). Menghidupkan ulang..."
            StartPy "pipeline.live.advisor" "advisor_out.log" "advisor_err.log"
            $lastAdvTry = $now
        }
    }

    # ---------- 5. MT5 BENAR-BENAR bisa trading? ----------
    # Cek proses di langkah 1 LOLOS walau terminal duduk di dialog login atau tombol
    # Algo Trading mati. Probe ini yang membedakannya (lihat _MONITOR/mt5_probe.py):
    #   exit 1 MATI     -> restart terminal64 (setelah 2 kegagalan berturut-turut)
    #   exit 2 TERKUNCI -> HANYA lapor. Restart tidak akan menyalakan tombol Algo
    #                      Trading; itu keadaan GUI tersimpan yang cuma bisa diperbaiki
    #                      manusia. Restart berulang justru mengaburkan jurnal.
    if ((Mt5Up) -and ($now - $lastProbe).TotalMinutes -ge $ProbeMin) {
        $pout = & $Py $Probe 2>&1
        $pcode = $LASTEXITCODE
        $lastProbe = $now
        if ($pcode -eq 0) {
            if ($probeFails -gt 0) { J "OK " "MT5 PULIH - bisa trading lagi. $pout" }
            $probeFails = 0
        } elseif ($pcode -eq 2) {
            $probeFails = 0
            if (($now - $lastLockedWarn).TotalMinutes -ge $LockedWarnMin) {
                J "ERR" "MT5 login TAPI Algo Trading MATI - tidak ada order yang bisa terkirim. NYALAKAN tombol Algo Trading di MT5 (watchdog tidak bisa). $pout"
                $lastLockedWarn = $now
            }
        } else {
            $probeFails++
            J "WRN" ("MT5 proses ada tapi tidak bisa dipakai ({0}/2). {1}" -f $probeFails, $pout)
            if ($probeFails -ge 2 -and ($now - $lastMt5Try).TotalMinutes -ge $Mt5CooldownMin) {
                J "RST" "MT5 macet (belum login / tidak merespons) - restart terminal64. SL/TP posisi tetap dijaga broker."
                Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force
                Start-Sleep -Seconds 5
                Start-Process -FilePath $Mt5Exe
                $lastMt5Try = $now
                $probeFails = 0
            }
        }
    }

    # ---------- 6. rekam sinyal berkala ----------
    if ($healthy -and ($now - $lastSignalPoll).TotalMinutes -ge $SignalPollMin) {
        try {
            $s = (Invoke-WebRequest -Uri $SignalUrl -UseBasicParsing -TimeoutSec 60).Content | ConvertFrom-Json
            foreach ($sig in $s.signals) {
                $rec = @{
                    ts = (NowUtc); strategy = $sig.strategy; action = $sig.action
                    sl = $sig.sl; tp = $sig.tp; signal_id = $sig.signal_id
                } | ConvertTo-Json -Compress
                Add-Content -Path $SigLog -Value $rec -Encoding utf8
                if ($sig.action -ne $lastAction) {
                    J "SIG" ("{0}: {1} -> {2}  sl={3} tp={4}" -f `
                        $sig.strategy, $lastAction, $sig.action, $sig.sl, $sig.tp)
                    $lastAction = $sig.action
                }
            }
        } catch { }
        $lastSignalPoll = $now
    }

    # ---------- 6b. eterna diblokir zrev? ----------
    # LUBANG YANG DITUTUP 2026-08-11: kalau _book_conflict memblokir eterna, dia meng-emit
    # FLAT - persis sama dengan eterna yang memang tidak punya sinyal. Di jurnal, "diblokir"
    # dan "menunggu" terlihat IDENTIK. Padahal simulasi per-trade menunjukkan 53,4% entry
    # eterna kena blokir (research/blocking_akurat.py), jadi ini bukan kasus langka.
    # Alasannya cuma tercatat di brain_err.log yang tidak pernah dibaca siapa pun.
    # Di sini log itu dipindai dan setiap pemblokiran BARU diangkat ke jurnal.
    #
    # Asimetrinya disengaja, bukan bug: zrev (920622) ada di governor.magics, eterna
    # (920627) tidak, jadi zrev selalu menang saat keduanya searah. Dibiarkan karena
    # korelasi keduanya +0,83. Yang tidak boleh adalah itu terjadi TANPA TERLIHAT.
    if (($now - $lastGuardCheck).TotalMinutes -ge $GuardCheckMin) {
        $lastGuardCheck = $now
        $hits = @(Get-Content (Join-Path $MonDir "brain_err.log") -Tail 4000 -ErrorAction SilentlyContinue |
                  Select-String -Pattern "net-exposure guard")
        $n = $hits.Count
        if ($guardCount -lt 0 -or $n -lt $guardCount) {
            $guardCount = $n              # pemindaian pertama, atau brain restart -> log baru
        } elseif ($n -gt $guardCount) {
            foreach ($h in $hits[($guardCount)..($n - 1)]) {
                J "BLK" ("ETERNA DIBLOKIR - zrev sudah pegang XAU searah, entry eterna dibatalkan. {0}" -f $h.Line.Trim())
            }
            $guardCount = $n
        }
    }

    # ---------- 7. umur cadangan off-VPS ----------
    # auto_backup.ps1 menulis last_push_ok.txt tiap kali push GitHub sukses. Kalau file
    # itu basi, cadangan satu-satunya tinggal ZIP di VPS ini - dan VPS ini yang sudah
    # pernah hilang sekali. Wajib berisik, bukan gagal diam-diam.
    if (($now - $lastPushCheck).TotalHours -ge $PushCheckHours) {
        $lastPushCheck = $now
        if (Test-Path $PushFile) {
            $umur = ($now - (Get-Item $PushFile).LastWriteTime).TotalHours
            if ($umur -ge $PushStaleHours) {
                J "ERR" ("Push GitHub terakhir sukses {0:N0} jam lalu (ambang {1} jam). Kerja terbaru HANYA ada di VPS ini." -f $umur, $PushStaleHours)
            }
        } else {
            J "ERR" "Belum pernah ada catatan push GitHub sukses. Cadangan off-VPS BELUM terbukti."
        }
    }

    # ---------- 8. heartbeat ----------
    if (($now - $lastHeartbeat).TotalMinutes -ge $HeartbeatMin) {
        $mt5txt = "MT5 UP";     if (-not (Mt5Up)) { $mt5txt = "MT5 DOWN" }
        $xetxt  = "xauexec UP"; if (-not (ProcUp "pipeline\.live\.xau_executor"))    { $xetxt  = "xauexec DOWN" }
        $orbtxt = "orbmgr UP";  if (-not (ProcUp "pipeline\.live\.orb_stop_manager")) { $orbtxt = "orbmgr DOWN" }
        J "HB " ("Sehat. slots={0}. {1}. {2}. {3}." -f $slots, $mt5txt, $xetxt, $orbtxt)
        $lastHeartbeat = $now
    }

    Start-Sleep -Seconds $Interval
}
