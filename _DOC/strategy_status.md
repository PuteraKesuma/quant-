# Status Strategi — XAUUSD & NAS100 (riset 2026-06-26)

Divalidasi di data Dukascopy 1m **2021–2026** (XAU 1.94jt bar, NAS 1.87jt bar).
Split **In-Sample 2021–2024** vs **Out-of-Sample 2025–2026**. Semua R = satuan risiko (SL=−1R).
Semua di sini **sesuai arsitektur** = slot `type: orb` di `config.yaml live.strategies` (tanpa ubah kode).

## Inventaris slot ORB (yang nyata & teruji)

| Slot (magic) | Setup | OOS PF | OOS WR | Walk-fwd | Per-tahun | Vonis |
|---|---|---|---|---|---|---|
| **orb30_xau_asia** (920620) | XAU Asia 00:00, 30m, 1:1, exit 08:00 | **1.30** | 57% | 10/11 | semua + | ✅ **KEEPER** — paling robust, plateau lebar, nol outlier |
| **orb30_xau** (920618) | XAU NY 13:30, 30m, TP3/SL1, exit 20:00 | **1.21** | 43% | **11/11** | semua + | ✅ **SOLID** — konsisten tiap tahun (loss −27R kmrn cuma 1 variance) |
| **orb30_nas** (920617) | NAS NY 13:30, 30m, 1:1, +range_filter, exit 20:00 | **1.03** | 51% | 9/11 | 2025–26 **luruh** | ⚠️ **GANTI** — open fix 13:30 salah window krn DST; lihat edge baru di bawah |
| vision_smc_xau (920621) | AI vision | — | — | — | rugi | ⛔ **DITUNDA** — tak ada edge teruji |

## Kandidat retune (kalau mau, tetap type:orb — cuma ganti params)
- **orb30_nas → 60m TP1/SL0.5 +RF**: OOS PF 1.19 (vs 1.03 sekarang), tapi WF cuma 7/11. Sedikit lebih baik, belum meyakinkan.
- **orb30_xau → TP2/SL0.5**: OOS PF 1.22, +58R (vs +40R), tapi WR turun ke 33%. Lebih banyak R, lebih bergerigi. Opsional.
- **XAU Asia long-bias**: long-only IS 1.16 / OOS 1.32 / DD cuma 4R (vs 9R) — lebih halus, tapi taruhan gold terus naik.

## ✅ NAS100 edge BARU ditemukan (2026-06-26)
Akar masalah slot lama: open dipatok **fix 13:30 UTC**, padahal cash-open NAS geser ikut DST AS
(13:30 musim panas / **14:30 musim dingin**) → separuh tahun salah window. Perbaikan:

| Setup | IS PF | OOS PF | WR | MaxDD | Walk-fwd |
|---|---|---|---|---|---|
| **DST-open + 30m 1:1 + trend50(SMA harian) + range_filter** | 1.49 | **1.33** | 59% | **5.0R** | **11/11** |
| DST-open + 30m 1:1 + trend50 | 1.38 | 1.35 | 60% | 6.0R | 10/11 |

Lolos bar A++, plateau robust, IS≈OOS. **Butuh 2 param baru di wrapper live** (`pipeline/live/signal.py`,
seperti `range_filter`): `dst_open` (hitung open NY per-DST) + `trend_sma` (gate arah = trend harian).
Tipe slot tetap `orb`; `strategy_orb.py` (backtest) TIDAK disentuh (aturan onboarding #1); EA tidak berubah.

## Catatan NAS100 (penting — bukan bug)
ORB **tidak** entry tiap sesi NY. Dia entry **hanya saat harga menembus opening range** (13:30–14:00). Hari "NO-BREAK" (harga diam di dalam range, mis. 25 Jun yg range-nya 892 poin) = **tidak ada trade, by design**. Dari data: ~73% sesi NY ada breakout (trade), ~27% NO-BREAK (skip). Range_filter juga skip hari range abnormal. Jadi: **tidak ada bug**, tapi **bukan tiap sesi**.
