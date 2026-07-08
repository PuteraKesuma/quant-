"""ORB -> PLAYBOOK. ORB is a BREAKOUT (momentum) strategy, so its regime hypothesis is the OPPOSITE
of Golden's fade: breakouts should do BETTER on trending/volatile days and WORSE in chop. Tag each
ORB trade with the PRIOR-day NAS100 ADX(14) (a regime known before the trade) and daily ATR, then:
  1) expectancy by ADX bin (does chop kill ORB?),
  2) test skip-chop and size-up-trend rules, walk-forward + per-year, best-practice (keep the edge,
     don't overfit an already-thin strategy: exp ~0.08R, so be strict).
Run: python research/orb_regime.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split
from portfolio_audit import nas_dollars

NAS = nas_dollars().sort_index()          # $ per ORB trade, indexed by entry ts
M1 = load_m1("NAS100")
d1 = M1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])

def adx(h, n=14):
    up = h["high"].diff(); dn = -h["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()
dadx = adx(d1).shift(1)                    # prior completed day's ADX (known before the NY session)

df = pd.DataFrame({"pnl": NAS.values}, index=NAS.index)
df["adx"] = dadx.reindex(df.index, method="ffill").values
df = df.dropna()
print(f"### ORB regime (prior-day NAS ADX14) — n={len(df)} trades ###")
print(f"  daily ADX: median {df.adx.median():.1f}  |  chop<20: {100*(df.adx<20).mean():.0f}%  trend>25: {100*(df.adx>25).mean():.0f}%\n")
print("--- expectancy by prior-day ADX bin ---")
for lo, hi, lbl in ((0, 18, "chop <18"), (18, 22, "18-22"), (22, 28, "22-28"), (28, 100, "trend >28")):
    g = df[(df.adx >= lo) & (df.adx < hi)]
    if len(g) < 10:
        continue
    pf = g[g.pnl > 0].pnl.sum() / max(1e-9, -g[g.pnl < 0].pnl.sum())
    print(f"  {lbl:10} n={len(g):4d}  avg=${g.pnl.mean():+5.2f}  net=${g.pnl.sum():+6.0f}  PF={pf:4.2f}  WR={100*(g.pnl>0).mean():3.0f}%")

def wf(s):
    w = s.resample("6MS").sum(); w = w[w != 0]; return int((w > 0).sum()), len(w)
def yr(s):
    return sum(1 for y in range(2021, 2027) if s[s.index.year == y].sum() > 0)
def rep(name, s):
    eq = s.cumsum(); dd = float((eq - eq.cummax()).min())
    mo = s.resample("MS").sum(); sh = mo.mean() / mo.std() * np.sqrt(12) if mo.std() > 0 else 0
    pf = s[s > 0].sum() / max(1e-9, -s[s < 0].sum())
    _, o = split(list(zip(s.index, s.values)))
    wp, wn = wf(s)
    print(f"  {name:20s} n={int((s!=0).sum()):4d} net=${s.sum():+6.0f} maxDD=${dd:+5.0f} PF={pf:4.2f} "
          f"OOSpf={stats(o)['pf']:4.2f} Sharpe={sh:4.2f} WF={wp}/{wn} yr+={yr(s)}/6")

print("\n--- rules (best-practice: keep edge, don't overfit a thin strategy) ---")
rep("flat (all days)", df.pnl)
for thr in (16, 18, 20):
    rep(f"skip ADX<{thr}", df.pnl.where(df.adx >= thr, 0.0))
for thr in (25, 28):
    rep(f"size2x ADX>{thr}", df.pnl * np.where(df.adx > thr, 2.0, 1.0))
rep("skip<18 + size2x>28", df.pnl.where(df.adx >= 18, 0.0) * np.where(df.adx > 28, 2.0, 1.0))
print("\n  accept only if net/PF/Sharpe UP AND WF+yr not worse. ORB edge is THIN -> reject marginal.")
print("DONE")
