# Pemulihan sistem — kalau VPS ini hilang

Diperbarui **2026-08-21**. Dokumen ini menggambarkan sistem yang BENAR-BENAR jalan
hari ini. Versi sebelumnya (2026-07-04) sudah usang tujuh minggu: dia menyuruh
memasang `zrev_xau`, `orb30_nas`, `liquidity_limit` dan `vision_smc` — semuanya sudah
mati — sekaligus **tidak menyebut Semi Marti sama sekali**, padahal dia penghasil
profit terbesar. Mengikutinya akan membangun ulang sistem yang salah.

Jangan percaya dokumen ini begitu saja. Setelah selesai, jalankan:

```
python tools\verify_system.py
```

Skrip itu memeriksa MESIN-nya, bukan dokumen, jadi dia tidak bisa usang dengan cara
yang sama. Dia melaporkan persis apa yang kurang dan cara memperbaikinya.

---

## Yang jalan sekarang — dua strategi

| | eterna | Semi Marti |
|---|---|---|
| Dijalankan oleh | brain Python + EA `SignalExecutor` | EA `SemiMartiV10_Gated` (mandiri) |
| Chart | XAUUSD **M30** | XAUUSD **M5** |
| Magic | 920627 | 20250822 |
| Butuh brain? | **YA** — tanpa brain tidak ada sinyal | tidak |
| Stop | SL/TP broker, median ~$48 (struktural) | basket SL **$75 virtual, dijaga EA** |
| Preset | tidak perlu | **WAJIB** `SemiMartiV10_GATED.set` |

Semua slot lain di `config.yaml` sengaja `enabled: false`. Jangan hidupkan tanpa
validasi ulang.

---

## Tidak ada rahasia yang dibutuhkan untuk trading

`.env` hanya berisi `ANTHROPIC_API_KEY`, dan itu cuma dipakai slot `vision` yang
semuanya mati. **Sistem trading jalan tanpa API key apa pun.** Yang tidak ada di git:

- `.env` — hanya perlu kalau slot vision dihidupkan lagi
- `data/Level_0_Raw/*.duckdb` — data riset; live TIDAK memakainya (brain ambil bar
  langsung dari MT5). Sumber ini juga terbukti bolong untuk 2026 — lihat catatan di
  `research/eterna_ensemble_final.py`
- `mt5_tester/` — instance MT5 portable ~920MB, untuk backtest saja
- Login MT5 — tersimpan di MT5 sendiri (centang "Save account information")
- `_MONITOR/*.jsonl` — jurnal operasional, riwayat saja

---

## Membangun ulang di mesin baru (Windows)

**1. Python + kode**

```
Install Python 3.11 (centang "Add Python to PATH")
git clone https://github.com/PuteraKesuma/quant- C:\Quant
cd C:\Quant
pip install -r requirements.txt
```

**2. MetaTrader 5**

Install MT5, login ke akun, centang **"Save account information"** supaya login
otomatis setelah reboot.

**3. Pasang EA + preset**

```
INSTALL_EA.bat
```

Menyalin `SignalExecutor`, `SemiMartiV10_Gated`, dan kedua file preset ke semua
terminal MT5 yang terpasang.

**4. Pasang EA ke chart — MANUAL, tidak bisa diotomatiskan**

MT5 tidak menyediakan cara memasang EA ke chart dari luar GUI-nya.

- Chart **XAUUSD M30** → drag `SignalExecutor` (tanpa preset)
- Chart **XAUUSD M5** → drag `SemiMartiV10_Gated` → di dialog inputs tekan
  **Load** → pilih **`SemiMartiV10_GATED.set`**
- Nyalakan tombol **Algo Trading**

> **Langkah Load tidak boleh dilewati.** Default EA adalah `InpGlobalSL_USD = 0` —
> **tidak ada batas kerugian sama sekali** pada martingale-nya. `InpDebug` juga
> default `true`, yang pernah menghasilkan log 768MB dalam hitungan menit. Preset
> memasang SL $75, menyalakan regime gate, dan mematikan debug.

**5. Autostart**

```
INSTALL_AUTOSTART.bat
```

Memasang shortcut di folder Startup. Untuk benar-benar tahan reboot, aktifkan juga
auto-login Windows (`netplwiz`), dan tutup RDP dengan **Disconnect (X), jangan Sign
out** — Sign out mematikan semua proses.

**6. Verifikasi**

```
python tools\verify_system.py
```

Harus lolos semua sebelum ditinggal jalan sendiri.

---

## Cara kerjanya saat hidup

```
Windows login
   └─ folder Startup → _MONITOR\watchdog_shadow.ps1
         ├─ hidupkan MetaTrader 5 kalau mati
         └─ hidupkan brain (python -m pipeline.live.run_server) kalau mati
               └─ layani /signals di 127.0.0.1:8000
                     └─ SignalExecutor (chart M30) polling → kirim order eterna

MetaTrader 5
   └─ SemiMartiV10_Gated (chart M5) — mandiri, tidak menyentuh brain
```

Brain dan Semi Marti **tidak saling bergantung**. Brain mati → Semi Marti tetap jalan.
MT5 mati → keduanya berhenti.

---

## Pengaman yang berlaku

| Lapis | Nilai | Ditegakkan oleh |
|---|---|---|
| Risiko per trade eterna | $70 | brain (`signal.py::_risk_ok`) |
| Budget risiko gabungan | $105 | brain — jatah eterna mengecil saat Marti terbuka |
| Basket SL Semi Marti | $75 | **EA, bukan server** — kalau EA berhenti merespons, tidak ada yang menutup posisi |
| Stop harian / lantai ekuitas | $250 / $50 | `monthly_governor` — **tidak jalan saat ini** |

Governor sedang mati. Kalau ingin lapis terakhir itu hidup, jalankan
`START_GOVERNOR.bat`. Daftar magic-nya sudah diperbaiki 2026-08-21 supaya benar-benar
menjaga kedua strategi — sebelumnya dia menjaga tiga slot yang semuanya sudah mati,
jadi dia adalah jaring pengaman yang tidak menjaga apa pun.

---

## Kalau GitHub juga hilang

Simpan salinan OFF-VPS secara berkala:

```
git bundle create quant-backup.bundle --all
```

Satu file itu berisi seluruh riwayat repo. Pulihkan dengan
`git clone quant-backup.bundle C:\Quant`. Simpan bersama `.env` (kalau slot vision
dipakai) dan salinan `SemiMartiV10_GATED.set`. **Jangan pernah unggah bundle berisi
`.env` ke tempat publik.**
