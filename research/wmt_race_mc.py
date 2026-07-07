"""WMT FUNDED-ACCOUNT RACE MC — the sizing question answered honestly.

Account: $10k WeMasterTrade funded, equity now $9,600. Target = $11,000 (10% of initial).
Max total drawdown = 10% static => breach (absorbing barrier) at $9,000. Remaining buffer $600.

For each lot size (multiples of the validated 0.01-lot $ series) and two books:
  GOLD-ONLY  = Z + LIQ          (if WMT has no Nasdaq index)
  FULL       = Z + NAS + LIQ    (if USTEC/NAS100 exists there)
simulate 4,000 paths of weekly PnL (4-week block bootstrap, regime-clustering honest),
max 156 weeks (3 years). Report: P(reach $11,000 BEFORE $9,000), P(breach first),
median weeks to target among the winners.

NOTE: uses FBS-validated $ series; WMT spreads/commission must still be verified before
live (LIQ's $13 stop dies at $5/trade cost — measured, not assumed).
Run: python research/wmt_race_mc.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")

START, TARGET, BARRIER = 9600.0, 11000.0, 9000.0
N_PATHS, MAX_W, BLOCK = 4000, 156, 4

print("=== building component series (same sims as portfolio_best) ===", flush=True)
import portfolio_best as pb   # noqa: E402
BOOKS = {
    "GOLD-ONLY (Z+LIQ)": pd.concat([pb.Z, pb.LIQ]).sort_index(),
    "FULL (Z+NAS+LIQ)":  pd.concat([pb.Z, pb.NAS, pb.LIQ]).sort_index(),
}

rng = np.random.default_rng(21)
for name, book in BOOKS.items():
    wk = book.resample("W").sum().values
    nblk = int(np.ceil(MAX_W / BLOCK))
    print(f"\n=== {name}: weekly mean=${wk.mean():+.1f} sd=${wk.std():.1f} ===")
    print(f"  {'lot':>5} {'P(target dulu)':>15} {'P(breach dulu)':>15} {'P(>3thn)':>9} {'median mgg->target':>19}")
    for mult, lot in ((1, "0.01"), (2, "0.02"), (3, "0.03"), (5, "0.05")):
        starts = rng.integers(0, len(wk), size=(N_PATHS, nblk))
        idx = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]) % len(wk)
        draws = wk[idx].reshape(N_PATHS, -1)[:, :MAX_W] * mult
        eq = START + np.cumsum(draws, axis=1)
        hit_t = eq >= TARGET
        hit_b = eq <= BARRIER
        wt = np.where(hit_t.any(axis=1), hit_t.argmax(axis=1), MAX_W + 1)
        wb = np.where(hit_b.any(axis=1), hit_b.argmax(axis=1), MAX_W + 1)
        win = (wt < wb)
        lose = (wb < wt)
        neither = ~win & ~lose
        med_w = float(np.median(wt[win])) + 1 if win.any() else float("nan")
        print(f"  {lot:>5} {100*win.mean():>14.1f}% {100*lose.mean():>14.1f}% {100*neither.mean():>8.1f}% "
              f"{med_w:>15.0f} (~{med_w/4.3:.0f} bulan)")
print("\nDONE")
