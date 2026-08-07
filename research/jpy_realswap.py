"""USDJPY carry with the REAL FBS-demo broker swap (measured 2026-07-15), not the idealized rate-diff.
Broker pays long +0.91%/yr (swap_long 4.05pts) and charges short -5.56%/yr (swap_short -24.71pts) ->
implied broker MARKUP ~2.32%/yr, fair rate-diff ~3.23%/yr. So the carry a LONG actually banks each year
= max-ish(rate_diff - 2.32%). In low-diff years (2015-16, 2020-21) net carry goes NEGATIVE -> you PAY to
hold. Re-tests the JPY sleeve + the 3-sleeve book under this realistic swap, two gates:
  (A) trend-only gate (long when >SMA100), eat negative net-swap when it happens
  (B) NET-carry gate  (long when >SMA100 AND net_swap>0) -> sit out negative-carry regimes

Run: python research/jpy_realswap.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import donchian_ret, sharpe, COST, FOLDS

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.23}
MARKUP = 2.32                      # measured FBS broker swap markup, %/yr

px = pd.read_parquet(CACHE)
c = px["USDJPY"].dropna(); ret = c.pct_change()
yrs = c.index.year
model_swap = pd.Series([RATEDIFF.get(y,3.23)/100/252 for y in yrs], index=c.index)              # idealized +3%
real_swap  = pd.Series([(RATEDIFF.get(y,3.23)-MARKUP)/100/252 for y in yrs], index=c.index)      # net of markup
sma = c.rolling(100).mean(); up = (c > sma)
vol = ret.rolling(50).std().shift(1)*np.sqrt(252); scale=(0.10/vol).clip(upper=3).fillna(0)


def mets(r, tag):
    r=r.dropna(); eq=(1+r).cumprod(); dd=(eq/eq.cummax()-1).min(); cagr=eq.iloc[-1]**(252/len(r))-1
    fp=sum(1 for a,b in FOLDS if (s:=sharpe(r[(r.index>=pd.Timestamp(a,tz='UTC'))&(r.index<pd.Timestamp(b,tz='UTC'))]))>0 and np.isfinite(s))
    print(f"  {tag:34} Sharpe {sharpe(r):+.2f}  CAGR {cagr:+.1%}  maxDD {dd:5.1%}  WF+ {fp}/5")
    return r

print(f"USDJPY carry: MODEL(+3%) vs REAL FBS swap (markup {MARKUP}%/yr)")
print(f"net long carry by year (real): " + ", ".join(f"{y}:{RATEDIFF[y]-MARKUP:+.1f}%" for y in (2019,2021,2022,2023,2025,2026)) + "\n")

print("JPY sleeve (vol-targeted 10%):")
posA = up.astype(float).shift(1).fillna(0)                        # trend-only gate
posB = (up & (real_swap>0)).astype(float).shift(1).fillna(0)      # net-carry gate
jm  = mets(posA*scale*(ret+model_swap), "A trend-gate  + MODEL swap (old)")
jrA = mets(posA*scale*(ret+real_swap),  "A trend-gate  + REAL swap")
jrB = mets(posB*scale*(ret+real_swap),  "B netcarry-gate + REAL swap")

# 3-sleeve book at varying JPY weight, using the REAL-swap JPY (net-carry gate = the honest one)
g = donchian_ret(px["XAUUSD"].dropna(), COST["XAUUSD"], 100)
n = donchian_ret(px["NAS100"].dropna(), COST["NAS100"], 100)
print("\n3-sleeve book (GOLD+NAS+w*JPY)/(2+w), JPY = REAL swap, net-carry gate:")
print(f"  {'JPY wt':>7} {'Sharpe':>7} {'CAGR':>7} {'maxDD':>7} {'WF+':>5}")
for jpy, lbl in ((jrB,"REAL"),):
    S = pd.concat({"g":g,"n":n,"j":jpy}, axis=1).dropna()
    for w in (0.0, 0.25, 0.5, 0.75, 1.0):
        P=((S["g"]+S["n"]+w*S["j"])/(2+w)).dropna(); eq=(1+P).cumprod()
        dd=(eq/eq.cummax()-1).min(); cg=eq.iloc[-1]**(252/len(P))-1
        fp=sum(1 for a,b in FOLDS if (s:=sharpe(P[(P.index>=pd.Timestamp(a,tz='UTC'))&(P.index<pd.Timestamp(b,tz='UTC'))]))>0)
        print(f"  {w:>7.2f} {sharpe(P):>7.2f} {cg:>7.1%} {dd:>7.1%} {fp:>3}/5")
print("\nread: if REAL-swap JPY sleeve Sharpe collapses vs MODEL, the carry edge was mostly broker-swap fantasy;")
print("pick the JPY weight where the book still improves under REAL swap (not the model).")
print("DONE")
