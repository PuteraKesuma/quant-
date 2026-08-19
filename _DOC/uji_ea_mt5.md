# Cara menguji EA MQL5 tanpa mengganggu trading yang sedang jalan

Ditulis 2026-08-11 setelah menguji `Semi Marti Cuan v10`. Kalau nanti ada EA lain yang
mau diuji, ikuti ini — jangan menjalankan Strategy Tester di MT5 yang sedang live.

## Kenapa pakai salinan terisolasi

MT5 live sedang memegang order sungguhan (ORB pending STOP, posisi eterna). Strategy
Tester membebani terminal dan mengubah tampilannya ke mode tester. Salinan terpisah
menghapus seluruh risiko itu dengan harga 1 GB disk.

Foldernya **sengaja di-gitignore**. Log satu agent saja bisa 113 MB; auto-backup
memakai `git add -A`, jadi tanpa itu satu backup akan mencoba mendorong satu giga
biner ke GitHub.

## Langkahnya

```powershell
# 1. salin instalasi + data (history & akun tersimpan)
robocopy "C:\Program Files\MetaTrader 5" "C:\Quant\mt5_tester" /E /NFL /NDL /NJH /NJS /NP
$src = "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075"
robocopy "$src\bases"  "C:\Quant\mt5_tester\bases"  /E /NFL /NDL /NJH /NJS /NP
robocopy "$src\config" "C:\Quant\mt5_tester\config" /E /NFL /NDL /NJH /NJS /NP
robocopy "$src\MQL5\Include" "C:\Quant\mt5_tester\MQL5\Include" /E /NFL /NDL /NJH /NJS /NP

# 2. taruh EA-nya
Copy-Item "<file>.mq5" "C:\Quant\mt5_tester\MQL5\Experts\NamaEA.mq5"

# 3. compile - /inc WAJIB, tanpa itu Trade.mqh tidak ketemu
& "C:\Quant\mt5_tester\metaeditor64.exe" /compile:"C:\Quant\mt5_tester\MQL5\Experts\NamaEA.mq5" `
  /inc:"C:\Quant\mt5_tester\MQL5" /log:"C:\Quant\mt5_tester\compile.log"
Get-Content "C:\Quant\mt5_tester\compile.log" -Encoding Unicode | Select-String 'error|Result'

# 4. jalankan
Start-Process "C:\Quant\mt5_tester\terminal64.exe" -ArgumentList "/portable","/config:C:\Quant\mt5_tester\test.ini"
```

`test.ini`:

```ini
[Tester]
Expert=NamaEA
Symbol=XAUUSD
Period=M15
Model=1                 ; 0=tiap tick (lambat), 1=OHLC M1, 4=tick asli
FromDate=2021.01.01
ToDate=2026.08.11
Deposit=1000
Currency=USD
Leverage=1:500
Report=laporan
ReplaceReport=1
ShutdownTerminal=1
Visual=0

[TesterInputs]
InpNamaParameter=nilai
```

## Jebakan yang sudah memakan waktu

**`InpDebug=true` membuat tester merangkak.** EA yang mencetak tiap tick: 5 hari data =
4 menit CPU. Matikan dulu, selalu.

**`[TesterInputs]` hanya menimpa yang disebut.** Sisanya pakai default di kode. Log
tester mencetak SELURUH daftar input di awal — baca itu untuk memastikan yang kamu
maksud benar-benar terpasang, jangan diasumsikan.

**Laporan keluar sebagai `.htm`, bukan `.html`.** Menunggu file `.html` = menunggu
selamanya.

**Satu run memakai ulang folder Agent yang sama** dan menambahkan ke log lama. Kalau
mau membaca log satu run tertentu, catat waktunya, atau hapus `Tester\Agent-*` dulu.

## Urutan yang benar: jangan langsung pasang

1. **Baca kodenya dulu.** Di `Semi Marti Cuan v10` ada dua hal yang cuma ketahuan dari
   membaca: `InpGlobalSL_USD=0` artinya TANPA SL sama sekali, dan rumus pip-nya
   runtuh di simbol 2-digit (`25 pip` jadi 25 sen emas).
2. **Uji periode terbaru dulu** — cepat, dan kalau di sini saja rugi, selesai.
3. **Uji 5 tahun penuh.** Ini yang menentukan. `Semi Marti Cuan v10`: 2026 saja
   +23,7%, 2021–2025 **−100% (modal habis)**.
4. Baru port ke Python kalau perlu mencari parameter — dan **validasi port-nya dulu**
   terhadap jumlah trade hasil tester sebelum memercayai satu angka pun darinya.

## Aritmetika yang paling cepat membongkar EA winrate tinggi

```
winrate impas = rata_rata_rugi / (rata_rata_menang + rata_rata_rugi)
```

`Semi Marti Cuan v10`: menang $5,05, rugi $26,91 → butuh **84,2%** hanya untuk impas.
2026 dapat 89,8% (untung), 2021–2025 dapat 79,9% (ludes). Winrate 80% terdengar hebat
dan tetap menghabiskan akun. Hitung ambang ini sebelum terpukau angka winrate.
