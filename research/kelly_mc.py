"""Phase 1 sizing foundation: Kelly fraction + Monte-Carlo for the live book
(zrev dual-TF + orb30_nas) at $1500. Answers: what lot is growth-optimal, what's the
realistic drawdown, and the risk of ruin -- BEFORE any ML.

PnL per trade in $ at 0.01 lot (XAU $1/pt, NAS $0.10/pt). Bootstrap the trade order.
Run: python research/kelly_mc.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from zrev_dual_trend import sim_dual, daily_map
from portfolio_audit import nas_dollars

CAP = 1500.0

# book per-trade $ at 0.01 lot
dmap = daily_map(50)
z = sim_dual(dmap=dmap, use_daily=True)
zser = pd.Series([t[3] for t in z], index=pd.DatetimeIndex([t[1] for t in z]))
nser = nas_dollars()
book = pd.concat([zser, nser]).sort_index()
pnl = book.values
n_per_year = len(pnl) / ((book.index.max() - book.index.min()).days / 365.25)
print(f"book: {len(pnl)} trades, ~{n_per_year:.0f}/year, net=${pnl.sum():+.0f}@0.01lot, "
      f"avg=${pnl.mean():+.2f}, std=${pnl.std():.2f}")

# ---- Kelly (leverage multiplier on the 0.01-lot bet, returns vs $1500) ----
r = pnl / CAP
f_star = r.mean() / r.var()              # continuous Kelly leverage
print(f"\nKELLY (returns vs ${CAP:.0f}): mean={r.mean():.5f} var={r.var():.2e} "
      f"-> full-Kelly leverage f*={f_star:.1f}x  (lot {0.01*f_star:.3f})")
print(f"  full-Kelly is too wild; practitioners use 1/4-1/2 Kelly.")
for frac, lab in [(1.0, "full"), (0.5, "half"), (0.25, "quarter")]:
    lot = 0.01 * f_star * frac
    print(f"  {lab:7s}-Kelly -> lot ~{lot:.3f}")

# ---- Monte Carlo: bootstrap, simulate 1 year, per lot ----
print(f"\nMONTE-CARLO (start ${CAP:.0f}, 1 year ~{int(n_per_year)} trades, 3000 paths):")
print(f"  {'lot':>5} {'medFinal':>9} {'5pct':>8} {'medMaxDD%':>9} {'P(DD>40%)':>9} {'P(ruin<50%)':>11}")
rng = np.random.default_rng(7)
N = int(n_per_year)
for lot in (0.01, 0.02, 0.03, 0.05, 0.08):
    finals = []; dds = []; ruin = 0; dd40 = 0
    scale = lot / 0.01
    for _ in range(3000):
        s = rng.choice(pnl, size=N, replace=True) * scale
        eq = CAP + np.cumsum(s)
        peak = np.maximum.accumulate(np.concatenate([[CAP], eq]))
        ddpct = (np.concatenate([[CAP], eq]) / peak - 1).min()
        finals.append(eq[-1]); dds.append(ddpct)
        if eq.min() <= 0.5 * CAP: ruin += 1
        if ddpct <= -0.40: dd40 += 1
    finals = np.array(finals); dds = np.array(dds)
    print(f"  {lot:>5.2f} ${np.median(finals):>8.0f} ${np.percentile(finals,5):>7.0f} "
          f"{100*np.median(dds):>8.0f}% {100*dd40/3000:>8.0f}% {100*ruin/3000:>10.0f}%")
print("\n  Rekomendasi: pilih lot dgn P(ruin)~0 & DD median yang kamu nyaman (mis. <30%).")
