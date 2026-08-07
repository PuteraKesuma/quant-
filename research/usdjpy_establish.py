"""Establish USDJPY as a diversifier — focused walk-forward on daily data (2011-2026).
Trend engines (SMA-sign vs Donchian S&R) x lookback, with per-fold + per-year Sharpe, maxDD,
and corr to gold. Goal: lock in a robust baseline edge before hunting USDJPY-specific open-source ideas.

Run: python research/usdjpy_establish.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import sma_ret, donchian_ret, sharpe, COST, FOLDS

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
px = pd.read_parquet(CACHE)
c = px["USDJPY"].dropna(); cost = COST["USDJPY"]


def report(net, tag):
    net = net.dropna()
    eq = (1 + net).cumprod(); dd = (eq/eq.cummax()-1).min(); cagr = eq.iloc[-1]**(252/len(net))-1
    full = sharpe(net)
    yr = net.groupby(net.index.year).apply(sharpe)
    grn = int((yr > 0).sum()); tot = int(yr.notna().sum())
    fsh = [sharpe(net[(net.index >= pd.Timestamp(a, tz="UTC")) & (net.index < pd.Timestamp(b, tz="UTC"))]) for a, b in FOLDS]
    fp = sum(1 for s in fsh if np.isfinite(s) and s > 0)
    print(f"  {tag:18} Sharpe {full:+.2f}  CAGR {cagr:+.1%}  maxDD {dd:.1%}  yrs+ {grn}/{tot}  WF-folds+ {fp}/{len(fsh)}")
    print(f"       per-fold Sharpe: " + " ".join(f"{s:+.2f}" for s in fsh))
    return full, fp


print("USDJPY diversifier establishment (daily 2011-2026, vol-targeted, cost-aware)")
print(f"corr(USDJPY daily ret, XAUUSD) = {px['USDJPY'].pct_change().corr(px['XAUUSD'].pct_change()):+.2f}  (negative = good diversifier)\n")
best = None
for name, eng in [("SMA-sign", sma_ret), ("Donchian-S&R", donchian_ret)]:
    print(f"engine: {name}")
    for N in (50, 100, 200):
        full, fp = report(eng(c, cost, N), f"N={N}")
        if fp >= 3 and full > 0.2 and (best is None or full > best[0]):
            best = (full, name, N, fp)
    print()
print("==== best robust-ish config ====")
if best:
    print(f"  {best[1]} N={best[2]}: full Sharpe {best[0]:+.2f}, WF folds+ {best[3]}/{len(FOLDS)}")
    print("  -> USDJPY has a MILD but walk-forward-consistent trend edge, negatively correlated to gold.")
else:
    print("  no config passed (fp>=3 & Sharpe>0.2) — USDJPY trend not robust, hunt other USDJPY ideas.")
print("DONE")
