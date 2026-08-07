"""Walk-forward + MARGIN OF ERROR for the JPY sleeve and the book, under FULL REAL cost.
Per-fold OOS walk-forward Sharpe (no hindsight) + block-bootstrap 95% CI on annualized Sharpe and
CAGR (block=20d preserves autocorrelation). The CI IS the margin of error: a thin sleeve shows a wide
band (maybe straddling 0); a real book edge shows a band that stays positive.

Run: python research/walkforward_moe.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from walkforward_trend import COST, sharpe, FOLDS

np.random.seed(7)
CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
px = pd.read_parquet(CACHE)
SWAP = {"XAUUSD": (-6.30, +2.24), "NAS100": (-1.44, -0.31)}
RATEDIFF = {2011:0.0,2012:0.0,2013:0.0,2014:0.0,2015:0.1,2016:0.4,2017:1.0,2018:2.0,2019:2.1,
            2020:0.2,2021:0.2,2022:1.7,2023:5.0,2024:4.9,2025:3.6,2026:3.23}
MARKUP = 2.32


def _scale(ret):
    vol = ret.rolling(50).std().shift(1) * np.sqrt(252)
    return (0.10 / vol).clip(upper=3).fillna(0)


def donch(name, N=100):
    c = px[name].dropna(); ret = c.pct_change()
    up = c.rolling(N).max().shift(1); dn = c.rolling(N).min().shift(1)
    pos = pd.Series(np.nan, index=c.index); pos[c >= up] = 1.0; pos[c <= dn] = -1.0
    pos = pos.ffill().shift(1); scale = _scale(ret)
    r = pos * scale * ret - pos.diff().abs().fillna(0) * scale * COST[name]
    sl, ss = SWAP[name]
    sw = pd.Series(np.where(pos > 0, sl, np.where(pos < 0, ss, 0.0)), index=c.index) / 100 / 252
    return r + pos.abs() * scale * sw


def jpy():
    c = px["USDJPY"].dropna(); ret = c.pct_change()
    net = pd.Series([(RATEDIFF.get(y, 3.23) - MARKUP) / 100 / 252 for y in c.index.year], index=c.index)
    pos = ((c > c.rolling(100).mean()) & (net > 0)).astype(float).shift(1).fillna(0)
    scale = _scale(ret)
    r = pos * scale * ret - pos.diff().abs().fillna(0) * scale * COST["USDJPY"]
    return r + pos * scale * net.clip(lower=0)


def wf_folds(r):
    out = []
    for a, b in FOLDS:
        seg = r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))]
        out.append(sharpe(seg))
    return out


def boot_ci(r, n=3000, block=20):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    shs, cgs = [], []
    for _ in range(n):
        starts = np.random.randint(0, N - block, nb)
        idx = (starts[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std()
        shs.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
        cgs.append((1 + s).prod() ** (252 / len(s)) - 1)
    return np.percentile(shs, [2.5, 50, 97.5]), np.percentile(cgs, [2.5, 50, 97.5])


g, n, j = donch("XAUUSD"), donch("NAS100"), jpy()
book_no = pd.concat({"g": g, "n": n}, axis=1).dropna().mean(axis=1)
S = pd.concat({"g": g, "n": n, "j": j}, axis=1).dropna()
book_jpy = (S["g"] + S["n"] + 0.5 * S["j"]) / 2.5

yrs = (j.dropna().index[-1] - j.dropna().index[0]).days / 365.25
print(f"FULL REAL COST — walk-forward + bootstrap MOE   (~{yrs:.1f} yrs daily, block-boot 95% CI)\n")
for tag, r in (("JPY sleeve (real swap)", j), ("BOOK  no-JPY (gold+nas)", book_no),
               ("BOOK +JPY 0.5x", book_jpy)):
    (sl, sm, sh), (cl, cm, ch) = boot_ci(r)
    folds = wf_folds(r); fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
    print(f"{tag:26} Sharpe {sharpe(r):+.2f}  95%CI[{sl:+.2f},{sh:+.2f}]   "
          f"CAGR {(1+r.dropna()).prod()**(252/len(r.dropna()))-1:+.1%} CI[{cl:+.1%},{ch:+.1%}]   WF+ {fp}/5")
    print(f"{'':26} per-fold OOS Sharpe: " + " ".join(f"{x:+.2f}" for x in folds))
print("\nread: the 95% CI = the margin of error. If it stays > 0, the edge survives sampling noise;")
print("if it straddles 0, the standalone number is not yet distinguishable from luck at this sample.")
print("DONE")
