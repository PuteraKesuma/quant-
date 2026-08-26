# Pemulihan sistem — kalau VPS ini hilang

Diperbarui **2026-08-26**. Dokumen ini menggambarkan sistem yang BENAR-BENAR jalan
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
| Preset | tidak perlu | **WAJIB** `SemiMartiV10_LIVE.set` |

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
  **Load** → pilih **`SemiMartiV10_LIVE.set`**
- Nyalakan tombol **Algo Trading**

> **Langkah Load tidak boleh dilewati, dan ini bukan formalitas.** Default EA adalah
> `InpGlobalSL_USD = 0` — **tidak ada batas kerugian sama sekali** pada martingale-nya.
> `InpDebug` juga default `true`, yang pernah menghasilkan log 768MB dalam hitungan
> menit.
>
> Dua hal yang terbukti terjadi 2026-08-26 dan wajib kamu tahu:
>
> 1. **MT5 mengingat input terakhir per-chart.** Kalau tombol Load tidak ditekan,
>    dialog memakai nilai lama dan file `.set` **tidak pernah dibaca**. Live berjalan
>    berminggu-minggu dengan `InpRequireBreakConfirm` yang berbeda dari SEMUA file
>    preset, sehingga setiap backtest yang dipakai mengambil keputusan menguji EA
>    yang berbeda.
> 2. **Mengganti file `.ex5` me-reset input ke DEFAULT.** Setelah recompile, EA
>    sempat jalan dengan `SL=$0` — basket tanpa stop. Ketahuan dalam hitungan detik
>    hanya karena EA sekarang mencetak input aktifnya.
>
> Setelah memasang, WAJIB periksa log Experts. EA mencetak blok `=== INPUT AKTIF ===`
> setiap kali dimuat. `python tools\verify_system.py` membandingkannya otomatis
> dengan file preset dan menyatakan FAIL kalau berbeda. **Jangan pernah menganggap
> preset sudah termuat tanpa melihat blok itu.**
>
> Jangan pula membuka dialog Properties (F7) saat ada posisi terbuka: menutupnya
> me-restart EA. Sejak 2026-08-26 EA mengadopsi kembali posisi yang ada
> (`ADOPSI DUAL`), tapi sebelum itu setiap restart menghapus TP $10 dan trailing $25
> dari basket yang sedang berjalan.

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
| Budget risiko gabungan | $145 | brain — plafon jujur atas worst case pasangan ini |
| Basket TP / SL Semi Marti | $40 / $75 | **EA, VIRTUAL** — posisi di broker `sl=0.00` |
| Trailing global basket | $10 / $2 | EA |
| Dual Entry | TP $10 kaki #1, trailing $25 kaki #2 | EA |
| **Penjaga basket** | **−$110** | **brain (`book.py::BasketGuardian`)** |
| **Alarm laju basket** | **>6 per 24 jam** | **brain — peringatan, bukan pemutus** |
| Stop harian / lantai ekuitas | $250 / $50 | `monthly_governor` |

**Kenapa penjaga di brain itu ada.** SL $75 Semi Marti tidak ada di broker — posisi
duduk di sana dengan `sl=0.00`. Satu-satunya yang menutup basket rugi adalah EA yang
hidup di chart. Itu titik kegagalan tunggal untuk satu-satunya stop yang ada, dan EA
lebih mudah terganggu daripada dugaan siapa pun: 2026-08-26 dia ter-restart tiga kali
dalam satu jam hanya karena memasang ulang, mengganti timeframe, dan menutup dialog
F7. Pada 2026-07-05 reboot VPS menghidupkan MT5 **tanpa EA di chart** dan tidak ada
yang sadar selama 2,5 hari. Brain adalah proses terpisah yang selamat dari semua itu.

Penjaga menyala di −$110, jauh di belakang SL EA ($75) dan di belakang rugi terburuk
yang pernah terjadi (−$75.34). Jadi dalam operasi normal dia **tidak pernah menyala**
— kalau sampai menyala, itu sendiri alarmnya. Lihat `fired_count` di `/health`.

**Kenapa alarm laju ada.** Setelan live memakai `InpRequireBreakConfirm=false`, yang
membuka posisi pada sinyal mentah. Yang menahannya cuma filter berita EA — dan filter
itu gagal DIAM-DIAM kalau kalender MT5 tidak termuat. Laju normal 1–2 basket/hari;
tanpa rem itu 28/hari, dan run tick-asli 8 minggu pada laju tersebut berakhir dengan
akun habis. Alarm >6 memisahkan keduanya dengan jarak lebar.

**Broker ini selalu membalas `retcode 0`** pada setiap order, buka maupun tutup,
padahal eksekusinya berhasil. Karena itu EA memulihkan tiket dengan memindai posisi
(`PULIH: ...`) dan penjaga Python memverifikasi penutupan dengan **membaca ulang buku
posisi, bukan kode balasan**. Jangan pernah menulis kode di sini yang mempercayai
retcode.

---

## Kalau GitHub juga hilang

Simpan salinan OFF-VPS secara berkala:

```
git bundle create quant-backup.bundle --all
```

Satu file itu berisi seluruh riwayat repo. Pulihkan dengan
`git clone quant-backup.bundle C:\Quant`. Simpan bersama `.env` (kalau slot vision
dipakai) dan salinan `SemiMartiV10_LIVE.set`. **Jangan pernah unggah bundle berisi
`.env` ke tempat publik.**
