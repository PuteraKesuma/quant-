# Audit sistem live + forward model — 2026-07-17 (Fable → handoff ke Opus)

Konteks: user forward-test di FBS-Demo 106271896 sampai akhir Juli, lalu isi ~$1000 di Agustus.
Semua angka di bawah bisa direproduksi: `research/final_1000.py`, `research/golden_check.py`,
`research/book_1000_full.py`, `research/compound_1000.py`, `research/forward_model.py`.

## 1. Status live TERVERIFIKASI (07-17 pagi)

| Sleeve | Magic | Jalur | Status | Bukti |
|---|---|---|---|---|
| Z (Donchian-20 XAU) | 920622 | brain `run_server` (PID 1084) → EA | ON + gate ADX-28 baru | log: `daily ADX(14)=37.9 (min 28 -> TRADE)`; entry short 07-17 06:35 |
| ORB-NAS | 920617 | `orb_stop_manager` (PID 5680), pending STOP asli | ON | entry 07-17 03:23 → +$5.08. Slot config `enabled:false` = SENGAJA (2026-07-04, manager yang punya entry — bukan bug) |
| Reversal-US100 RSI-2 | 920633 | task harian 06:15 | ON | entry BUY 07-17 09:15 |
| Golden | 920626 | brain | **OFF 2026-07-17** | artifact — lihat §3 |

Book live == book yang di-backtest (Z ADX28 + ORB + Rev). Compounding 1 lot-step/$1500 sudah
terpasang lama via `lot_per_balance: 0.0000067` — tidak perlu diubah untuk $1000.

## 2. Jawaban untuk "hijau setiap saat, kurva pasti naik"

**Tidak ada dan tidak akan ada.** Fakta historis book terbaik kita sendiri (62 bulan kalender):
**23/62 bulan MERAH (37%)**, median +$27/bulan @0.01 lot, bulan terburuk −$245 (2025-03).
Dua strategi yang backtest-nya tampak "selalu hijau" — mr_xau (PF palsu 2.74 → asli 0.72) dan
Golden (PF palsu 2.14 → asli 1.04) — dua-duanya artifact. **Kurva yang tidak pernah turun adalah
tanda bug, bukan tanda edge.** Yang bisa dijamin sistem: hijau per-TAHUN (6/6 in-sample), DD
ter-bound, dan kill-switch yang memberhentikan sistem kalau perilakunya keluar dari model (§5).
JANGAN pernah men-deploy konfigurasi baru karena backtest-nya "selalu hijau" — itu red flag utama.

## 3. Golden = artifact (dinonaktifkan 07-17)

Semua riset Golden (`semi_marti_*.py`, `regime_adaptive.py:34,46`, `regime_sizing.py:32,43`)
me-reindex gate H1 (trend EMA15 + ADX) ke M5 dengan ffill TANPA shift → lookahead s/d 55 menit
(label resample = AWAL bar). Sinyal mentah rugi; kedua gate itu = seluruh "edge" → edge = lookahead.
Jujur: PF 1.04, 3/6 tahun hijau, 2026 = tahun terburuknya (−$312). Di portfolio $1000: maxDD
−19% → −39%, 6/6 → 4/6. `regime_sizing.py` bug sama → aturan lot 0.02 ADX<20 fiktif.
**Syarat re-enable: rerun riset dengan gate H1 di-shift +1 bar, dan nilai efek MARGINAL-nya ke
DD/Sharpe book, bukan PF standalone.**

## 4. Forward model (block-bootstrap MC, 5000 path, blok 20 hari; `forward_model.py`)

Dari $1000, aturan lot-step $1500, base 0.01 lot:

| Skenario | P(bulan merah) | Bulan p5..p95 | 12-bln median | 12-bln p5 | maxDD med/p5 | P(DD<−19%) | P(tahun merah) |
|---|---|---|---|---|---|---|---|
| Semua regime 2021-26 | 37% | −$99..+$419 | $1.615 | $896 | −15% / −37% | 35% | 9% |
| Regime kurus (2021-23 saja) | 46% | −$67..+$98 | $1.114 | $844 | −13% / −27% | 18% | 25% |
| Tanpa Z (ORB+Rev saja) | 26% | −$55..+$101 | $1.300 | $971 | −8% / −28% | 17% | 6% |

Stabil terhadap panjang blok (10/40 hari: maxDD median −15/−16%). Interpretasi:
- Ekspektasi jujur 12 bulan: **median +60%, tapi 1-dari-11 path tetap merah**, dan sepertiga path
  menyentuh DD>19%. Backtest single-path ($9.620) itu tail atas, bukan ekspektasi.
- Kalau setahun ke depan seperti 2021-23 (emas tak trending): median cuma +11%, P(rugi) 25%.
  Book bertahan (ORB+Rev menopang), tapi TIDAK menghasilkan banyak. Itu perilaku normal, bukan rusak.
- MC mencampur hari 2021-26; regime yang belum pernah terjadi tidak ada di distribusi ini.

Risiko tersembunyi yang tidak ada di backtest realized-only: **Reversal stopless** — floating MAE
(dari close harian, jadi UNDERestimate): p95 −$53, terburuk −$282, hold sampai 19 hari; disaster
stop 5% ≈ −$147 realized @0.01 lot bila kena. Di akun kecil ini risiko margin nyata.

## 5. Kill criteria (pre-registered — supaya evaluasi tidak pakai perasaan)

Basis 0.01 lot. Bandingkan realisasi live vs distribusi model:
1. **WARNING**: bulan kalender < −$99 (p5 model) → audit eksekusi + regime; jangan langsung ubah strategi.
2. **WARNING**: 2 bulan berturut < −$22 (p25) → cek edge decay per-sleeve.
3. **HALT semua entry**: DD akun dari peak > 25% (di luar median model −15%, mendekati p5) → stop, audit penuh, jangan menambah size untuk "mengejar".
4. **BUG ALERT**: Z entry saat ADX harian < 28, atau Z reverse melawan gate → periksa `_daily_adx`.
5. **INFRA ALERT**: ORB tanpa satu pun trade 5 sesi berturut saat governor tidak pause → cek `orb_stop_manager` hidup.
6. **MARGIN ALERT**: floating Reversal < −$100 di akun <$500 → pantau margin, jangan intervensi posisinya.
Evaluasi akhir Juli = kepatuhan pada model & aturan (poin 1-6), BUKAN saldo.

## 6. Aturan operasional (pelajaran insiden sesi ini)

- **JANGAN restart brain / toggle slot selagi ADA posisi terbuka.** 07-17: disable Golden + restart
  → posisi Z ikut tertutup 3 detik kemudian oleh order ber-magic Golden (untung +$26.93, tapi itu
  keberuntungan). Flatten dulu atau tunggu flat.
- **JANGAN tambah 920633 (Reversal) ke `governor.magics`.** Set itu juga dipakai `_book_conflict`;
  menambahkannya membuat ORB/Rev saling blokir stacking searah yang justru DIIZINKAN book backtest
  → mengubah book vs yang diuji. Reversal di luar governor itu by-design; risikonya dibatasi
  disaster-stop 5% + monitoring poin 6.
- Slot ORB `enabled:false` = arsitektur, jangan "diperbaiki" jadi true (dobel entry EA+manager).
- Standing: EA tidak pernah disentuh; `pipeline/backtest/strategy_orb.py` read-only; jangan commit
  `.env`/jurnal.md; git = copy ke `C:\Quant_push\repo`, add eksplisit.
- Setiap backtest baru: gate lintas-timeframe WAJIB di-shift (2 artifact terakhir dua-duanya
  lookahead); DD% selalu dihitung terhadap kurva equity path-dependent, bukan equity awal.

## 7. Yang TIDAK perlu dikerjakan (sudah diuji, hasil negatif)

- Trailing/monthly circuit-breaker di book: DD multi-bulan, CB bulanan nol efek; trailing memotong
  return 53%→9% dan robustness 6/6→4/6 (`compound_1000.py`).
- SL lebih ketat di Reversal (1% stop → PF 0.99) dan ORB 0.5R (kolaps, Sh 0.65) (`sl_sweep.py`).
- Gate ADX < 28 untuk Z bila targetnya all-green (18/22/25 semua menyisakan 1 tahun merah).
- ML overlay (meta-label AUC ~0.5 di Z, LIQ, FX — berkali-kali).
