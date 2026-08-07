"""Legit Sharpe-raising, take 2: (a) multi-speed trend on the TRENDING subset only (not the whole
diluting universe), (b) OPTIMAL risk weights across trend/carry/reversal (lean to the high-Sharpe,
low-corr sleeves) via grid search. Honest ceiling = the max Sharpe these edges actually support.

Run: python research/opt_book.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(53)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31), "SP500": (-1.44, -0.31),
        "DOW": (-1.44, -0.31), "NIKKEI": (-0.5, -0.5), "DAX": (-1.0, -0.5)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}
SPEEDS = [(16, 64), (32, 128), (64, 256)]
TRENDERS = ["XAUUSD", "NAS100", "SP500", "DOW", "NIKKEI", "DAX"]


def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def ms_trend(name):
    c = px[name].dropna(); ret = c.pct_change(); pvol = c.diff().ewm(span=36).std()
    fcs = []
    for f, s in SPEEDS:
        fc = (c.ewm(span=f).mean() - c.ewm(span=s).mean()) / pvol.replace(0, np.nan)
        fc = fc / fc.abs().rolling(252, min_periods=60).mean() * 10; fcs.append(fc.clip(-20, 20))
    fc = pd.concat(fcs, axis=1).mean(axis=1).clip(-20, 20)
    ivol = ret.rolling(50).std() * np.sqrt(252)
    pos = ((fc / 10) * (0.10 / ivol).clip(upper=3)).shift(1).fillna(0)
    r = pos * ret - pos.diff().abs().fillna(0) * COST[name]
    if name in SWAP:
        sl, ss = SWAP[name]; sw = pd.Series(np.where(pos > 0, sl, np.where(pos < 0, ss, 0.0)), index=c.index) / 100 / 252
        r = r + pos.abs() * sw
    return r.dropna()
def donch(nm):
    c = px[nm].dropna(); rt = c.pct_change(); u = c.rolling(100).max().shift(1); dn = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= u] = 1; p[c <= dn] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    sl, ss = SWAP[nm]; sw = pd.Series(np.where(p > 0, sl, np.where(p < 0, ss, 0.0)), index=c.index) / 100 / 252
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST[nm] + p.abs() * sc * sw
def jpy_carry():
    c = px["USDJPY"].dropna(); rt = c.pct_change(); net = pd.Series([(RD.get(y,3.23)-2.32)/100/252 for y in c.index.year], index=c.index)
    p = ((c > c.rolling(100).mean()) & (net > 0)).astype(float).shift(1).fillna(0); sc = _sc(rt)
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST["USDJPY"] + p * sc * net.clip(lower=0)
def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))
def rev(name):
    c = px[name].dropna(); ret = c.pct_change(); r2 = rsi(c, 2).values
    up = (c > c.rolling(200).mean()).values; ex = (c > c.rolling(5).mean()).values
    pos = np.zeros(len(c)); ip = False
    for i in range(1, len(c)):
        if not ip and r2[i] < 10 and up[i]: ip = True
        elif ip and ex[i]: ip = False
        pos[i] = 1.0 if ip else 0.0
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    r = pos * ret - pos.diff().abs().fillna(0) * COST[name]
    return (r / (r.rolling(50).std() * np.sqrt(252)) * 0.10).replace([np.inf, -np.inf], 0).fillna(0)
def shp(r): return sharpe(r.dropna())
def mets(r):
    r = r.dropna(); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    fp = sum(1 for a, b in FOLDS if np.isfinite(s := sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))])) and s > 0)
    return sharpe(r), cagr, dd, fp

ms = pd.concat({n: ms_trend(n) for n in TRENDERS}, axis=1).mean(axis=1)
dc = pd.concat({n: donch(n) for n in ("XAUUSD", "NAS100")}, axis=1).mean(axis=1)
print(f"multi-speed trend (6 trenders): Sharpe {shp(ms):+.2f}   vs   single-Donchian gold+nas: {shp(dc):+.2f}")
best_trend = ms if shp(ms) >= shp(dc) else dc
carry = jpy_carry(); revb = pd.concat({n: rev(n) for n in ("NAS100", "SP500", "NIKKEI")}, axis=1).mean(axis=1)
print(f"sleeves -> trend {shp(best_trend):+.2f}  carry {shp(carry):+.2f}  reversal {shp(revb):+.2f}\n")

B = pd.concat({"t": best_trend, "c": carry, "r": revb}, axis=1).dropna()
best = (None, -9)
for wt in np.arange(0, 1.01, 0.1):
    for wc in np.arange(0, 1.01 - wt + 1e-9, 0.1):
        wr = 1 - wt - wc
        if wr < -1e-9: continue
        P = wt * B["t"] + wc * B["c"] + wr * B["r"]
        s = shp(P)
        if s > best[1]: best = ((round(wt, 1), round(wc, 1), round(wr, 1)), s)
w = best[0]
P = w[0] * B["t"] + w[1] * B["c"] + w[2] * B["r"]
s, cg, dd, fp = mets(P)
print(f"MAX-SHARPE weights  trend={w[0]} carry={w[1]} reversal={w[2]}")
print(f"OPTIMIZED BOOK: Sharpe {s:+.2f}  CAGR {cg:+.1%}  maxDD {dd:.1%}  WF {fp}/5   (old crude book 0.70)")
# honest caveat: these weights are fit in-sample; show equal-weight for reference
Pe = B.mean(axis=1); print(f"  reference equal-weight: Sharpe {shp(Pe):+.2f}  (gap vs optimized = in-sample fitting)")
print("DONE")
