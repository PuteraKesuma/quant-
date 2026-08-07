"""EDGE HUNT #5 — extend the VALIDATED RSI-2 reversal across the whole 21-asset universe.
More independent survivors = higher combined Sharpe = more levered return (the honest path to high
return). Same rule: LONG when RSI(2)<10 AND close>SMA200, exit when close>SMA5. Full rigor per asset
(cost, WF, boot CI), then build a basket of SURVIVORS and show their cross-correlation + combined MOE.

Run: python research/reversal_universe.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(37)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")


def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def rev(name, entry=10, sma_t=200, sma_x=5):
    c = px[name].dropna(); ret = c.pct_change()
    r2 = rsi(c, 2).values; up = (c > c.rolling(sma_t).mean()).values; ex = (c > c.rolling(sma_x).mean()).values
    pos = np.zeros(len(c)); inpos = False
    for i in range(1, len(c)):
        if not inpos and (r2[i] < entry) and up[i]: inpos = True
        elif inpos and ex[i]: inpos = False
        pos[i] = 1.0 if inpos else 0.0
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    return (pos * ret - pos.diff().abs().fillna(0) * COST[name]).dropna(), pos.mean()


def boot(r, n=2500, block=10):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    out = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); out.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(out, [2.5, 97.5])


print("RSI-2 REVERSAL across the universe (long dips in uptrends)\n")
print(f"{'asset':9} {'Sharpe':>7} {'95% CI':>16} {'CAGR':>7} {'expo':>5} {'WF+':>5}")
survivors = {}
for name in px.columns:
    c = px[name].dropna()
    if len(c) < 500: continue
    r, expo = rev(name)
    if len(r) < 300: continue
    lo, hi = boot(r); folds = [sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))]) for a, b in FOLDS]
    fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
    eq = (1 + r).cumprod(); cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    surv = (lo > 0) and (fp >= 3)
    if surv: survivors[name] = r
    print(f"{name:9} {sharpe(r):>7.2f}  [{lo:+.2f},{hi:+.2f}]   {cagr:>+6.1%} {expo:>5.0%} {fp:>3}/5{'  <== SURVIVES' if surv else ''}")

if len(survivors) >= 2:
    print(f"\nsurvivors: {list(survivors.keys())}")
    M = pd.concat(survivors, axis=1).dropna()
    print("cross-correlation among survivors:")
    print(M.corr().round(2).to_string())
    basket = M.mean(axis=1)
    lo, hi = boot(basket); folds = [sharpe(basket[(basket.index >= pd.Timestamp(a, tz='UTC')) & (basket.index < pd.Timestamp(b, tz='UTC'))]) for a, b in FOLDS]
    fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
    eq = (1 + basket).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(basket)) - 1
    print(f"\nSURVIVOR BASKET (equal-wt): Sharpe {sharpe(basket):+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  "
          f"CAGR {cagr:+.1%}  maxDD {dd:.1%}  WF+ {fp}/5")
    avg_corr = (M.corr().values[np.triu_indices(len(M.columns), 1)]).mean()
    print(f"avg pairwise corr among survivors = {avg_corr:+.2f}  (lower = more diversification benefit)")
print("DONE")
