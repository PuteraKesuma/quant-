"""VALIDATE the equity short-term reversal edge before trusting it: (1) parameter robustness grid
(is it a plateau or a knife-edge?), (2) does adding it ACTUALLY improve the live book (Sharpe/DD)?
Focus NAS100+SP500 (the two that survived). Full real-cost book for the additive test.

Run: python research/reversal_validate.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(31)
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
        if not inpos and r2[i] < entry and up[i]: inpos = True
        elif inpos and ex[i]: inpos = False
        pos[i] = 1.0 if inpos else 0.0
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    return (pos * ret - pos.diff().abs().fillna(0) * COST[name]).dropna()


def basket(entry=10, sma_t=200, sma_x=5):
    return pd.concat({n: rev(n, entry, sma_t, sma_x) for n in ("NAS100", "SP500")}, axis=1).mean(axis=1).dropna()


def mets(r):
    r = r.dropna(); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    fp = sum(1 for a, b in FOLDS if np.isfinite(s := sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))])) and s > 0)
    return sharpe(r), cagr, dd, fp


print("(1) PARAMETER ROBUSTNESS — NAS+SP reversal basket (plateau check)\n")
print(f"  {'entry':>5} {'trendSMA':>8} {'exitSMA':>7} {'Sharpe':>7} {'CAGR':>7} {'maxDD':>7} {'WF+':>5}")
for entry in (5, 10, 15):
    for sma_t in (150, 200):
        for sma_x in (3, 5):
            s, cg, dd, fp = mets(basket(entry, sma_t, sma_x))
            print(f"  {entry:>5} {sma_t:>8} {sma_x:>7} {s:>7.2f} {cg:>+6.1%} {dd:>7.1%} {fp:>3}/5")

# (2) additive test to the live book (full real cost)
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}
def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def dsl(nm):
    c = px[nm].dropna(); rt = c.pct_change(); u = c.rolling(100).max().shift(1); dn = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= u] = 1; p[c <= dn] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    sl, ss = SWAP[nm]; sw = pd.Series(np.where(p > 0, sl, np.where(p < 0, ss, 0.0)), index=c.index) / 100 / 252
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST[nm] + p.abs() * sc * sw
def jsl():
    c = px["USDJPY"].dropna(); rt = c.pct_change(); net = pd.Series([(RD.get(y,3.23)-2.32)/100/252 for y in c.index.year], index=c.index)
    p = ((c > c.rolling(100).mean()) & (net > 0)).astype(float).shift(1).fillna(0); sc = _sc(rt)
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST["USDJPY"] + p * sc * net.clip(lower=0)

# vol-target the reversal sleeve to 10% so it is comparable-risk in the book
rb = basket(10, 200, 5)
rb_vt = (rb / (rb.rolling(50).std() * np.sqrt(252)) * 0.10).replace([np.inf, -np.inf], 0).fillna(0)
B = pd.concat({"g": dsl("XAUUSD"), "n": dsl("NAS100"), "j": jsl(), "r": rb_vt}, axis=1).dropna()
book0 = (B["g"] + B["n"] + 0.5 * B["j"]) / 2.5
print("\n(2) ADD reversal sleeve to the live book (equal-risk weight w):")
print(f"  corr(reversal, book) = {B['r'].corr(book0):+.2f}")
print(f"  {'rev wt':>6} {'Sharpe':>7} {'CAGR':>7} {'maxDD':>7} {'WF+':>5}")
for w in (0.0, 0.5, 1.0):
    P = (B["g"] + B["n"] + 0.5 * B["j"] + w * B["r"]) / (2.5 + w)
    s, cg, dd, fp = mets(P)
    print(f"  {w:>6.2f} {s:>7.2f} {cg:>+6.1%} {dd:>7.1%} {fp:>3}/5")
print("\nverdict: robust if the grid is a PLATEAU (not one lucky cell) AND adding it lifts book Sharpe / cuts DD.")
print("DONE")
