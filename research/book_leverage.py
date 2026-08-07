"""The HONEST 'high return' answer: combine every validated edge (gold-trend + NAS-trend + JPY-carry +
equity-reversal) into the highest-Sharpe book, then show what LEVERAGE does — return scales up, but so
does drawdown and ruin risk (Sharpe is fixed by the edges). Bootstrap Monte-Carlo for the DD/ruin at
each leverage. This is how return actually gets high: (more uncorrelated edges -> higher Sharpe) x leverage.

Run: python research/book_leverage.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(41)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
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
def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))
def rev(name):
    c = px[name].dropna(); ret = c.pct_change(); r2 = rsi(c, 2).values
    up = (c > c.rolling(200).mean()).values; ex = (c > c.rolling(5).mean()).values
    pos = np.zeros(len(c)); ip = False
    for i in range(1, len(c)):
        if not ip and r2[i] < 10 and up[i]: ip = True
        elif ip and ex[i]: ip = False
        pos[i] = 1.0 if ip else 0.0
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    r = pos * ret - pos.diff().abs().fillna(0) * COST[name]
    return (r / (r.rolling(50).std() * np.sqrt(252)) * 0.10).replace([np.inf, -np.inf], 0).fillna(0)


revb = pd.concat({n: rev(n) for n in ("NAS100", "SP500", "NIKKEI")}, axis=1).mean(axis=1)
B = pd.concat({"gold": dsl("XAUUSD"), "nas": dsl("NAS100"), "jpy": jsl(), "rev": revb}, axis=1).dropna()
# equal-risk combine (each already ~10% vol); JPY half-weight (shakiest)
book = (B["gold"] + B["nas"] + 0.5 * B["jpy"] + B["rev"]) / 3.5
r = book.dropna()
base_sh = sharpe(r); base_vol = r.std() * np.sqrt(252)
eq = (1 + r).cumprod(); base_dd = (eq / eq.cummax() - 1).min(); base_cagr = eq.iloc[-1] ** (252 / len(r)) - 1
fp = sum(1 for a, b in FOLDS if np.isfinite(s := sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))])) and s > 0)
print(f"COMBINED BOOK (gold-trend + NAS-trend + JPY-carry x0.5 + equity-reversal), real cost")
print(f"  base @ vol {base_vol:.0%}: Sharpe {base_sh:+.2f}  CAGR {base_cagr:+.1%}  maxDD {base_dd:.1%}  WF+ {fp}/5\n")

# leverage sweep with bootstrap MC (1yr paths) for DD/ruin
rv = r.values
def mc(L, paths=4000, horizon=252, block=10):
    N = len(rv); nb = horizon // block; cagrs = []; dds = []; ruin = 0
    for _ in range(paths):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()[:horizon]
        s = rv[idx] * L
        eqp = np.cumprod(1 + s)
        if eqp.min() <= 0.02: ruin += 1; eqp = np.maximum(eqp, 0.001)
        cagrs.append(eqp[-1] - 1); dds.append((eqp / np.maximum.accumulate(eqp) - 1).min())
    return np.median(cagrs), np.percentile(cagrs, 5), np.median(dds), np.percentile(dds, 5), ruin / paths

print(f"  {'lev':>4} {'CAGR~':>7} {'medDD':>7} {'p5 yr ret':>10} {'p5 DD':>7} {'P(ruin/yr)':>11}")
for L in (1, 2, 3, 4, 6, 8):
    mcagr, p5cagr, mdd, p5dd, ruin = mc(L)
    print(f"  {L:>3}x {base_cagr*L:>+6.1%} {mdd:>7.1%} {p5cagr:>+9.1%} {p5dd:>7.1%} {ruin:>10.1%}")
print("\nread: Sharpe is FIXED by the edges (~0.7). Leverage moves you UP the return line — and DOWN the")
print("drawdown line by the SAME factor. 'High return' = high leverage = big DD + real ruin risk. No free")
print("lunch; more uncorrelated edges is the only way to raise return WITHOUT raising DD proportionally.")
print("DONE")
