"""Z MOMENTUM-BASED SIZING (playbook rule #3) — walk-forward, best-practice. Z whipsaws are NOT
ADX-separable, but ENTRY MOMENTUM (z-score of price vs 20-bar H1 mean, direction-adjusted) is the
validated size signal: strong-momentum breakouts are better trades. Size up on high |z|, min on weak,
capped. Compare flat vs momentum tilts on net/maxDD/Sharpe/PF/WF/per-year. Accept only if risk-
adjusted (Sharpe kept/up, DD not much worse, WF+yr green); reject over-leverage. Sizing is a pure $
multiplier on the SAME trades, so this is faithful (unlike a filter). Run: python research/z_momentum_sizing.py

CAVEAT printed at end: on a SMALL account the capital-aware cap (lot_per_balance) must keep Z at 0.01
until balance is large enough to survive a scaled Z loss (Z's worst -$136 @0.01 -> -$272 @0.02).
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split
from zrev_dual_trend import sim_dual, daily_map

M1 = load_m1("XAUUSD")
h1 = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
cc = h1["close"]
ma20 = cc.rolling(20).mean(); sd20 = cc.rolling(20).std()

rows = []
for e, x, d, p in sim_dual(dmap=daily_map(50), use_daily=True):
    m = ma20.asof(e); s = sd20.asof(e); px = cc.asof(e)
    if not np.isfinite(s) or s <= 0:
        z = 0.0
    else:
        z = (px - m) / s
    z_dir = z if d == "long" else -z          # direction-adjusted momentum
    rows.append((x, p, z_dir))
df = pd.DataFrame(rows, columns=["ts", "pnl", "zdir"]).set_index("ts").sort_index()

print(f"### Z momentum sizing (walk-forward) — n={len(df)} trades ###")
print(f"  entry momentum z_dir: median {df.zdir.median():.2f}  |  frac z_dir>=1: {100*(df.zdir>=1).mean():.0f}%"
      f"  z_dir>=2: {100*(df.zdir>=2).mean():.0f}%\n")

def mult_flat(z): return 1.0
def mult_live(z): return 2.0 if z >= 2.0 else 1.0            # current live (lot_max 0.02, step-floored)
def mult_m2(z): return 2.0 if z >= 1.0 else 1.0
def mult_m3(z): return 3.0 if z >= 2.0 else (2.0 if z >= 1.0 else 1.0)
def mult_m3b(z): return 3.0 if z >= 1.5 else (2.0 if z >= 0.5 else 1.0)

def report(name, fn):
    s = df.pnl * df.zdir.map(fn)
    eq = s.cumsum(); dd = float((eq - eq.cummax()).min())
    mo = s.resample("MS").sum(); sharpe = mo.mean() / mo.std() * np.sqrt(12) if mo.std() > 0 else 0
    pf = s[s > 0].sum() / max(1e-9, -s[s < 0].sum())
    _, oos = split(list(zip(df.index, s.values)))
    w6 = s.resample("6MS").sum(); w6 = w6[w6 != 0]
    py = [s[s.index.year == y].sum() for y in range(2021, 2027)]
    gy = sum(1 for v in py if v > 0)
    worst = df.pnl.min() * fn(df.loc[df.pnl.idxmin(), "zdir"])
    print(f"  {name:14s} net=${s.sum():+6.0f} maxDD=${dd:+6.0f} PF={pf:4.2f} OOSpf={stats(oos)['pf']:4.2f} "
          f"Sharpe={sharpe:4.2f} WF={int((w6>0).sum())}/{len(w6)} yr+={gy}/6 worst1trade=${worst:+.0f}")

report("flat 0.01", mult_flat)
report("live(z>=2:2x)", mult_live)
report("mom z>=1:2x", mult_m2)
report("mom grad 2/3x", mult_m3)
report("mom grad b", mult_m3b)
print("\n  NOTE: net is on full-history NOTIONAL. On the $260 demo the capital-aware cap")
print("  (lot_per_balance) MUST keep Z at 0.01 until ~$1500+ (a scaled Z loss -$272 would blow $260).")
print("DONE")
