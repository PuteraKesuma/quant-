"""How much to trust USDJPY? Book = (GOLD + NAS + w*JPY)/(2+w) at varying JPY weight w.
Shows the Sharpe/DD trade-off so we don't over-rely on the shakiest (carry) sleeve."""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import donchian_ret, sharpe, COST, FOLDS

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.0}
px = pd.read_parquet(CACHE)
c = px["USDJPY"].dropna(); ret = c.pct_change()
swap = pd.Series([RATEDIFF.get(y,3.0)/100/252 for y in c.index.year], index=c.index)
pos = ((c>c.rolling(100).mean())&(swap>0)).astype(float).shift(1).fillna(0)
vol = ret.rolling(50).std().shift(1)*np.sqrt(252); jpy = pos*(0.10/vol).clip(upper=3).fillna(0)*(ret+swap)
g = donchian_ret(px["XAUUSD"].dropna(), COST["XAUUSD"], 100)
n = donchian_ret(px["NAS100"].dropna(), COST["NAS100"], 100)
S = pd.concat({"g":g,"n":n,"j":jpy}, axis=1).dropna()

def mets(r):
    r=r.dropna(); eq=(1+r).cumprod(); dd=(eq/eq.cummax()-1).min()
    fp=sum(1 for a,b in FOLDS if (s:=sharpe(r[(r.index>=pd.Timestamp(a,tz='UTC'))&(r.index<pd.Timestamp(b,tz='UTC'))]))>0)
    return sharpe(r), eq.iloc[-1]**(252/len(r))-1, dd, fp

print("JPY weight sweep (GOLD+NAS+w*JPY)/(2+w)")
print(f"  {'JPY wt':>7} {'Sharpe':>7} {'CAGR':>7} {'maxDD':>7} {'WF+':>5}")
for w in (0.0, 0.25, 0.5, 0.75, 1.0):
    P = (S["g"]+S["n"]+w*S["j"])/(2+w)
    sh,cg,dd,fp = mets(P)
    print(f"  {w:>7.2f} {sh:>7.2f} {cg:>7.1%} {dd:>7.1%} {fp:>3}/5")
print("DONE")
