"""Equity curve from $400, Jan 2026 -> now, using the DEPLOYED dynamic lot (z-score
momentum sizing 0.01-0.03) for zrev XAU -- vs fixed 0.01 -- and combined with US100.
US100 (orb) stays fixed 0.01 (no sizing signal). $ at 0.01-base lot.
"""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from vol_target import trades_with_risk          # returns (exit_ts, pnl@0.01, risk, z_dir)
from portfolio_audit import nas_dollars

START = pd.Timestamp("2026-01-01", tz="UTC"); CAP = 400.0
OUT = r"C:\Users\ADMINI~1\AppData\Local\Temp\1\claude\C--Users-Administrator\91e0ccf1-c993-48f2-8268-f1678ad108cb\scratchpad\equity_dyn.png"


def dmult(zd, lo=1.0, hi=3.0, lmin=0.01, lmax=0.03):
    """Live dynamic-lot mapping -> lot multiplier (1/2/3) from direction-adjusted z."""
    frac = max(0.0, min(1.0, (zd - lo) / (hi - lo)))
    raw = lmin + frac * (lmax - lmin)
    lot = max(lmin, min(lmax, int(round(raw / 0.01 - 1e-9)) * 0.01))
    return round(lot, 2) / 0.01


tr = [t for t in trades_with_risk() if t[0] >= START]
ts = pd.DatetimeIndex([t[0] for t in tr])
pnl = np.array([t[1] for t in tr]); z = np.array([t[3] for t in tr])
mult = np.array([dmult(zd) for zd in z])
xau_fix = pd.Series(pnl, index=ts)
xau_dyn = pd.Series(pnl * mult, index=ts)
nas = nas_dollars(); nas = nas[nas.index >= START]
END = max(ts.max(), nas.index.max())
print(f"periode {START.date()} -> {END.date()}, modal ${CAP:.0f}")
print(f"sebaran lot dinamis: 0.01={int((mult==1).sum())}  0.02={int((mult==2).sum())}  0.03={int((mult==3).sum())}")


def curve(*series):
    s = pd.concat(series).sort_index()
    eq = CAP + s.cumsum()
    d = eq.resample("1D").last().ffill()
    return pd.concat([pd.Series([CAP], index=[START]), d]).sort_index()


def stat(c):
    f = float(c.iloc[-1]); return f, (f / CAP - 1) * 100, float((c / c.cummax() - 1).min()) * 100


e_fix = curve(xau_fix); e_dyn = curve(xau_dyn); e_dyn_nas = curve(xau_dyn, nas)
for nm, c in [("XAU fixed 0.01", e_fix), ("XAU DINAMIS", e_dyn), ("XAU DINAMIS + US100", e_dyn_nas)]:
    f, r, dd = stat(c); print(f"  {nm:22s} -> ${f:6.0f}  ({r:+.0f}%)  maxDD {dd:.0f}%")

plt.figure(figsize=(11, 5.5))
for c, lab, col in [(e_fix, "XAU fixed 0.01", "#bbbbbb"),
                    (e_dyn, "XAU DINAMIS (momentum)", "#d4a017"),
                    (e_dyn_nas, "XAU DINAMIS + US100", "#1f77b4")]:
    f, r, dd = stat(c)
    plt.plot(c.index, c.values, label=f"{lab}  (${f:.0f}, {r:+.0f}%, DD {dd:.0f}%)",
             lw=(1.3 if "fixed" in lab else 1.9), color=col, ls=("--" if "fixed" in lab else "-"))
plt.axhline(CAP, color="gray", ls=":", lw=0.7)
plt.title(f"Equity ${CAP:.0f}  ({START.date()} -> {END.date()})  -- dynamic lot vs fixed")
plt.ylabel("Equity ($)"); plt.legend(loc="upper left"); plt.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(OUT, dpi=110)
print("saved:", OUT)
