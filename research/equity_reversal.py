"""EDGE HUNT #4 — SHORT-TERM REVERSAL in equity indices (Connors RSI-2 buy-the-dip).
Documented: indices mean-revert over 2-5 days. Rule: LONG when RSI(2)<10 AND close>SMA200 (dips only
in an uptrend), exit when close>SMA5. Long-only, few-day holds, low turnover. Different horizon/logic
from our persistent trend -> test corr. Full rigor: cost, walk-forward, block-bootstrap 95% CI (MOE).

Run: python research/equity_reversal.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(29)
CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(CACHE)
IDX = ["NAS100", "SP500", "DOW", "DAX", "FTSE", "NIKKEI"]


def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def reversal_ret(name, entry=10, sma_t=200, sma_x=5):
    c = px[name].dropna(); ret = c.pct_change()
    r2 = rsi(c, 2); up = c > c.rolling(sma_t).mean(); exit_hi = c > c.rolling(sma_x).mean()
    pos = np.zeros(len(c)); inpos = False
    r2v, upv, exv = r2.values, up.values, exit_hi.values
    for i in range(1, len(c)):
        if not inpos and r2v[i] < entry and upv[i]:
            inpos = True
        elif inpos and exv[i]:
            inpos = False
        pos[i] = 1.0 if inpos else 0.0
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    return (pos * ret - pos.diff().abs().fillna(0) * COST[name]).dropna(), pos


def boot(r, n=3000, block=10):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    out = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); out.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(out, [2.5, 97.5])


print("EQUITY SHORT-TERM REVERSAL (RSI2<10 & >SMA200 -> long, exit >SMA5)\n")
print(f"{'idx':8} {'Sharpe':>7} {'95% CI':>16} {'CAGR':>7} {'expo':>5} {'WF+':>5}")
streams = {}
for name in IDX:
    r, pos = reversal_ret(name); streams[name] = r
    lo, hi = boot(r); folds = [sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))]) for a, b in FOLDS]
    fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
    eq = (1 + r).cumprod(); cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    keep = "  <==CI>0" if lo > 0 else ""
    print(f"{name:8} {sharpe(r):>7.2f}  [{lo:+.2f},{hi:+.2f}]   {cagr:>+6.1%} {pos.mean():>5.0%} {fp:>3}/5{keep}")

basket = pd.concat(streams, axis=1).mean(axis=1).dropna()
lo, hi = boot(basket); folds = [sharpe(basket[(basket.index >= pd.Timestamp(a, tz='UTC')) & (basket.index < pd.Timestamp(b, tz='UTC'))]) for a, b in FOLDS]
fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
eq = (1 + basket).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(basket)) - 1
print(f"\nBASKET (6 indices, equal-wt): Sharpe {sharpe(basket):+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  "
      f"CAGR {cagr:+.1%}  maxDD {dd:.1%}  WF+ {fp}/5  per-fold " + " ".join(f"{x:+.2f}" for x in folds))

# corr to live book
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
B = pd.concat({"g": dsl("XAUUSD"), "n": dsl("NAS100"), "j": jsl()}, axis=1).dropna()
book = (B["g"] + B["n"] + 0.5 * B["j"]) / 2.5
jj = pd.concat([basket.rename("rev"), book.rename("book")], axis=1).dropna()
print(f"correlation to LIVE book = {jj['rev'].corr(jj['book']):+.2f}  (n={len(jj)})")
print("\nKEEP if basket 95% CI > 0 AND WF majority green. Else discard.")
print("DONE")
