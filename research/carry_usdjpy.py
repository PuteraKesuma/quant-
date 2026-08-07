"""Test the USDJPY CARRY edge honestly — the one structural USDJPY thread. Long USDJPY earns positive
swap (US-JP short-rate differential); price-only backtests MISS it. We model swap as a daily accrual
from an APPROXIMATE annual US-JP rate-diff schedule (transparent approximation; real broker swap has a
markup). Strategies: always-long (pure carry) vs carry+trend-filter (long only when carry>0 AND uptrend).
Show WITH vs WITHOUT swap to isolate carry's contribution. Walk-forward folds + per-year + maxDD (the
unwind risk). Daily USDJPY 2011-2026 from the cached universe.

Run: python research/carry_usdjpy.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import sharpe, FOLDS

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
# approx US minus JP short-rate differential, annual avg % (Fed funds vs BoJ policy)
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.0}

px = pd.read_parquet(CACHE)
c = px["USDJPY"].dropna()
ret = c.pct_change()
swap = pd.Series([RATEDIFF.get(y,3.0)/100/252 for y in c.index.year], index=c.index)  # daily long-swap return


def report(r, tag):
    r = r.dropna()
    if len(r) < 100: print(f"  {tag}: n/a"); return
    eq=(1+r).cumprod(); dd=(eq/eq.cummax()-1).min(); cagr=eq.iloc[-1]**(252/len(r))-1
    yr=r.groupby(r.index.year).sum(); grn=int((yr>0).sum())
    fsh=[sharpe(r[(r.index>=pd.Timestamp(a,tz='UTC'))&(r.index<pd.Timestamp(b,tz='UTC'))]) for a,b in FOLDS]
    fp=sum(1 for s in fsh if np.isfinite(s) and s>0)
    print(f"  {tag:26} Sharpe {sharpe(r):+.2f}  CAGR {cagr:+.1%}  maxDD {dd:.1%}  yrs+ {grn}/{len(yr)}  WF+ {fp}/{len(fsh)}  [{' '.join(f'{s:+.1f}' for s in fsh)}]")


sma = c.rolling(100).mean()
up = (c > sma)            # trend filter
carry_on = (swap > 0)     # positive carry regime

print("USDJPY CARRY test (daily 2011-2026; swap = approx US-JP rate diff / 252)")
print(f"avg annual swap earned when long: {swap.mean()*252*100:.1f}%\n")

print("ALWAYS-LONG:")
report(ret, "price-only")
report(ret + swap, "price + SWAP")
print("\nCARRY + TREND FILTER (long only when carry>0 AND close>SMA100, else flat):")
pos = (up & carry_on).astype(float).shift(1).fillna(0)
report(pos*ret, "price-only")
report(pos*(ret + swap), "price + SWAP")
print("\nCARRY + TREND, VOL-TARGETED 10%:")
vol = ret.rolling(50).std().shift(1)*np.sqrt(252); scale=(0.10/vol).clip(upper=3).fillna(0)
report(pos*scale*ret, "price-only")
report(pos*scale*(ret+swap), "price + SWAP")

print("\nread: carry is REAL if 'price+SWAP' is clearly better than price-only AND robust across WF folds/years;")
print("watch maxDD = the carry-unwind risk (2024-08 style). If the edge is ONLY the swap with huge DD, it's fragile.")
print("DONE")
