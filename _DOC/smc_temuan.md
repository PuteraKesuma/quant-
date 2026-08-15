# SMC di XAUUSD — hasil uji, 2026-08-13

Permintaan: sleeve baru berbasis Smart Money Concepts + follow-the-trend, limit order
di zona Order Block dengan expiry, plus lapis sentimen/berita lewat Claude.

Yang diuji di sini **hanya mesin SMC deterministik** (lapis A). Lapis berita ditunda
sampai lapis A terbukti.

## Yang sudah pasti secara teknis

- FBS-Demo XAUUSD: `expiration_mode = 15` → `ORDER_TIME_SPECIFIED` didukung.
  **Limit order dengan expiry bisa dibangun.**
- `trade_stops_level = 0` → limit boleh dipasang sedekat apa pun ke harga.
- `filling_mode = 3` → FOK dan IOC tersedia.

## Cara SMC ditulis (mekanis, bukan tafsir gambar)

| primitif | aturan |
|---|---|
| swing pivot | fractal ±k bar. **Anti-lookahead**: pivot di bar `i` baru dipakai dari bar `i+k` |
| BOS | close menembus pivot terkonfirmasi. **Peristiwa, bukan keadaan** — satu level hanya sekali |
| Order Block | candle berlawanan arah terakhir sebelum impulse; zona = `[low, high]` |
| FVG | `low[t+1] > high[t-1]` (bullish) di dalam leg impulse |
| sweep | wick menembus pivot lawan lalu close balik ke dalam |

Entry = BUY/SELL LIMIT di ujung zona OB (mitigation entry). SL di luar zona + buffer.
TP = R × jarak SL. Order kedaluwarsa setelah `expiry` bar kalau tidak kena.

Biaya: spread $0,50/trade, swap LONG −$0,6995/malam, SHORT +$0,2491, Rabu 3×.

## Hasil: 4 konfigurasi × 4 timeframe = **16 trial**

13 dari 16 rugi atau tidak bermakna. Satu menonjol — **H4 config B (OB+BOS+FVG)**:

```
n=96   net +$630.93   PF 1.81   winrate 43%   maxDD -14.8%   5/6 tahun hijau
margin di atas winrate impas: +13.5 poin      DSR 0.629
```

## Uji ketahanan

**Dataran parameter — LOLOS.** 21/21 varian untung, dan acuan bukan puncak di sumbu
mana pun (k=2, expiry=24, rr=3 semuanya lebih untung). Kalau ini hasil penyetelan,
acuan akan duduk di puncak. Dia tidak.

| sumbu | rentang diuji | net$ |
|---|---|---|
| k | 2–5 | +669 / +631 / +502 / +512 |
| expiry | 6–24 | +356 / +606 / +631 / +682 / +697 |
| rr | 1,5–3,0 | +363 / +631 / +644 / +725 |
| buffer | 0–0,20 | +594 / +600 / +631 / +477 |
| ob_lookback | 6–20 | jenuh di 8, tidak sensitif |

**vs beli-dan-tahan (swap dimodelkan) — LOLOS.** B&H kotor $2.140, tapi bayar swap
$1.400 (2.002 malam) → bersih **$740 dengan maxDD −62,4%**. H4-B $631 dengan maxDD
−14,8%. Per poin drawdown: **42,5 vs 11,9**.

**vs pembanding bodoh (tanpa OB, tanpa FVG) — LOLOS pada risiko setara.**

| | net$ | maxDD% | Sharpe | net/poin DD | net @DD 15% |
|---|---|---|---|---|---|
| H4-B | 631 | 14,8 | 0,89 | 42,5 | 629 |
| bodoh BOS market | 1.613 | 110,9 | 0,52 | 14,5 | 215 |
| bodoh BOS limit 30% | 1.010 | 101,9 | 0,52 | 9,9 | 147 |
| bodoh BOS market LONG | 1.532 | 63,1 | 0,88 | 24,3 | 360 |

Ini membatalkan dugaan bahwa OB+FVG cuma memperkecil partisipasi — kalau begitu,
rasio net/DD akan datar. Rasionya naik. Filternya menambah kualitas nyata.
**Tapi** Sharpe 0,89 vs 0,88 (bodoh-LONG) praktis sama; keunggulan itu bertumpu pada
maxDD, statistik jalur-terburuk yang jauh lebih berisik daripada Sharpe.

**Nilai portofolio — LOLOS.** Korelasi bulanan rendah: 0,25 dengan ETERNA, 0,11 dengan ORB.

| | CAGR% | maxDD% | Calmar | Sharpe | hijau% |
|---|---|---|---|---|---|
| ORB + ETERNA | 43,3 | −25,0 | 1,73 | 1,03 | 53 |
| + SMC | 47,4 | −24,2 | **1,95** | 1,14 | 59 |

## Yang TIDAK lolos

**1. Belah waktu — masalah terbesar.**

```
2021-2023 : 52 trade, net  -$9.54,  PF 0.97, winrate 35%
2024-2026 : 44 trade, net +$640.47, PF 2.30, winrate 52%
```

Tiga tahun pertama menghasilkan **nol**. Bukan "bekerja lalu ada masa sepi" — memang
tidak menghasilkan apa-apa selama 3 tahun.

**2. Semua pembanding bodoh punya belah waktu yang sama** (−489/+2.102, −420/+1.430,
−201/+1.734). Jadi *kapan* uang dihasilkan ditentukan rezim emas, bukan oleh SMC.
Yang ditambahkan SMC adalah *kualitas* per satuan risiko, bukan kemampuan menghasilkan
di rezim yang tidak mendukung.

**3. Konsentrasi.** Buang 1 trade terbaik → $427. Buang 3 → $132. Buang 5 → **−$46**.
Lima trade dari 96 menopang seluruh hasil.

**4. DSR 0,629, ambang 0,95.** Dengan 16 trial dilaporkan jujur, kita tidak bisa
membedakan ini dari keberuntungan pada tingkat keyakinan 95%.

**5. n=96 dalam 5,5 tahun** = ~17 trade/tahun. Sampel kecil.

## Kesimpulan

Bukti campuran, bukan negatif. Yang benar-benar kuat: dataran parameter, korelasi
rendah, perbaikan Calmar portofolio, dan keunggulan risk-adjusted atas pembanding
bodoh. Yang benar-benar lemah: tiga tahun nol, ketergantungan pada 5 trade, dan DSR
gagal di ambang.

**Rekomendasi: jangan dipasang live. Pasang sebagai shadow sleeve** — hitung sinyal,
catat limit order yang *akan* dipasang beserta expiry-nya, jangan kirim order.

Alasannya: DSR gagal berarti kita belum berhak menaruh uang, tapi bukti yang ada
cukup kuat untuk layak dikumpulkan datanya. Dan belah waktu memberi peringatan konkret
— kalau rezim berbalik seperti 2021-2023, sleeve ini bisa diam bertahun-tahun. Lebih
baik tahu itu dari log daripada dari saldo.

**Syarat promosi ke live** (ditetapkan di depan, sebelum melihat hasil):
- minimal 40 sinyal shadow terkumpul, DAN
- PF shadow ≥ 1,4, DAN
- korelasi bulanan dengan ETERNA tetap < 0,4, DAN
- Calmar portofolio simulasi tetap membaik

## Catatan proses

Tiga kesalahanku dalam analisis ini, semuanya kelas yang sama — mengutip angka mentah
tanpa penyebut risiko:

1. Menyebut ini "paku tunggal" sebelum menguji tetangga parameter. Salah — ini dataran.
2. Menyebut kalah dari beli-dan-tahan pakai angka kotor $2.140. Setelah swap: $740.
3. Menyebut pembanding bodoh menang karena $1.613 > $631. Drawdown-nya 110%.

Yang mencegah ketiganya jadi kesimpulan final adalah menjalankan ujinya, bukan
berhenti di intuisi pertama.

---

## Jurang frekuensi — diukur 2026-08-15

User berulang kali minta **1-2 trade/hari**. Ini angka yang sebenarnya dihasilkan
konfigurasi live, direplay dengan kelas produksi (`SmcLimitManager`) di data nyata:

| aliran | jendela | BOS | limit dipasang | terisi | trade/hari |
|---|---|---|---|---|---|
| `smc_xau` H4 (920643) | 60 hari | 14 | 4 | 0 | **0,00** |
| `smc_xau_h1` (920644) | 30 hari | 32 | 4 | 2 | **0,07** |

Meleset **20-30 kali lipat** dari yang diminta. Penyumbatnya bukan bug:

- filter *liquidity sweep* menolak **28 dari 32** BOS di H1
- filter FVG menolak **8 dari 14** BOS di H4

**Konsekuensi operasional: hari tanpa trade adalah keadaan NORMAL, bukan gejala
kerusakan.** Sebelum mencari bug ketika "kok gaada trade", replay dulu tabel BOS-nya
— itu membedakan "aturannya menolak" dari "sistemnya rusak". Dua kali sudah waktu
terbuang mencari bug yang tidak ada karena langkah ini dilewati.

### Contoh terverifikasi, 13-14 Agustus

Empat sleeve, empat alasan berbeda, semuanya benar:

| tanggal | sleeve | yang terjadi |
|---|---|---|
| 13 Agu 06:18 | SMC-H1 | SELLLIM 4412,90 sl 4453,20 tp 4332,29 dipasang. Tidak tersentuh, batal saat expiry |
| 13 Agu 14:00 | ORB | terisi, ditutup di akhir sesi **+$1,40** |
| 13 Agu 04:00 | ETERNA | flip SELL, tren H1 naik -> ditolak gate konservatif |
| 14 Agu | SMC-H4 | **nol BOS** - harga tidak menembus swing manapun |
| 14 Agu | SMC-H1 | 3 BOS, ketiganya ditolak filter sweep |
| 14 Agu 09:00 | ETERNA | flip BUY, tren H1 turun -> ditolak gate konservatif |
| 14 Agu 14:00 | ORB | pending dibatalkan - tembusan pertama melawan tren |

Order SMC-H1 itu sekaligus **bukti offset +3 sudah benar** pada tanggal tersebut:
harga limitnya cocok persis dengan replay yang dihitung memakai +3. Kalau offset
jatuh ke 0, angkanya pasti meleset. Log tidak bisa membuktikan ini (terhapus saat
restart) - ordernya yang membuktikan.

### Jalan keluar yang SAH

Melonggarkan filter sudah diuji **tiga kali** dan selalu rugi (longgarkan
timeframe/filter, MTF H4-bias+H1-entry, likuiditas sesi). Selektivitas itulah
edge-nya - menukarnya dengan frekuensi berarti membuang edge-nya.

Satu-satunya jalan yang tidak merusak statistik: **aturan yang SAMA PERSIS di
beberapa simbol** (`research/smc_multi_simbol.py`). Itu tetap 1 trial dengan lebih
banyak sampel, jadi memperKUAT bukti - bukan N parameter di 1 pasar, yang
memperLEMAH bukti lewat penalti DSR.

Status: ditunda user ke minggu setelah 2026-08-15. Jangan ubah sleeve yang sedang
forward test tanpa persetujuan.

---

## Multi-simbol: jalan keluar yang SAH dari jurang frekuensi (2026-08-15)

Aturan H1-C yang **identik** — OB + BOS + liquidity sweep, konfirmasi M5 12 bar, rr 2.0,
sl_maks $20 — dijalankan di lima pasar. **Tidak satu pun parameter disetel per simbol.**

| simbol | n | /hari | net$ | PF | WR | maxDD | tahun hijau |
|---|---|---|---|---|---|---|---|
| XAUUSD | 215 | 0,151 | +1.290,35 | 2,67 | 68% | -4,4% | 6/6 |
| EURUSD | 303 | 0,212 | +303,78 | 2,42 | 66% | -2,7% | 6/6 |
| GBPUSD | 310 | 0,217 | +150,66 | 1,36 | 59% | -5,8% | 5/6 |
| AUDUSD | 166 | 0,183 | -1,41 | 0,99 | 56% | -6,8% | 2/4 |
| NZDUSD | 158 | 0,174 | -67,06 | 0,60 | 49% | -14,8% | 1/4 |
| **GABUNGAN** | **1.152** | **0,806** | **+1.676,32** | **1,97** | 61% | **-4,7%** | **6/6** |

Frekuensi **0,07 -> 0,81 trade/hari** tanpa menyentuh satu pun aturan. Biaya spread dan
swap diambil langsung dari MT5 per simbol, bukan ditebak.

**Kenapa ini sah sementara melonggarkan filter tidak:**
1 aturan tetap x 5 pasar = **1 trial dengan 5 sampel** -> memperKUAT bukti.
5 parameter x 1 pasar = 5 trial -> memperLEMAH lewat penalti DSR.
Dan hasilnya konsisten: 3 dari 5 pasar untung, XAU/EUR keduanya 6/6 tahun hijau. Itu
tanda edge-nya **struktural**, bukan kebetulan cocok dengan emas.

### AUDUSD & NZDUSD sengaja TETAP dipasang meski rugi

Membuangnya setelah melihat hasilnya adalah **cherry-picking** - persis kesalahan yang
seluruh sistem ini dibangun untuk menghindari. Angka GABUNGAN sudah memikul bebannya,
dan itulah angka jujur untuk menilai forward test.
**Kriteria buang ditetapkan DI DEPAN:** hanya kalau PF forward < 1,0 DAN trade >= 20.

### Jatah harian bersama - wajib ada

`max_setups_per_day` bersifat PER SLOT, jadi 6 slot = 12 trade/hari, enam kali lipat
dari yang diminta user. `smc_budget.max_trades_per_day: 2` menghitung deal dari SELURUH
magic SMC digabung. Diuji dengan deal sintetis: trade di simbol mana pun memakan jatah
bersama, dan deal sleeve lain (eterna 920627) tidak ikut terhitung.

---

## LLM di SMC: apa yang benar-benar terjadi, dan kenapa TIDAK boleh jadi gerbang

**Agent RR terbukti JALAN** (diuji ujung-ke-ujung 2026-08-15, setelah kredit diisi):
tangkap 3 chart TradingView (H4/H1/M5) 23 detik, panggilan penuh 66 detik, web_search 2x,
verdict TAKE, **confidence 58**, 46.366 token masuk / 3.573 keluar = **$0,21 per panggilan**.
Catatan "0 dari 4" sebelumnya adalah era kredit habis, bukan cacat kode.

Dia mengembalikan `null` untuk sl/tp/expiry - artinya "angka mesin sudah benar". Itu
perilaku yang BENAR, bukan kegagalan.

### Kenapa confidence TIDAK dijadikan gerbang

Sistem ini **sudah pernah mencobanya dan gagal**. Dari komentar `vision_smc_xau` di
config.yaml (RETIRED 2026-07-01):

> LLM-as-decision-maker (entry gate via confidence) is UNVERIFIABLE by construction
> (no reproducible historical signal series -> can't be backtested), and in practice it
> just sat FLAT (conf 30-40 < gate 65) -> ~zero trades.

Dan uji hari ini mengembalikan **confidence 58** - di bawah gerbang 65 yang dulu dipakai.
Memasang gerbang sekarang akan mengulang persis kegagalan yang sama, di sleeve yang
frekuensinya memang sudah langka.

**Yang benar** (rencana yang sudah tertulis di config itu sendiri): catat confidence dan
verdict BERSAMA HASILNYA, jangan blokir apa pun. Setelah 30-40 setup terkumpul, ukur
apakah confidence rendah benar-benar memprediksi trade rugi. Kalau ya, ambangnya
ditentukan DARI DATA. Kalau tidak, lapisnya dibuang dan hemat biayanya.

Biaya kalau 2 setup/hari: ~$155/tahun. Itu nyata terhadap buku yang labanya puluhan
dolar per bulan, jadi lapis ini harus MEMBUKTIKAN dirinya, bukan diasumsikan berguna.

### Yang riset luar katakan tentang SMC

Pencarian web (Agustus 2026): klaim komunitas berkisar WR 50-65% dengan konfluensi ketat,
dan satu backtest 2.600 trade mengklaim WR 61,2% / PF 2,17 - tapi **tanpa metodologi,
tanpa rincian per aset, tanpa model biaya**, jadi tidak bisa diverifikasi.
Sisi akademis lebih jujur: yang punya dukungan penelitian adalah **fenomenanya**
(ketidakseimbangan order book, penggerombolan stop-loss), **bukan aturan dagangnya**.
Satu tinjauan menyimpulkan tidak ada dari 34 sumber terverifikasi yang mengesahkan
SMC/ICT sebagai sistem trading, dan bahwa keberhasilan SMC lebih karena ia memaksa
SL ketat + target jauh - keberhasilan MANAJEMEN RISIKO, bukan daya ramal.

Artinya: angka kita sendiri (PF 1,97 gabungan, biaya nyata dimodelkan, paritas diuji)
justru lebih ketat daripada yang dipublikasikan orang. Jangan tergoda meniru klaim
WR 60%+ dari internet.
