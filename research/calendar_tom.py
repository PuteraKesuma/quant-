"""EDGE HUNT — Turn-of-Month (TOM) calendar anomaly on equity indices + gold.
Documented effect: equity returns cluster around the last trading day + first few of each month
(fund flows / rebalancing). Calendar-deterministic => structurally UNCORRELATED to trend/carry.
Pre-registered test: long on the last E + first S trading days of each month, flat otherwise.
Full rigor: walk-forward folds + cost + block-bootstrap 95% CI (MOE) + correlation to the LIVE book.
NOT optimized on the grid — the (E=1,S=3) classic is THE test; the grid is only a robustness look.

Run: python research/calendar_tom.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import COST, sharpe, FOLDS

np.random.seed(11)
CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
px = pd.read_parquet(CACHE)
INDEX = ["NAS100", "SP500", "DOW", "DAX", "FTSE", "NIKKEI", "XAUUSD"]


def tom_pos(idx, e=1, s=3):
    r = pd.Series(np.arange(len(idx)), index=idx)
    ym = idx.to_period("M")
    rank_asc = r.groupby(ym).rank(method="first")
    size = r.groupby(ym).transform("size")
    rank_desc = size - rank_asc + 1
    return ((rank_asc <= s) | (rank_desc <= e)).astype(float)


def tom_ret(name, e=1, s=3):
    c = px[name].dropna(); ret = c.pct_change()
    pos = tom_pos(c.index, e, s)
    return (pos * ret - pos.diff().abs().fillna(0) * COST[name]).dropna()


def boot_ci(r, n=3000, block=15):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    sh = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std()
        sh.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(sh, [2.5, 97.5])


def wf(r):
    return [sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))]) for a, b in FOLDS]


print("TURN-OF-MONTH  (long last 1 + first 3 trading days/month, else flat)\n")
print(f"{'asset':8} {'Sharpe':>7} {'95% CI':>16} {'CAGR':>7} {'exposure':>8} {'WF+':>5}")
streams = {}
for name in INDEX:
    r = tom_ret(name, 1, 3); streams[name] = r
    lo, hi = boot_ci(r); folds = wf(r); fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
    expo = (tom_pos(px[name].dropna().index) > 0).mean()
    eq = (1 + r).cumprod(); cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    flag = "  <== survives" if lo > 0 else ""
    print(f"{name:8} {sharpe(r):>7.2f}  [{lo:+.2f},{hi:+.2f}]   {cagr:>+6.1%} {expo:>7.0%} {fp:>3}/5{flag}")

# equal-weight index TOM basket (ex-gold) = the candidate sleeve
basket = pd.concat({n: streams[n] for n in INDEX if n != "XAUUSD"}, axis=1).mean(axis=1).dropna()
lo, hi = boot_ci(basket); folds = wf(basket); fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
eq = (1 + basket).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(basket)) - 1
print(f"\nINDEX-TOM BASKET (equal-wt, 6 indices): Sharpe {sharpe(basket):+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  "
      f"CAGR {cagr:+.1%}  maxDD {dd:.1%}  WF+ {fp}/5  per-fold " + " ".join(f"{x:+.2f}" for x in folds))

# robustness grid (NOT used to pick — just to see if the effect is a knife-edge)
print("\nrobustness grid on the BASKET (E last / S first days):")
for e in (1, 2):
    for s in (2, 3, 4):
        b = pd.concat({n: tom_ret(n, e, s) for n in INDEX if n != "XAUUSD"}, axis=1).mean(axis=1).dropna()
        print(f"  E={e} S={s}: Sharpe {sharpe(b):+.2f}  CAGR {(1+b).cumprod().iloc[-1]**(252/len(b))-1:+.1%}")

# correlation to the live book (daily gold+nas trend + jpy carry, real cost) — must be ~0 to add value
SWAP = {"XAUUSD": (-6.30, +2.24), "NAS100": (-1.44, -0.31)}
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.23}


def _scale(ret): return (0.10 / (ret.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)


def donch_sleeve(name, N=100):
    c = px[name].dropna(); ret = c.pct_change()
    up = c.rolling(N).max().shift(1); dn = c.rolling(N).min().shift(1)
    pos = pd.Series(np.nan, index=c.index); pos[c >= up] = 1.0; pos[c <= dn] = -1.0
    pos = pos.ffill().shift(1); sc = _scale(ret)
    r = pos * sc * ret - pos.diff().abs().fillna(0) * sc * COST[name]
    sl, ss = SWAP[name]; sw = pd.Series(np.where(pos > 0, sl, np.where(pos < 0, ss, 0.0)), index=c.index) / 100 / 252
    return r + pos.abs() * sc * sw


def jpy_sleeve():
    c = px["USDJPY"].dropna(); ret = c.pct_change()
    net = pd.Series([(RATEDIFF.get(y, 3.23) - 2.32) / 100 / 252 for y in c.index.year], index=c.index)
    pos = ((c > c.rolling(100).mean()) & (net > 0)).astype(float).shift(1).fillna(0); sc = _scale(ret)
    return pos * sc * ret - pos.diff().abs().fillna(0) * sc * COST["USDJPY"] + pos * sc * net.clip(lower=0)


g = donch_sleeve("XAUUSD"); nas = donch_sleeve("NAS100"); j = jpy_sleeve()
B = pd.concat({"g": g, "n": nas, "j": j}, axis=1).dropna()
book = (B["g"] + B["n"] + 0.5 * B["j"]) / 2.5
jj = pd.concat([basket.rename("tom"), book.rename("book")], axis=1).dropna()
print(f"\ncorrelation TOM-basket vs LIVE book (daily) = {jj['tom'].corr(jj['book']):+.2f}  (n={len(jj)})")
print("\nread: keep ONLY if the basket 95% CI stays > 0 AND corr to the book is low. Else discard.")
print("DONE")
