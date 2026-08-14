# Forward test — mulai 2026-08-14

Kriteria di dokumen ini **ditetapkan sebelum melihat hasil apa pun**. Itu inti dari
forward test: tanpa kriteria yang ditulis di depan, hasil apa pun nanti akan
terdengar masuk akal dan kita cuma menonton, bukan menguji.

## Titik awal

| | |
|---|---|
| tanggal mulai | 2026-08-14 (UTC) |
| balance awal | **$523.28** |
| akun | FBS-Demo 106271896 |
| commit | `8829fba` |

## Buku yang diuji

| sleeve | magic | mekanisme | lot / risiko |
|---|---|---|---|
| orb30_nas | 920617 | pending STOP di batas range NY | 0.01 tetap |
| eterna_xau | 920627 | dual-Supertrend H1, market | 0.01 tetap |
| smc_xau | 920643 | OB+BOS+FVG H4, limit + expiry | 0.01 tetap |
| smc_xau_h1 | 920644 | OB+BOS+sweep H1, konfirmasi M5 → market | risk 2%, lot_maks 0.05 |

Lapis LLM: `smc_rr` (baca berita + sesuaikan SL/TP/exit sebelum order) dan `advisor`
(verdict chart setelah posisi/zona). Keduanya **tidak memblokir** apa pun; agent RR
menyesuaikan angka di dalam batas keras, advisor murni mencatat.

## Yang DIHARAPKAN, dari backtest

Per 3 bulan (~63 hari perdagangan):

| sleeve | trade/tahun | perkiraan 3 bulan |
|---|---|---|
| ORB | ~120 | ~30 |
| ETERNA | ~106 | ~26 |
| SMC H4 | ~18 | ~4-5 |
| SMC H1 | ~39 | ~10 |

**Total ~70 trade.** Itu cukup untuk menilai BUKU, tapi **tidak cukup** untuk
memvalidasi SMC sendiri (10-15 trade). Jangan menyimpulkan apa pun tentang edge SMC
dari 3 bulan.

## Apa yang sebenarnya bisa dibuktikan forward test ini

**Bisa dibuktikan (dan inilah tujuannya):**
- Paritas live vs backtest: apakah order yang dikirim sama dengan yang dihitung riset
- Mekanisme berjalan: limit+expiry, konfirmasi M5, sizing, batas harian, agent RR
- Ketahanan: proses hidup terus, pulih dari gangguan
- Biaya nyata API vs perkiraan

**TIDAK bisa dibuktikan:**
- Apakah SMC punya edge. Butuh ratusan trade; 3 bulan memberi belasan.
- Apakah DSR 0.629 itu keberuntungan atau bukan.

## Kriteria — ditetapkan di depan

### HENTIKAN (matikan sleeve, selidiki) kalau salah satu terjadi

1. **Cacat paritas**: satu saja order live yang harga/arah/SL/TP-nya tidak cocok
   dengan yang dihitung `_setup_terkini` untuk zona yang sama.
2. **Equity turun 25%** dari $523.28 → di bawah **$392.46**.
3. **Satu trade merugi lebih dari 20% akun** (~$105) — berarti ada yang salah di
   sizing atau governor, bukan sekadar sial.
4. **Sleeve trade di luar aturannya**: SMC lebih dari 2 trade/hari; ORB di luar
   jendela sesi; entry tanpa konfirmasi M5 pada 920644.
5. **Agent RR menggeser SL/TP melewati batas keras** tanpa ditolak.

### LANJUTKAN kalau tidak ada di atas

Termasuk kalau rugi. Rugi dalam batas wajar **bukan** alasan menghentikan — backtest
sendiri memperkirakan 85% hari tanpa trade SMC dan 3 tahun beruntun tanpa hasil di
regime datar.

### EVALUASI pada 2026-11-14 (3 bulan)

Yang diperiksa, berurutan:

1. **Paritas** — nol cacat. Ini kriteria utama.
2. **Jumlah trade** dalam rentang wajar dari perkiraan di atas (±50%). Terlalu sedikit
   berarti ada yang menghalangi; terlalu banyak berarti aturannya bocor.
3. **Mekanisme** — semua terbukti pernah berjalan: limit terisi, limit kedaluwarsa,
   konfirmasi M5 memicu entry, sizing menaikkan lot, batas harian memblokir,
   agent RR menyesuaikan DAN ditolak batas keras minimal sekali.
4. **Biaya API nyata** vs perkiraan $13.61/tahun.
5. Arah PnL — dicatat, **tapi tidak dipakai untuk memutuskan**. Sampelnya terlalu kecil.

## Catatan kejujuran

Yang dipasang di sini **tidak lolos ambang statistik**: DSR SMC 0.629 versus ambang
0.95, dengan 20+ trial dilaporkan. Itu diketahui dan diterima user secara sadar
setelah keberatan disampaikan. Forward test ini **bukan** untuk membuktikan edge-nya —
itu butuh bertahun-tahun. Dia untuk membuktikan bahwa yang berjalan di akun sama
dengan yang dihitung di riset, dan bahwa mesinnya tidak rusak diam-diam.

Pantau dengan: `python _MONITOR/cek_forward.py`
