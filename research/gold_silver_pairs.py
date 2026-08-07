"""EDGE HUNT #6 — GOLD/SILVER PAIRS (mean-reversion stat-arb). Trusted archetype: Ernie Chan
'Algorithmic Trading', QuantConnect community, recent GC-SI/GLD-SLV cointegration research. Method:
ratio = XAU/XAG, z-score vs rolling mean/std; DOLLAR-NEUTRAL — long ratio (long gold/short silver)
when z<-entry, short ratio when z>+entry, exit |z|<exit. Market-neutral => should be UNCORRELATED to
our directional book (the reason to want it). Full rigor: 2-leg cost, walk-forward, boot CI, corr to book.
Pre-registered test = (window 60, entry 2.0, exit 0.5); grid is robustness only.

Run: python research/gold_silver_pairs.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(43)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
g = px["XAUUSD"].dropna(); s = px["XAGUSD"].dropna()
J = pd.concat([g.rename("g"), s.rename("s")], axis=1).dropna()
gret = J["g"].pct_change(); sret = J["s"].pct_change()
leg_cost = COST["XAUUSD"] + COST["XAGUSD"]


def pair_ret(window=60, entry=2.0, exit=0.5):
    ratio = J["g"] / J["s"]
    z = (ratio - ratio.rolling(window).mean()) / ratio.rolling(window).std()
    pos = np.zeros(len(z)); cur = 0.0; zv = z.values
    for i in range(len(z)):
        if np.isnan(zv[i]): pos[i] = cur; continue
        if cur == 0:
            if zv[i] < -entry: cur = 1.0            # long ratio: long gold / short silver
            elif zv[i] > entry: cur = -1.0
        elif cur == 1 and zv[i] >= -exit: cur = 0.0
        elif cur == -1 and zv[i] <= exit: cur = 0.0
        pos[i] = cur
    pos = pd.Series(pos, index=z.index).shift(1).fillna(0)
    raw = pos * (gret - sret)                        # dollar-neutral spread return
    net = raw - pos.diff().abs().fillna(0) * leg_cost
    vt = (net / (net.rolling(50).std() * np.sqrt(252)) * 0.10).replace([np.inf, -np.inf], 0).fillna(0)
    return net.dropna(), vt.dropna()


def boot(r, n=3000, block=15):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    out = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        x = r[idx]; sd = x.std(); out.append(x.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(out, [2.5, 97.5])


def mets(r):
    r = r.dropna(); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    fp = sum(1 for a, b in FOLDS if np.isfinite(x := sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))])) and x > 0)
    return sharpe(r), cagr, dd, fp


net, vt = pair_ret(60, 2.0, 0.5)
sh, cg, dd, fp = mets(vt); lo, hi = boot(vt)
print("GOLD/SILVER PAIRS (dollar-neutral, z-score mean reversion) — pre-registered (60,2.0,0.5)\n")
print(f"  Sharpe {sh:+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  CAGR {cg:+.1%}  maxDD {dd:.1%}  WF+ {fp}/5")
print(f"  per-fold: " + " ".join(f"{sharpe(vt[(vt.index>=pd.Timestamp(a,tz='UTC'))&(vt.index<pd.Timestamp(b,tz='UTC'))]):+.2f}" for a,b in FOLDS))

print("\n  robustness grid (window / entry-z):")
for w in (30, 60, 90):
    for e in (1.5, 2.0, 2.5):
        _, v = pair_ret(w, e, 0.5); ss, cc, _, ff = mets(v)
        print(f"    win={w} entry={e}: Sharpe {ss:+.2f}  CAGR {cc:+.1%}  WF+ {ff}/5")

# correlation to the live book
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}
def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def dsl(nm):
    c = px[nm].dropna(); rt = c.pct_change(); u = c.rolling(100).max().shift(1); dn = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= u] = 1; p[c <= dn] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    sl, ss = SWAP[nm]; sw = pd.Series(np.where(p > 0, sl, np.where(p < 0, ss, 0.0)), index=c.index) / 100 / 252
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST[nm] + p.abs() * sc * sw
def jsl():
    c = px["USDJPY"].dropna(); rt = c.pct_change(); net2 = pd.Series([(RD.get(y,3.23)-2.32)/100/252 for y in c.index.year], index=c.index)
    p = ((c > c.rolling(100).mean()) & (net2 > 0)).astype(float).shift(1).fillna(0); sc = _sc(rt)
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST["USDJPY"] + p * sc * net2.clip(lower=0)
B = pd.concat({"g": dsl("XAUUSD"), "n": dsl("NAS100"), "j": jsl()}, axis=1).dropna()
book = (B["g"] + B["n"] + 0.5 * B["j"]) / 2.5
jj = pd.concat([vt.rename("pair"), book.rename("book")], axis=1).dropna()
print(f"\n  correlation to LIVE book = {jj['pair'].corr(jj['book']):+.2f}  (n={len(jj)})")
print("  KEEP if CI>0 AND WF majority AND corr low (market-neutral should be ~0). Else discard.")
print("DONE")
