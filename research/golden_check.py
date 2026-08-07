"""Golden standalone: does my reimplementation reproduce the claimed PF 2.14 / 6/6 green / maxDD -35%?
If not, Golden is UNVERIFIED in our hands and must not ship to a real $1000 account until reconciled.
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from book_1000_full import golden_trades, z_trades_full, dir_series

xau = load_m1("XAUUSD")
gtr = golden_trades(xau)                       # no guard, standalone
p = np.array([x[3] for x in gtr]); ts = pd.DatetimeIndex([x[1] for x in gtr])
g = p[p > 0].sum(); b = -p[p < 0].sum()
print("=" * 78)
print("GOLDEN STANDALONE — my reimplementation vs the claim in config.yaml/signal.py")
print("=" * 78)
print(f"trades {len(p)} | net ${p.sum():+.0f} | PF {g/b if b else np.inf:.2f} | "
      f"WR {100*(p > 0).mean():.1f}% | avg ${p.mean():+.3f}/trade")
print(f"claim  : PF 2.14 (OOS 1.99), 6/6 green years, maxDD -35%, 11/11 walk-forward\n")
print(f"{'Year':6}{'trades':>8}{'net$':>9}{'PF':>7}{'WR%':>7}{'DD$':>8}  verdict")
eq_all = pd.Series(p, index=ts).sort_index()
for y in range(2021, 2027):
    m = ts.year == y
    if m.sum() == 0: continue
    q = p[m]; gg = q[q > 0].sum(); bb = -q[q < 0].sum()
    e = pd.Series(q).cumsum(); dd = float((e - e.cummax()).min())
    print(f"{y:<6}{m.sum():>8}{q.sum():>+9.0f}{(gg/bb if bb else np.inf):>7.2f}"
          f"{100*(q > 0).mean():>7.1f}{dd:>8.0f}  {'GREEN' if q.sum() > 0 else 'RED'}")
e = eq_all.cumsum(); print(f"{'ALL':<6}{len(p):>8}{p.sum():>+9.0f}{(g/b if b else np.inf):>7.2f}"
                          f"{100*(p > 0).mean():>7.1f}{float((e-e.cummax()).min()):>8.0f}")

# how often is Golden even in the market at the same time as Z?
m5idx = xau.resample("5min").agg({"close": "last"}).dropna().index
zdir = dir_series(z_trades_full(xau), m5idx)
gdir = dir_series(gtr, m5idx)
both = ((zdir != 0) & (gdir != 0))
opp = both & (zdir != gdir)
print(f"\nOverlap with Z: both in market {100*both.mean():.1f}% of M5 bars | "
      f"OPPOSITE sides {100*opp.mean():.2f}% ({int(opp.sum())} bars)")
print("DONE")
