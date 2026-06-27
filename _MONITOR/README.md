# _MONITOR — pemantauan brain

Isi folder:

| File | Fungsi |
|---|---|
| `jurnal.md` | **Baca ini sepulang kerja.** Catatan manusia: brain up/down, restart, insiden, catatan analis. |
| `health_log.jsonl` | Sampel mentah `/health` tiap 30 dtk (untuk audit detail; satu JSON per baris). |
| `watchdog_brain.ps1` | Watchdog: cek health, auto-restart brain kalau down, tulis ke 2 file di atas. |

## Cara kerja watchdog
- Cek `http://127.0.0.1:8000/health` tiap **30 detik**.
- DOWN **3x beruntun** → otomatis restart brain via `START_TRADING.bat` (ada cooldown 3 mnt, bersihkan port zombie dulu).
- Kalau **MT5 mati** juga → tidak auto-restart (preflight pasti gagal); ditulis `WARN` di jurnal — perlu login MT5 manual.

## Menjalankan watchdog (kalau jendelanya tertutup)
```powershell
powershell -ExecutionPolicy Bypass -File C:\Quant\_MONITOR\watchdog_brain.ps1
```
Jendela watchdog **jangan ditutup** — kalau ditutup, auto-restart berhenti (brain tetap jalan, hanya tidak terjaga).

## Cek cepat manual
```powershell
# brain hidup?
(Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing).Content
# 10 baris terakhir jurnal
Get-Content C:\Quant\_MONITOR\jurnal.md -Tail 10
```
