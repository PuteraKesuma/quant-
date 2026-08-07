"""THE honest answer: does adding USDJPY lower DD and/or raise return — under FULL REAL cost?
Adds the measured FBS overnight swap to EVERY sleeve (not just JPY):
  XAUUSD long -6.30%/yr / short +2.24%/yr ; US100 long -1.44 / short -0.31 ; USDJPY net-carry (real).
Sleeves: GOLD & NAS = Donchian-100 stop&reverse (both directions, so swap sign follows position);
JPY = carry long, net-carry gate (long only when rate_diff>broker_markup). All vol-targeted 10%, spread
cost on flips (walkforward_trend.COST). Compares book WITHOUT vs WITH JPY at several weights.

Run: python research/book_realcost.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import COST, sharpe, FOLDS

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
px = pd.read_parquet(CACHE)
SWAP = {"XAUUSD": (-6.30, +2.24), "NAS100": (-1.44, -0.31)}      # (long %/yr, short %/yr), measured
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.23}
MARKUP = 2.32


def _scale(ret):
    vol = ret.rolling(50).std().shift(1) * np.sqrt(252)
    return (0.10 / vol).clip(upper=3).fillna(0)


def donch_sleeve(name, N=100, swap=True):
    c = px[name].dropna(); ret = c.pct_change()
    up = c.rolling(N).max().shift(1); dn = c.rolling(N).min().shift(1)
    pos = pd.Series(np.nan, index=c.index); pos[c >= up] = 1.0; pos[c <= dn] = -1.0
    pos = pos.ffill().shift(1); scale = _scale(ret)
    r = pos * scale * ret - pos.diff().abs().fillna(0) * scale * COST[name]
    if swap:
        sl, ss = SWAP[name]
        sw = pd.Series(np.where(pos > 0, sl, np.where(pos < 0, ss, 0.0)), index=c.index) / 100 / 252
        r = r + pos.abs() * scale * sw
    return r


def jpy_sleeve(swap=True):
    c = px["USDJPY"].dropna(); ret = c.pct_change()
    net = pd.Series([(RATEDIFF.get(y, 3.23) - MARKUP) / 100 / 252 for y in c.index.year], index=c.index)
    sma = c.rolling(100).mean()
    pos = ((c > sma) & (net > 0)).astype(float).shift(1).fillna(0)      # net-carry gate
    scale = _scale(ret)
    r = pos * scale * ret - pos.diff().abs().fillna(0) * scale * COST["USDJPY"]
    if swap:
        r = r + pos * scale * net.clip(lower=0)     # earn the (positive) net carry while long
    return r


def mets(r):
    r = r.dropna(); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    fp = sum(1 for a, b in FOLDS
             if np.isfinite(s := sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))])) and s > 0)
    return sharpe(r), cagr, dd, fp


g0 = donch_sleeve("XAUUSD", swap=False); n0 = donch_sleeve("NAS100", swap=False); j0 = jpy_sleeve(swap=False)
g1 = donch_sleeve("XAUUSD", swap=True);  n1 = donch_sleeve("NAS100", swap=True);  j1 = jpy_sleeve(swap=True)

print("Per-sleeve, price+spread ONLY vs +REAL overnight swap:")
for nm, a, b in (("GOLD", g0, g1), ("NAS", n0, n1), ("JPY", j0, j1)):
    sa = mets(a); sb = mets(b)
    print(f"  {nm:4} no-swap  Sh {sa[0]:+.2f} CAGR {sa[1]:+.1%} DD {sa[2]:5.1%}   |  +swap  Sh {sb[0]:+.2f} CAGR {sb[1]:+.1%} DD {sb[2]:5.1%}")

print("\nBOOK under FULL REAL cost (gold+nas swap included), JPY weight sweep:")
S = pd.concat({"g": g1, "n": n1, "j": j1}, axis=1).dropna()
print(f"  {'JPY wt':>7} {'Sharpe':>7} {'CAGR':>7} {'maxDD':>7} {'WF+':>5}")
base = None
for w in (0.0, 0.25, 0.5, 0.75, 1.0):
    P = ((S["g"] + S["n"] + w * S["j"]) / (2 + w)).dropna()
    sh, cg, dd, fp = mets(P)
    if w == 0.0: base = (sh, cg, dd)
    tag = "" if w else "  <- no JPY (baseline)"
    print(f"  {w:>7.2f} {sh:>7.2f} {cg:>7.1%} {dd:>7.1%} {fp:>3}/5{tag}")

print("\nDelta from adding JPY at 0.5x vs no-JPY (full real cost):")
P0 = ((S["g"] + S["n"]) / 2).dropna(); P5 = ((S["g"] + S["n"] + 0.5 * S["j"]) / 2.5).dropna()
m0, m5 = mets(P0), mets(P5)
print(f"  Sharpe {m0[0]:+.2f} -> {m5[0]:+.2f}   CAGR {m0[1]:+.1%} -> {m5[1]:+.1%}   maxDD {m0[2]:.1%} -> {m5[2]:.1%}")
print("DONE")
