"""STEP 2 — robustness of the 3-sleeve book: per-year P&L (consistent or few-years driven?),
cost-stress (2x/3x), Donchian-N sensitivity (not overfit to N=100?), turnover (deployable freq?),
and a 4th-sleeve candidate check. Daily 2012-2026, equal-risk, vol-targeted.

Run: python research/robustness_book.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import donchian_ret, sharpe, COST

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.0}
px = pd.read_parquet(CACHE)


def jpy():
    c=px["USDJPY"].dropna(); ret=c.pct_change()
    swap=pd.Series([RATEDIFF.get(y,3.0)/100/252 for y in c.index.year],index=c.index)
    pos=((c>c.rolling(100).mean())&(swap>0)).astype(float).shift(1).fillna(0)
    vol=ret.rolling(50).std().shift(1)*np.sqrt(252); sc=(0.10/vol).clip(upper=3).fillna(0)
    return pos*sc*(ret+swap), pos


def donch_pos(c,N=100):
    up=c.rolling(N).max().shift(1); dn=c.rolling(N).min().shift(1)
    p=pd.Series(np.nan,index=c.index); p[c>=up]=1; p[c<=dn]=-1; return p.ffill().shift(1)


def book(N=100):
    g=donchian_ret(px["XAUUSD"].dropna(),COST["XAUUSD"],N)
    n=donchian_ret(px["NAS100"].dropna(),COST["NAS100"],N)
    j,_=jpy()
    return pd.concat({"GOLD":g,"NAS":n,"JPY":j},axis=1).dropna()


def mets(r):
    r=r.dropna(); eq=(1+r).cumprod(); dd=(eq/eq.cummax()-1).min()
    return sharpe(r), eq.iloc[-1]**(252/len(r))-1, dd

S=book(100); P=S.mean(axis=1)
print("STEP 2 ROBUSTNESS — 3-sleeve book (GOLD+NAS+JPY, Donchian100, equal-risk)\n")

print("=== per-year return (%), each sleeve + portfolio ===")
yr=pd.DataFrame({k:(S[k].groupby(S.index.year).sum()*100) for k in S.columns})
yr["PORT"]=P.groupby(P.index.year).sum()*100
print(yr.round(1).to_string())
print(f"  portfolio: {(yr['PORT']>0).sum()}/{yr['PORT'].notna().sum()} green years")

print("\n=== cost-stress (portfolio) ===")
for m in (1.0,2.0,3.0):
    g=donchian_ret(px["XAUUSD"].dropna(),COST["XAUUSD"]*m,100)
    n=donchian_ret(px["NAS100"].dropna(),COST["NAS100"]*m,100)
    j,_=jpy(); Pm=pd.concat({"g":g,"n":n,"j":j},axis=1).dropna().mean(axis=1)
    sh,cg,dd=mets(Pm); print(f"  {m:.0f}x cost: Sharpe {sh:+.2f}  CAGR {cg:+.1%}  maxDD {dd:.1%}")

print("\n=== Donchian-N sensitivity (portfolio; is N=100 a lucky pick?) ===")
for N in (50,75,100,150,200):
    Sn=book(N); Pn=Sn.mean(axis=1); sh,cg,dd=mets(Pn)
    print(f"  N={N:>3}: Sharpe {sh:+.2f}  CAGR {cg:+.1%}  maxDD {dd:.1%}")

print("\n=== turnover (position changes / year, deployability) ===")
for name,c in [("GOLD",px["XAUUSD"].dropna()),("NAS",px["NAS100"].dropna())]:
    p=donch_pos(c,100); ch=(p.diff().abs()>0).groupby(p.index.year).sum().mean()
    print(f"  {name}: ~{ch:.0f} flips/year (Donchian100)")
_,pj=jpy(); print(f"  JPY: ~{(pj.diff().abs()>0).groupby(pj.index.year).sum().mean():.0f} flips/year")

print("\n=== 4th-sleeve candidates (corr to book PnL + own Sharpe) ===")
for cand in ("SP500","NIKKEI","WTI","XAGUSD","DXY"):
    if cand not in px.columns: continue
    r=donchian_ret(px[cand].dropna(),COST.get(cand,0.0003),100)
    j=pd.concat([r.rename("c"),P.rename("p")],axis=1).dropna()
    corr=j["c"].corr(j["p"]); sh=sharpe(r)
    add="  <- diversifies" if (abs(corr)<0.2 and sh>0.3) else ("  (corr high)" if abs(corr)>=0.3 else "")
    print(f"  {cand:7} own-Sharpe {sh:+.2f}  corr-to-book {corr:+.2f}{add}")
print("DONE")
