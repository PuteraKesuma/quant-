"""Long-term DD best practice: the biggest robust lever is SIZING (bet fractionally to
survive the multi-year worst case), not more strategy tweaks. Multi-year Monte-Carlo of
the live book (gold zrev dual-filter + US100 orb) showing, per lot size: median growth,
the worst-case max-DD-EVER (95th pct over ~5 yr), and risk of ruin.

Key point: over many years you WILL hit a drawdown worse than any single year -> size so
that worst case is survivable. $ @0.01 base lot. Run: python research/longterm_dd.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from zrev_dual_trend import sim_dual, daily_map
from portfolio_audit import nas_dollars

CAP = 1500.0
dmap = daily_map(50)
z = sim_dual(dmap=dmap, use_daily=True)
zser = pd.Series([t[3] for t in z], index=pd.DatetimeIndex([t[1] for t in z]))
nas = nas_dollars()
book = pd.concat([zser, nas]).sort_index().values     # per-trade $ @0.01 lot
per_year = len(book) / 5.5
print(f"book: {len(book)} trades over 5.5y (~{per_year:.0f}/yr), avg ${book.mean():+.2f}/trade @0.01\n")
print(f"LONG-TERM Monte-Carlo (start ${CAP:.0f}, ~5 years, 4000 paths):")
print(f"  {'lot':>5} {'med final':>10} {'med maxDD':>10} {'95th maxDD':>11} {'P(DD>40%)':>10} {'P(ruin<50%)':>12}")
rng = np.random.default_rng(11)
N5 = int(per_year * 5)
for lot in (0.01, 0.015, 0.02, 0.03):
    scale = lot / 0.01
    finals = []; dds = []; ruin = 0; dd40 = 0
    for _ in range(4000):
        s = rng.choice(book, size=N5, replace=True) * scale
        eq = CAP + np.cumsum(s)
        path = np.concatenate([[CAP], eq])
        ddpct = (path / np.maximum.accumulate(path) - 1).min()
        finals.append(eq[-1]); dds.append(ddpct)
        if path.min() <= 0.5 * CAP: ruin += 1
        if ddpct <= -0.40: dd40 += 1
    finals = np.array(finals); dds = np.array(dds)
    print(f"  {lot:>5.3f} ${np.median(finals):>9.0f} {100*np.median(dds):>9.0f}% "
          f"{100*np.percentile(dds,5):>10.0f}% {100*dd40/4000:>9.0f}% {100*ruin/4000:>11.0f}%")
print("\n  'med maxDD' = drawdown terburuk khas; '95th maxDD' = skenario buruk 5 tahun.")
print("  Best practice jangka panjang: pilih lot di mana 95th-maxDD & P(ruin) masih kamu tahan.")
