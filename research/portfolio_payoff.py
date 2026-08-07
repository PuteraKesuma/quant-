"""PAYOFF TEST — does combining 3 UNCORRELATED edges (gold trend + NAS trend + USDJPY carry) beat each
alone? The culmination of the research arc + the user's 'gold + uncorrelated diversifiers' thesis.
All sleeves vol-targeted 10% (equal-risk). Reports each sleeve, the strategy-return correlation matrix
(are they really uncorrelated?), and the portfolio ladder gold-only -> +NAS -> +JPY (Sharpe up? DD down?),
with walk-forward per-fold. Daily 2012-2026 (common window). Engines fixed (no per-asset cherry-pick):
Donchian N=100 for gold & NAS; carry+SMA100 filter + swap for JPY.

Run: python research/portfolio_payoff.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import donchian_ret, sharpe, COST, FOLDS

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.0}
px = pd.read_parquet(CACHE)


def jpy_carry_sleeve():
    c = px["USDJPY"].dropna(); ret = c.pct_change()
    swap = pd.Series([RATEDIFF.get(y,3.0)/100/252 for y in c.index.year], index=c.index)
    pos = ((c > c.rolling(100).mean()) & (swap > 0)).astype(float).shift(1).fillna(0)
    vol = ret.rolling(50).std().shift(1)*np.sqrt(252); scale=(0.10/vol).clip(upper=3).fillna(0)
    return pos*scale*(ret+swap)


def metrics(r):
    r=r.dropna()
    eq=(1+r).cumprod(); dd=(eq/eq.cummax()-1).min(); cagr=eq.iloc[-1]**(252/len(r))-1
    fsh=[sharpe(r[(r.index>=pd.Timestamp(a,tz='UTC'))&(r.index<pd.Timestamp(b,tz='UTC'))]) for a,b in FOLDS]
    fp=sum(1 for s in fsh if np.isfinite(s) and s>0)
    return sharpe(r), cagr, dd, fp, fsh


sleeves = {
    "GOLD(Donch100)": donchian_ret(px["XAUUSD"].dropna(), COST["XAUUSD"], 100),
    "NAS(Donch100)":  donchian_ret(px["NAS100"].dropna(), COST["NAS100"], 100),
    "JPY(carry)":     jpy_carry_sleeve(),
}
S = pd.concat(sleeves, axis=1).dropna()          # common window
print(f"PAYOFF TEST  common window {S.index.min():%Y-%m} -> {S.index.max():%Y-%m}  ({len(S)} days)\n")

print("=== individual sleeves ===")
print(f"  {'sleeve':16} {'Sharpe':>7} {'CAGR':>7} {'maxDD':>7} {'WF+':>5}  per-fold")
for name in sleeves:
    sh,cagr,dd,fp,fsh = metrics(S[name])
    print(f"  {name:16} {sh:>7.2f} {cagr:>7.1%} {dd:>7.1%} {fp:>3}/5  [{' '.join(f'{x:+.1f}' for x in fsh)}]")

print("\n=== strategy-return correlation matrix (want ~0 = true diversification) ===")
print(S.corr().round(2).to_string())

print("\n=== PORTFOLIO LADDER (equal-risk avg) — does adding uncorrelated sleeves help? ===")
print(f"  {'book':22} {'Sharpe':>7} {'CAGR':>7} {'maxDD':>7} {'WF+':>5}")
ladders = [("GOLD only", ["GOLD(Donch100)"]),
           ("GOLD + NAS", ["GOLD(Donch100)","NAS(Donch100)"]),
           ("GOLD + NAS + JPY", ["GOLD(Donch100)","NAS(Donch100)","JPY(carry)"])]
for label, cols in ladders:
    P = S[cols].mean(axis=1)
    sh,cagr,dd,fp,fsh = metrics(P)
    print(f"  {label:22} {sh:>7.2f} {cagr:>7.1%} {dd:>7.1%} {fp:>3}/5")

print("\nread: diversification PAYS if 'GOLD+NAS+JPY' Sharpe > GOLD-alone AND maxDD is LOWER (smoother equity),")
print("driven by ~0 cross-correlation. If Sharpe barely moves, the diversifiers aren't worth the complexity.")
print("DONE")
