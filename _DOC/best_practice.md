# Best Practice — sistem trading kuantitatif

Disusun 2026-08-11 dari kesalahan nyata di proyek ini, bukan dari teori. Tiap aturan
punya bukti angkanya. Baca ini sebelum memasang strategi baru, atau saat ada EA yang
kelihatan meyakinkan.

---

## Bagian 1 — Menguji strategi

### 1.1 Uji siklus penuh, jangan pernah cuma periode terbaru

Ini aturan tunggal yang paling sering menyelamatkan uang di sini.

| | periode terbaru | siklus penuh |
|---|---|---|
| EA Semi Marti Cuan v10 | 2026: **+23,7%** | 2021–2025: **−100%, modal habis** |
| Eterna | 2026: $2.063 dari 46 trade | 2021–2025: $726 dari 538 trade |

Eterna 2026 menghasilkan **14× lipat** rata-rata tahun sebelumnya bahkan setelah
dinormalkan terhadap harga emas. **74% dari seluruh profit 5,5 tahun berasal dari 6
bulan.** Angka yang dipakai untuk ekspektasi harus versi tanpa periode anomali.

Vendor EA selalu menunjukkan periode terbaiknya. Itu bukan penipuan — itu default.

### 1.2 Hitung winrate impas sebelum terpukau winrate

```
winrate impas = rata_rugi / (rata_menang + rata_rugi)
```

EA tadi: menang $5,05, rugi $26,91 → butuh **84,2%** hanya untuk impas.
2026 dapat 89,8% (untung). 2021–2025 dapat 79,9% (ludes).

**Winrate 80% selama 5 tahun tetap menghabiskan akun.** Hitung ambang ini dulu; sepuluh
detik dan langsung membongkar sebagian besar EA winrate-tinggi.

### 1.3 Tetapkan kriteria lulus SEBELUM menjalankan tes

Saat menguji konfirmasi S/R untuk eterna, kriteria ditulis lebih dulu: net naik ≥25%,
trade tersisa ≥60%, bulan hijau tidak turun.

Varian `A_ruang` menghasilkan PF **1,96** (dari 1,19) dan drawdown turun ke −4,5%.
Terlihat seperti penemuan. Tapi dia menyisakan **55 dari 538 trade** — 11 setahun. Itu
memetik pemenang, bukan menyaring.

Tanpa kriteria yang ditulis di depan, itu akan dilaporkan sebagai keberhasilan.

### 1.4 Hitung jumlah percobaan — Deflated Sharpe menghukum pencarian

Makin banyak kombinasi dicoba, makin tinggi ambang Sharpe yang harus dilewati agar
hasilnya bermakna. Eterna: **~1900 percobaan → DSR 0,0061** (butuh >0,95). Edge-nya
tidak bisa dibedakan dari keberuntungan mencari.

Akibatnya yang berlawanan dengan naluri: **menyapu parameter membuat strategi terlihat
lebih baik sambil membuat buktinya lebih lemah.** Kalau menyapu 20 varian sampai ada
yang lolos, yang ditemukan adalah varian yang cocok dengan data itu.

Sekali tes atas hipotesis yang punya alasan = 1 percobaan. Itu murah dan sah.

### 1.4b Kalahkan beli-dan-tahan, atau tidak usah

Kriteria yang paling sering hilang, dan yang paling cepat membongkar strategi emas.

Pola waktu "masuk 18:00 UTC, tahan 8 jam, LONG" lolos **keempat** kriteria: tanda
konsisten di kedua periode, PF out-of-sample 1,36, maxDD −28%, Calmar portofolio
1,94 → 2,67. Lalu:

```
beli-dan-tahan emas 2024-2026 (tanpa biaya)  : +$1.976,68
pola jam 18 (638 trade, dengan biaya)        : +$1.730,81
```

**Kalah dari tidak melakukan apa-apa**, setelah 638 kali membayar spread.

Emas naik 2,2× di periode uji. Apa pun yang long-biased akan mencetak uang di situ.
Itu menjelaskan pola yang berulang di SEMUA kandidat: eterna 74% profit dari 2026,
H1 acceleration seluruh profitnya dari 2025-26, EA martingale +23,7% di 2026 dan
−100% lima tahun. Bukan lima temuan berbeda — satu rezim yang sama dari lima sudut.

### 1.4c Tanyakan: apakah trade ini BISA benar-benar terjadi?

Kriteria statistik tidak cukup. Pencarian pola waktu menemukan "masuk 21:00 UTC,
tahan 8 jam" dengan PF out-of-sample **1,69** dan Calmar portofolio 1,94 → **3,37**.
Lolos semuanya. Lalu ketahuan: **FBS menutup XAUUSD jam 21:00–22:00 UTC.**

```
tick per jam UTC, 10 hari:  jam 20  36.223   jam 21  0   jam 22  45.038
bar M1 Dukascopy per jam :  jam 21  16,4     jam lain ~49
```

Order tidak mungkin dipasang saat pasar tutup. Data Dukascopy punya sisa print tipis
di situ dari batas sesi feed lain, dan "pola" itu sebenarnya gap melintasi jeda harian.

Sebelum memercayai hasil apa pun: cek jam perdagangan nyata, cek kelengkapan data per
jam, cek likuiditas di jam entry. Jam dengan tick jauh di bawah normal punya spread
yang tidak terwakili oleh angka yang diukur siang hari.

### 1.5 Periksa konsentrasi

Buang 5 trade terbaik. Masih untung?

- ORB: +$1.837 → +$1.484 tanpa 5 terbaik. **Lolos.**
- Eterna: 74% profit dari satu jendela 6 bulan. **Gagal.**

### 1.6 Pastikan kerangka ujimu memuat titik kerja NYATA strategi itu

Kesalahan hari ini: menguji EA dengan grid TP/SL 5–30 padahal titik kerja aslinya SL
keranjang **$70**, dan memakai TP/SL tetap padahal EA keluar bertingkat lewat dua posisi.
Kesimpulannya kebetulan benar, tapi alasannya salah.

Sebelum menyimpulkan "tidak ada edge", tanyakan: **apakah aku menguji yang sebenarnya
dia lakukan?**

---

## Bagian 2 — Kode yang benar-benar jalan

### 2.1 Backtest dan kode live harus strategi yang SAMA

RSI2 punya empat cacat parity, semuanya ada di live dan tidak satu pun dimodelkan
backtest:

| cacat | efek |
|---|---|
| batas hari UTC vs broker UTC+3 | +$1.531 → +$830 |
| disaster stop 5% tidak dimodelkan | → +$654 |
| bug re-entry setelah stop | → **+$291**, maxDD −28,7% → **−49,4%** |
| memutuskan dari bar yang masih terbentuk | belum terukur |

Gabungan: **−81%** dari angka yang dipercaya. Portofolio Calmar 2,99 → 0,78.

Sebelum memercayai backtest, baca kode live-nya berdampingan dan daftar tiap
perbedaannya.

### 2.2 Validasi tiap reimplementasi terhadap hasil yang sudah diketahui

Port sinyal EA ke Python: **29 → 35 → 47** sinyal terhadap acuan 49. Baru dipakai
setelah 47.

Kunci parity-nya cuma ketahuan dari membaca kode: filter jam EA melakukan *early
return* sebelum mesin keadaan dijalankan — jadi di luar jam, state **dibekukan**, bukan
sekadar entry ditolak. Salah satu baris itu = 29% selisih.

Preseden mahal: ORB/ZREV pernah ditulis ulang dan menghasilkan **0 trade diam-diam**
karena `resample("1D")` menyisipkan akhir pekan kosong lalu `rolling(50)` jadi NaN semua.

### 2.3 Anti-lookahead

- Gate timeframe lebih tinggi: **`shift(+1)` sebelum reindex.** `ffill` tanpa shift =
  lookahead 55 menit — itu yang membunuh strategi Golden.
- Pivot di bar `i` baru boleh dipakai sejak bar `i+k` (butuh k bar kanan untuk
  konfirmasi).
- Indikator dari bar **tertutup**, jangan dari bar berjalan.

### 2.4 Tes yang tidak bisa gagal lebih berbahaya daripada tes yang gagal

Skrip verifikasi reboot melaporkan **LULUS** dengan mengutip deal dari *sebelum* reboot.
`deal.time` MT5 memakai waktu server (UTC+3) sementara pembandingnya epoch lokal —
selisih 3 jam meloloskan data lama.

Vonisnya kebetulan benar. Tapi kalau sistemnya gagal, laporannya tetap akan bilang LULUS.

Perbaikannya bebas zona waktu: bandingkan **nomor tiket**, bukan waktu.

---

## Bagian 3 — Menjaga sistem hidup

### 3.1 Tidak boleh ada yang bergantung pada jendela yang terbuka

2026-08-10: watchdog dijalankan dari terminal. Terminal ditutup → seluruh rantai mati →
**15 jam senyap, nol trade.** Watchdog menjaga semuanya; tidak ada yang menjaga watchdog.

Pola yang dipakai sekarang: **Task Scheduler repetisi 5 menit + `IgnoreNew` + mutex**.
Kalau watchdog hidup, tick berikutnya diabaikan; kalau mati, dihidupkan. Penjaga-nya-
penjaga tanpa kode tambahan.

Tiga jebakan yang memakan waktu:
- Repetisi **wajib** punya trigger `-Once` berwaktu lampau. Yang hanya menempel di
  `AtLogOn`/`AtStartup` baru hidup setelah login berikutnya — sesaat setelah dipasang,
  tidak ada jaring pengaman sama sekali.
- `LogonType` wajib **Interactive**, bukan S4U. MT5 Python API tidak bisa menyeberang sesi.
- `[TimeSpan]::MaxValue` diterima saat membuat trigger tapi **ditolak saat mendaftarkan**.

### 3.2 Kegagalan diam adalah kegagalan termahal

Daftar yang pernah terjadi di sini, semuanya "hijau" saat rusak:

- EA lepas dari chart saat MT5 restart — semua monitor tetap hijau
- EA memakai `ServerSymbol` bawaan yang salah — rajin heartbeat, tak pernah terima sinyal
- `xau_executor` mati — brain tetap menghitung, `/health` hijau, **nol order terkirim**
- terminal64 duduk di dialog login — cek proses tetap lolos
- eterna diblokir zrev — tampak identik dengan eterna yang memang tidak punya sinyal
- auto-backup gagal push berhari-hari — tidak ada yang tahu

Aturannya: **untuk tiap komponen, tanyakan "kalau ini mati diam-diam, dari mana aku
tahu?"** Kalau jawabannya tidak ada, buat detaknya.

### 3.3 Bedakan sebab "tidak ada trade"

Tiga hal yang tampak identik dan artinya sangat berbeda:

1. strategi memang sedang menunggu (normal)
2. sistemnya mati (darurat)
3. entry diblokir sleeve lain (keputusan desain, harus terlihat)

`kesiapan.py` menjawab (1) dengan menyebutkan **apa** yang ditunggu dan **seberapa
jauh**. Tag `BLK` di jurnal menjawab (3).

### 3.4 Cadangan harus membuktikan dirinya

Auto-backup gagal push selama berhari-hari karena Git Credential Manager kehilangan
token — diam-diam. Sekarang `last_push_ok.txt` **hanya** ditulis saat push benar-benar
sukses, dan watchdog berteriak kalau umurnya >36 jam.

Jangan simpulkan "aman" dari fakta skrip backup pernah jalan.

---

## Bagian 4 — Menaikkan edge

Cuma ada tiga tuas. Diurutkan dari yang paling berdampak.

### 4.1 Tambah aliran yang tidak berkorelasi — satu-satunya makan siang gratis

Sharpe naik seakar jumlah edge independen.

```
2 sleeve sekarang            Sharpe 1.30
3 sleeve tak berkorelasi    ~Sharpe 1.59   (+22%)
4 sleeve                    ~Sharpe 1.84   (+41%)
```

Korelasi ORB↔ETERNA +0,05 memotong maxDD dari 35,2% (kalau dijumlah) jadi 21,8%. Gratis.

Syaratnya berat: edge-nya harus **benar-benar ada**. Kandidat yang gugur di sini —
EA martingale, restrukturisasi TP/SL, konfirmasi S/R, tiga sleeve hipotesis ekonomi
(overnight, turn-of-month, gotobi).

### 4.2 Kurangi biaya — satu-satunya perbaikan tanpa risiko statistik

Tiap sen spread dan slippage yang dihemat masuk langsung ke hasil, tanpa menaikkan N.

`orb_stop_manager` memakai pending STOP, bukan market order lewat EA. Slippage EA dulu
4,75–16,30 poin per trade; sekarang fill tepat di level. Sensitivitas ORB: tiap 2 poin
biaya ≈ **$410** sepanjang periode uji.

### 4.3 Modal — membeli daya tahan, BUKAN edge

Jangan tertukar. Kalau lot naik sebanding modal, **return dan drawdown dalam persen
tetap sama**:

```
modal $ 1.000  lot 0.03/0.01   CAGR 36.9%  maxDD -21.8%  Calmar 1.70
modal $ 5.000  lot 0.17/0.03   CAGR 32.8%  maxDD -21.5%  Calmar 1.53
modal $20.000  lot 0.68/0.12   CAGR 32.8%  maxDD -21.5%  Calmar 1.53
```

Yang dibeli modal adalah **kelangsungan hidup**. Di $548, satu trade ORB persentil-90
memakan 7,5% akun; trade terburuk eterna dalam sejarah ($168) akan memakan 31%.

> **Catatan penting:** inverse-vol *bukan* selalu lebih baik. Bobot "benar" untuk buku
> ini adalah ORB 85% / ETERNA 15%, tapi memasangnya menurunkan Calmar 1,70 → 1,53.
> Inverse-vol hanya melihat risiko, buta terhadap return. Rasio 3:1 yang terpasang
> secara kebetulan lebih baik.

### 4.4 Yang TIDAK menaikkan edge

Menyapu parameter. Dibuktikan tiga kali dalam satu hari: 90 konfigurasi EA (nol untung),
36 struktur TP/SL (nol untung), 3 konfirmasi S/R (nol lulus).

---

## Bagian 5 — Checklist sebelum memasang strategi baru

```
[ ] Baca kodenya. Cari: ada SL? ukuran lot bagaimana? apa yang terjadi saat rugi?
[ ] Hitung winrate impas dari rata-rata menang/rugi
[ ] Uji siklus penuh 5 tahun, bukan cuma tahun terbaik
[ ] Buang 5 trade terbaik - masih untung?
[ ] Berapa tahun yang rugi?
[ ] Hitung DSR dengan jumlah percobaan yang JUJUR
[ ] Bandingkan kode live vs backtest baris demi baris; daftar tiap perbedaan
[ ] Korelasi terhadap sleeve yang sudah ada - kalau >0,5, dia bukan diversifikasi
[ ] Kalau mati diam-diam, dari mana aku tahu? Buat detaknya
[ ] Tetapkan kriteria lulus SEBELUM melihat hasil
```

---

## Bagian 6 — Sikap

**Kalau instingmu bertabrakan dengan analisis, periksa analisisnya.** Dua kali hari ini
insting user benar dan analisis salah: mematikan zrev (angkanya ternyata mendukung,
−$742 sumbangan bersih), dan teguran "terlalu ruled based" (grid uji memang tidak
memuat titik kerja nyata EA).

**Kesimpulan benar dengan alasan salah tetap berbahaya** — dia akan menyesatkan di kasus
berikutnya.

**Angka yang menyenangkan pantas dicurigai lebih keras daripada angka yang mengecewakan.**
+344% dalam 6 bulan, PF 1,96, winrate 89,8% — ketiganya terlihat hebat dan ketiganya
artefak.

**Bukti yang belum tercemar hanya yang belum terjadi.** Eterna dan ORB sudah dicari
habis-habisan di data lama. Setiap trade live ke depan adalah out-of-sample sungguhan,
dan itu tidak bisa dibeli dengan riset tambahan.
