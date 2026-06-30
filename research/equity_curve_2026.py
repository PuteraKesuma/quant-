"""Equity curve from $400, Jan 2026 -> now (data end), 0.01 lot: XAU only vs XAU+US100.
Shows the diversification value of adding the (uncorrelated) US100 ORB to the gold book.
Deployed configs: zrev dual-filter (XAU) + orb30_nas (US100). $ @0.01 lot.
"""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from cross_asset_edges import dual_sr
from portfolio_audit import nas_dollars

START = pd.Timestamp("2026-01-01", tz="UTC")
CAP = 400.0
OUT = r"C:\Users\ADMINI~1\AppData\Local\Temp\1\claude\C--Users-Administrator\91e0ccf1-c993-48f2-8268-f1678ad108cb\scratchpad\equity_2026.png"

xau = dual_sr(load_m1("XAUUSD"), 0.30, N=20, ema_n=100, dsma=50)   # $ @0.01 lot ($1/pt)
nas = nas_dollars()                                                 # $ @0.01 lot
xau = xau[xau.index >= START]
nas = nas[nas.index >= START]
END = max(xau.index.max(), nas.index.max())
print(f"periode: {START.date()} -> {END.date()}  (modal ${CAP:.0f}, 0.01 lot)")


def curve(series_list):
    s = pd.concat(series_list).sort_index()
    eq = CAP + s.cumsum()
    daily = eq.resample("1D").last().ffill()
    daily.iloc[0] = daily.iloc[0]  # keep
    daily = pd.concat([pd.Series([CAP], index=[START]), daily]).sort_index()
    return daily


def stats(daily, s):
    final = float(daily.iloc[-1]); ret = (final / CAP - 1) * 100
    dd = float((daily / daily.cummax() - 1).min()) * 100
    return final, ret, dd, len(s)


eq_xau = curve([xau]); eq_both = curve([xau, nas])
fx, rx, ddx, nx = stats(eq_xau, xau)
fb, rb, ddb, nb = stats(eq_both, pd.concat([xau, nas]))
print(f"\nXAU saja   : ${CAP:.0f} -> ${fx:.0f}  ({rx:+.0f}%)  maxDD {ddx:.0f}%  trades {nx}")
print(f"XAU+US100  : ${CAP:.0f} -> ${fb:.0f}  ({rb:+.0f}%)  maxDD {ddb:.0f}%  trades {nb}  (nas {nb-nx})")
print(f"  -> US100 menambah ${fb-fx:+.0f} return; DD {ddb-ddx:+.0f}pp")

plt.figure(figsize=(11, 5.5))
plt.plot(eq_xau.index, eq_xau.values, label=f"XAU saja  (${fx:.0f}, {rx:+.0f}%, DD {ddx:.0f}%)", lw=1.8, color="#d4a017")
plt.plot(eq_both.index, eq_both.values, label=f"XAU + US100  (${fb:.0f}, {rb:+.0f}%, DD {ddb:.0f}%)", lw=1.8, color="#1f77b4")
plt.axhline(CAP, color="gray", ls="--", lw=0.7)
plt.title(f"Equity curve dari ${CAP:.0f}  ({START.date()} -> {END.date()}, 0.01 lot)")
plt.ylabel("Equity ($)"); plt.legend(loc="upper left"); plt.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(OUT, dpi=110)
print(f"\nsaved: {OUT}")
