"""TASK 2 — a DIFFERENT-MECHANISM edge: volatility SQUEEZE breakout (momentum ignition). When vol is
compressed (ATR20 < 0.9*ATR100) and price breaks the 20-day channel, ride the expansion; exit on cross
back through SMA10. Different horizon/logic from slow Donchian-100 trend and from reversal -> should be
low-correlation and add to the stack. Full rigor: cost, WF, boot CI, + correlation to the daily book.

Run: python research/vol_breakout.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(59)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}
ASSETS = ["XAUUSD", "NAS100", "SP500", "DOW", "DAX", "NIKKEI", "WTI"]


def vbreak(name, sq=0.9, chan=20, exitsma=10):
    c = px[name].dropna(); ret = c.pct_change()
    v20 = ret.rolling(20).std(); v100 = ret.rolling(100).std()
    squeeze = (v20 < sq * v100).values
    up = c.rolling(chan).max().shift(1).values; dn = c.rolling(chan).min().shift(1).values
    mid = c.rolling(exitsma).mean().values; cv = c.values
    pos = np.zeros(len(c)); cur = 0.0
    for i in range(1, len(c)):
        if cur == 0:
            if squeeze[i] and cv[i] >= up[i]: cur = 1.0
            elif squeeze[i] and cv[i] <= dn[i]: cur = -1.0
        elif cur == 1 and cv[i] < mid[i]: cur = 0.0
        elif cur == -1 and cv[i] > mid[i]: cur = 0.0
        pos[i] = cur
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    vol = ret.rolling(50).std() * np.sqrt(252); scale = (0.10 / vol).clip(upper=3).fillna(0)
    r = pos * scale * ret - pos.diff().abs().fillna(0) * scale * COST[name]
    return r.dropna()


def boot(r, n=3000, block=15):
    r = r.dropna().values; N = len(r); nb = max(1, N // block); out = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); out.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(out, [2.5, 97.5])
def wf(r): return [sharpe(r[(r.index>=pd.Timestamp(a,tz='UTC'))&(r.index<pd.Timestamp(b,tz='UTC'))]) for a,b in FOLDS]

print("VOLATILITY SQUEEZE BREAKOUT (compressed vol -> ride 20d channel break)\n")
print(f"{'asset':8} {'Sharpe':>7} {'95% CI':>16} {'WF+':>5}")
streams = {}
for n in ASSETS:
    if n not in px.columns: continue
    r = vbreak(n); streams[n] = r
    lo, hi = boot(r); folds = wf(r); fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
    print(f"{n:8} {sharpe(r):>7.2f}  [{lo:+.2f},{hi:+.2f}]  {fp:>3}/5{'  <-CI>0' if lo>0 else ''}")

basket = pd.concat(streams, axis=1).mean(axis=1).dropna()
lo, hi = boot(basket); folds = wf(basket); fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
print(f"\nSQUEEZE BASKET: Sharpe {sharpe(basket):+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  WF {fp}/5")

# correlation to the daily book sleeves
def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def dsl(nm):
    c = px[nm].dropna(); rt = c.pct_change(); u = c.rolling(100).max().shift(1); d = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= u] = 1; p[c <= d] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    sl, ss = SWAP[nm]; sw = pd.Series(np.where(p > 0, sl, np.where(p < 0, ss, 0.0)), index=c.index) / 100 / 252
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST[nm] + p.abs() * sc * sw
def jsl():
    c = px["USDJPY"].dropna(); rt = c.pct_change(); net = pd.Series([(RD.get(y,3.23)-2.32)/100/252 for y in c.index.year], index=c.index)
    p = ((c > c.rolling(100).mean()) & (net > 0)).astype(float).shift(1).fillna(0); sc = _sc(rt)
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST["USDJPY"] + p * sc * net.clip(lower=0)
trend = (dsl("XAUUSD")+dsl("NAS100"))/2
J = pd.concat([basket.rename("sqz"), trend.rename("trend"), jsl().rename("carry")], axis=1).dropna()
print(f"\ncorrelation squeeze vs trend = {J['sqz'].corr(J['trend']):+.2f}   vs carry = {J['sqz'].corr(J['carry']):+.2f}")
print("KEEP if basket CI>0 AND low corr to trend (different mechanism). Else discard.")
print("DONE")
