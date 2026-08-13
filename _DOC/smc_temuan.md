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
