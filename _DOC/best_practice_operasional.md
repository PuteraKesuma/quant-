# Best practice operasional — menjaga sistem tetap aman

Dokumen ini soal **menjalankan** sistem dengan aman. Untuk best practice *riset*
(DSR, kalahkan beli-dan-tahan, uji paritas), lihat `_DOC/best_practice.md`.

Ditulis 2026-08-14 setelah sehari penuh menemukan bug yang semuanya punya satu ciri
sama: **tidak terlihat dari mana pun sampai diuji secara sengaja.**

---

## 1. Lapisan pengaman yang ada sekarang

Urut dari yang paling sering bekerja ke yang paling jarang:

| lapis | apa yang dijaga | apa yang TIDAK dijaga |
|---|---|---|
| SL & TP di broker | tiap posisi punya SL di sisi server | tidak menolong kalau SL-nya sendiri kelewat lebar |
| `max_risk_per_trade` $90 | menolak entry berisiko > $90 | 17% akun — longgar. Terukur memblokir 1 dari 99 trade H4 |
| batas 2 trade/hari SMC | mencegah beruntun di satu hari | dihitung dari riwayat deal MT5, bukan file state |
| rem harian $250 | jeda entry baru kalau rugi harian tembus | butuh `monthly_governor` HIDUP |
| rem kerugian maks $50 | tutup semua posisi kalau equity nyaris habis | butuh `monthly_governor` HIDUP |
| watchdog + Task Scheduler | menghidupkan ulang 6 proses | tidak menjaga MT5 login/Algo Trading |
| autologon | sesi desktop setelah reboot | password ada di LSA, bukan registry (benar) |

**Kesimpulan penting:** dua rem terakhir bergantung pada satu proses. Pada 2026-08-14
ditemukan proses itu **tidak pernah dijalankan** dan `governor.json` membeku sejak
6 Agustus — jadi rem harian dan rem kerugian maksimum **mati** tanpa tanda apa pun.
Sekarang sudah dijaga watchdog.

---

## 2. Rutinitas pemeriksaan

### Tiap hari (1 menit)
```
CEK_SISTEM.bat
```
Yang harus terlihat: **6 proses**, watchdog `Running`, detak < 2 menit, Algo Trading
`True`, SHA lokal = GitHub.

### Tiap minggu (5 menit)
```
CEK_FORWARD.bat
```
Bandingkan jumlah trade tiap sleeve dengan harapan di `_DOC/forward_test.md`.
**Terlalu sedikit** berarti ada yang menghalangi; **terlalu banyak** berarti aturannya
bocor. Keduanya sama-sama sinyal, bukan cuma yang merah.

### Tiap ada perubahan kode
```
python research/smc_paritas.py
```
Harus LULUS untuk kedua aliran. Ini satu-satunya alat yang membuktikan live masih
sama dengan backtest.

---

## 3. Tanda bahaya — dan artinya

| yang terlihat | kemungkinan artinya |
|---|---|
| `/health` hijau tapi tidak ada order berhari-hari | eksekutor mati, atau order ditolak diam-diam. Cek log, bukan `/health` |
| `governor.json` tanggalnya tidak hari ini | governor mati → dua rem terakhir tidak berfungsi |
| jumlah proses < 6 | watchdog belum sempat, atau ada yang crash berulang. Cek `_MONITOR/*_err.log` |
| offset broker berubah-ubah di log | deteksi offset bermasalah → bar bergeser sejam → sinyal salah |
| SMC trade > 2 dalam sehari | batas harian bocor. HENTIKAN sleeve |
| satu trade rugi > $105 | sizing atau governor bermasalah, bukan sekadar sial |
| agent RR menggeser SL/TP tanpa baris `ditolak` yang wajar | batas keras tidak bekerja |

---

## 4. Yang JANGAN dilakukan

Semua ini pelajaran dari bug nyata hari ini, bukan teori.

**Jangan percaya `/health` sebagai bukti sistem sehat.**
Brain crash karena tipe slot `smclimit`, tapi `/health` tetap hijau berjam-jam —
karena proses lama masih hidup dari sebelum slot itu ditambahkan. Yang membuktikan
sehat adalah **membunuh proses lalu melihat dia bangkit**, bukan membaca status.

**Jangan menambah slot ke config tanpa menguji brain bisa start ulang.**
Satu tipe yang tidak dikenal membuat `SignalEngine` melempar error dan brain tidak
akan pernah naik lagi setelah reboot.

**Jangan percaya komentar kode sebagai jaminan.**
`abs(diff - nearest) <= 0.5` diberi komentar "fresh, sane" padahal syarat itu
**selalu benar** menurut definisi `round()`. Penjaganya tidak menyaring apa pun
selama entah berapa lama.

**Jangan menyimpan hitungan penting di file state kalau ada sumber yang lebih benar.**
Batas 2 trade/hari sempat dihitung dari file state, dan order yang dipasang lalu
dibatalkan tetap memakan jatah. Riwayat deal MT5 tidak bisa basi; file state bisa.

**Jangan menambahkan sleeve ke `governor.magics` untuk memperbaiki akuntansi PnL.**
Field itu juga dipakai `_book_conflict` — menambahnya membuat sleeve saling
memblokir. Kesalahan itu pernah memakan −$1.526 entry eterna (ZREV). Pakai
`governor.pnl_magics` yang terpisah.

**Jangan melonggarkan aturan SMC demi frekuensi.**
Sudah diuji tiga kali secara independen: longgarkan filter/timeframe, MTF
H4-bias+H1-entry, likuiditas sesi. Ketiganya rugi. Edge-nya ADA di selektivitasnya.

**Jangan menaikkan lot karena saldo naik saja.**
Naikkan hanya kalau saldo **dan** bukti statistiknya tumbuh. DSR SMC masih 0.629
versus ambang 0.95.

---

## 5. Prosedur darurat

**Mau menghentikan semua entry baru, posisi lama dibiarkan:**
Edit `_MONITOR/governor.json` → `"paused": true`. Brain, ORB manager, dan SMC manager
semuanya membacanya. Posisi terbuka tetap dijaga SL/TP broker.

**Mau mematikan satu sleeve:**
`config.yaml` → slot itu → `enabled: false` → restart manager terkait. Untuk ORB,
`enabled` tidak dibaca manager; ubah `params.dry_run: true`.

**Mau mematikan semuanya:**
`AUTO_TRADING_OFF.bat`. MT5 tidak disentuh — posisi terbuka tetap dijaga SL/TP di
sisi broker.

**Kalau ragu apakah sistem masih waras:** jangan menebak dari `/health`. Jalankan
`CEK_SISTEM.bat`, lalu `research/smc_paritas.py`.

---

## 6. Yang TIDAK terlindungi — jujur

1. **Gap harga melewati SL.** SL ada di sisi broker, tapi gap akhir pekan atau rilis
   berdampak tinggi bisa mengisi jauh dari level SL. Tidak ada yang bisa mencegahnya.
2. **Lot minimum 0.01 adalah lantai.** Pada saldo $523, zona lebar $101 berarti risiko
   19% akun dan sizing tidak bisa menurunkannya. Hanya `max_risk_per_trade` yang
   menahan, di $90 (17%).
3. **Rem harian $250 = 48% akun.** Longgar untuk akun sekecil ini. Dia backstop, bukan
   pengelola risiko.
4. **DSR 0.629.** Ketiga sleeve dipasang meski bukti statistiknya belum lolos ambang.
   Itu keputusan sadar user setelah keberatan disampaikan — bukan sesuatu yang
   "sudah aman".
5. **Mode konfirmasi M5 belum pernah menghasilkan trade live.** Semua bagiannya sudah
   terbukti terpisah, tapi belum pernah berjalan utuh dari zona sampai posisi.
6. **Jalur reboot penuh belum diuji sejak perbaikan brain.** Bagian-bagiannya sudah
   terbukti; rangkaian utuhnya belum.

---

## 7. Aturan tunggal kalau harus memilih satu

**Setiap klaim "aman" harus punya cara untuk gagal.**

Hari ini ada dua uji yang hampir dilaporkan LULUS padahal tidak pernah benar-benar
jalan — perintah kill-nya membunuh dirinya sendiri, dan PID-nya tidak berubah sama
sekali. Uji yang tidak bisa gagal lebih berbahaya daripada tidak menguji sama sekali,
karena dia memberi rasa aman yang keliru.

Sebelum percaya sesuatu aman, tanyakan: *kalau ini rusak sekarang, apa yang akan
terlihat berbeda?* Kalau jawabannya "tidak ada", berarti belum diuji.
