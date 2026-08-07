"""EDGE HUNT #3 — CROSS-SECTIONAL momentum (relative strength), the one big archetype not yet tested.
Distinct from time-series TSMOM: each month rank the 21-asset universe by trailing 6-mo vol-scaled
return (skip last week), go LONG the top tercile / SHORT the bottom tercile, equal-risk, monthly
rebalance (low turnover). Dollar/beta-neutral-ish => can be UNCORRELATED to our long-biased book.
Full rigor: cost on rebalance turnover, walk-forward folds, block-bootstrap 95% CI (MOE), corr to book.

Run: python research/cross_sectional_mom.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(23)
CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(CACHE)
LOOK, SKIP, HOLD_N = 126, 5, 5              # 6mo lookback, skip 1wk, long/short top&bottom 5


def build(px, look=LOOK, skip=SKIP, topn=HOLD_N):
    rets = px.pct_change()
    vol = rets.rolling(50).std() * np.sqrt(252)
    mom = (px.shift(skip) / px.shift(skip + look) - 1) / vol       # vol-scaled trailing momentum
    # monthly rebalance dates = last trading day each month
    s = pd.Series(px.index, index=px.index)
    is_last = s.groupby(px.index.to_period("M")).transform("max") == s
    rebal = set(px.index[is_last.values])
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    cur = pd.Series(0.0, index=px.columns)
    for d in px.index:
        if d in rebal:
            m = mom.loc[d].dropna()
            if len(m) >= 3 * topn:
                longs = m.nlargest(topn).index; shorts = m.nsmallest(topn).index
                cur = pd.Series(0.0, index=px.columns)
                cur[longs] = 1.0 / topn; cur[shorts] = -1.0 / topn
        w.loc[d] = cur
    # vol-target each leg to ~10% via inverse-vol, then daily pnl
    iv = (0.10 / vol).clip(upper=3).fillna(0)
    pos = (w * iv).shift(1).fillna(0)
    gross = (pos * rets).sum(axis=1)
    turn = (w * iv).diff().abs().fillna(0)
    cost = pd.Series({c: COST[c] for c in px.columns})
    tc = (turn * cost).sum(axis=1)
    return (gross - tc).dropna()


def boot(r, n=3000, block=20):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    out = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); out.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(out, [2.5, 97.5])


r = build(px)
lo, hi = boot(r)
folds = [sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))]) for a, b in FOLDS]
fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(r)) - 1
print("CROSS-SECTIONAL MOMENTUM (long top5 / short bottom5, 6mo mom, monthly rebal, vol-target)\n")
print(f"  Sharpe {sharpe(r):+.2f}   95% CI[{lo:+.2f},{hi:+.2f}]   CAGR {cagr:+.1%}   maxDD {dd:.1%}   WF+ {fp}/5")
print(f"  per-fold OOS Sharpe: " + " ".join(f"{x:+.2f}" for x in folds))

# robustness: different lookbacks (not used to pick)
print("\n  robustness (lookback months):")
for lb in (63, 126, 189, 252):
    rr = build(px, look=lb)
    print(f"    {lb//21}mo: Sharpe {sharpe(rr):+.2f}  CAGR {(1+rr).cumprod().iloc[-1]**(252/len(rr))-1:+.1%}")

# correlation to the live book
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}
def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def dsl(nm):
    c = px[nm].dropna(); rt = c.pct_change(); up = c.rolling(100).max().shift(1); dn = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= up] = 1; p[c <= dn] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    sl, ss = SWAP[nm]; sw = pd.Series(np.where(p > 0, sl, np.where(p < 0, ss, 0.0)), index=c.index) / 100 / 252
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST[nm] + p.abs() * sc * sw
def jsl():
    c = px["USDJPY"].dropna(); rt = c.pct_change(); net = pd.Series([(RD.get(y,3.23)-2.32)/100/252 for y in c.index.year], index=c.index)
    p = ((c > c.rolling(100).mean()) & (net > 0)).astype(float).shift(1).fillna(0); sc = _sc(rt)
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST["USDJPY"] + p * sc * net.clip(lower=0)
B = pd.concat({"g": dsl("XAUUSD"), "n": dsl("NAS100"), "j": jsl()}, axis=1).dropna()
book = (B["g"] + B["n"] + 0.5 * B["j"]) / 2.5
jj = pd.concat([r.rename("csm"), book.rename("book")], axis=1).dropna()
print(f"\n  correlation to LIVE book = {jj['csm'].corr(jj['book']):+.2f}  (n={len(jj)})")
print("\nKEEP if 95% CI > 0 AND WF majority green AND corr to book low. Else discard.")
print("DONE")
