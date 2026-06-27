# Research & Live-Integration Log — June 2026

Ringkasan kerja yang dilakukan di VPS (riset + integrasi strategi live). Semua angka
adalah hasil backtest/validasi; tidak memuat info akun/kredensial.

## Data riset
- Dukascopy 1m, **2021–2026**, untuk XAUUSD, NAS100, EURUSD, GBPUSD (di `data/Level_0_Raw/`, gitignored).
- Split validasi: In-Sample 2021–2024, Out-of-Sample 2025–2026. Plus walk-forward 6-bulanan.
- Broker MT5 dipakai untuk cek realisme (broker ≈ Dukascopy → spread tidak merusak edge).

## Strategi yang ditambahkan ke execution layer (`pipeline/live/`)
Semua sebagai class baru / param opsional di `pipeline/live/signal.py` + slot di `config.yaml`.
**EA MQL5 tidak pernah diubah** (tetap strategy-agnostic). `pipeline/backtest/strategy_orb.py` tidak disentuh (rule onboarding #1).

| Slot | Tipe | Inti | Validasi (OOS) |
|---|---|---|---|
| `zrev_xau` | `zrev` (baru) | Donchian stop-and-reverse XAU 1H, entry_n=100/exit_n=20, always-in, exit-by-signal | PF ~1.43, 6/7 thn hijau |
| `mr_xau` | `mr` (baru) | Mean-reversion z-score fade XAU 1H, N=20/entry=2.5σ/stop=3.0σ, TP=mean | PF ~3, 11/11 walk-forward, M1-fill konfirmed |
| `orb30_nas` | `orb` (+param baru) | NAS NY ORB 30m 1:1 + `dst_open` + `trend_sma:50` + `breakeven_r:0.5` | PF 1.52, WF 11/11 |
| `vision_smc_xau` | `vision` | Prompt v2 (`prompt_smc_mtf_v2.md`): H4 macro-bias guard + sweep→displacement→OB | eksperimental (LLM, tak di-backtest) |

Dinonaktifkan saat lean-down: `orb30_xau` (gold NY ORB) — berkorelasi dengan Z (gold dobel) sehingga menambah drawdown.

## Temuan riset utama
1. **ORB polos = edge tipis** (semua simbol OOS PF ~1.2–1.3). Bukan "A++".
2. **NAS100 ORB**: open dipatok fix 13:30 UTC salah window separuh tahun karena **DST AS** → `dst_open` + filter trend SMA50 mengangkat OOS PF 1.02 → 1.35.
3. **Dynamic exit (ORB)**: trailing/feel MERUGIKAN; hanya **breakeven@+0.5R** untuk NAS yang terbukti (OOS 1.33 → 1.52). Bukti pentingnya backtest sebelum deploy.
4. **Z (Donchian) spesifik gold** — tidak transfer ke NAS/FX (ditolak setelah uji OOS). Whipsaw Z bersifat struktural; filter trend/ADX tidak menolong tanpa membunuh profit.
5. **Mean-reversion gold = diversifier sejati**: panen di chop (saat Z whipsaw). Portofolio Z+MR menaikkan return dan **menurunkan drawdown** sekaligus.

## Risk note (untuk akun real)
Pada lot minimum 0.01, set strategi punya drawdown historis nyata. Untuk live nyaman (DD ≤ ~15%), modal awal sebaiknya jauh di atas modal hipotesis kecil. Sizing = lever risiko utama; whipsaw Z struktural (terima atau perbesar modal/diversifikasi).

## Infrastruktur "always-up" (VPS)
- `_MONITOR/watchdog_brain.ps1`: cek `/health` tiap 30 dtk, auto-restart brain (dan relaunch MT5) kalau down, log ke jurnal.
- `_MONITOR/strat_monitor.ps1`: catat keputusan vision + sinyal NAS ke log harian.
- Auto-start (Startup shortcuts) + auto-logon agar tahan reboot. (Detail runtime & jurnal berisi info akun → TIDAK masuk repo.)

## Tes
`tests/live/`: `test_orb_filters.py` (dst_open/trend), `test_orb_breakeven.py`, `test_mr.py`. Seluruh suite hijau (`pytest tests/ -q`).
