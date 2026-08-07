"""RAISE THE SHARPE — rebuild the book the way real systematic funds do (Carver/AHL), not the crude
single-rule version. Three structural upgrades, each legit (diversification, not curve-fit):
  1. MULTI-SPEED trend (EWMAC 16/64, 32/128, 64/256) combined, per asset  [vs single Donchian-100]
  2. BROAD universe — trend across ALL 21 instruments, diversified   [vs gold+NAS+SP only]
  3. Combine trend + JPY-carry + equity-reversal with risk weighting  [vs naive /3.5]
Measure combined Sharpe/CAGR/DD + walk-forward + bootstrap MOE, and compare to the old 0.70 book.

Run: python research/multi_speed_book.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(47)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}
SPEEDS = [(16, 64), (32, 128), (64, 256)]


def ewmac_trend(name):
    c = px[name].dropna()
    if len(c) < 400: return None
    ret = c.pct_change()
    pvol = c.diff().ewm(span=36).std()
    fcs = []
    for f, s in SPEEDS:
        raw = c.ewm(span=f).mean() - c.ewm(span=s).mean()
        fc = raw / pvol.replace(0, np.nan)
        fc = fc / fc.abs().rolling(252, min_periods=60).mean() * 10   # forecast scalar (trailing, no lookahead)
        fcs.append(fc.clip(-20, 20))
    fc = pd.concat(fcs, axis=1).mean(axis=1).clip(-20, 20)
    ivol = ret.rolling(50).std() * np.sqrt(252)
    pos = ((fc / 10) * (0.10 / ivol).clip(upper=3)).shift(1).fillna(0)
    # swap overlay if we know it (gold/nas), else spread only
    r = pos * ret - pos.diff().abs().fillna(0) * COST[name]
    if name in SWAP:
        sl, ss = SWAP[name]; sw = pd.Series(np.where(pos > 0, sl, np.where(pos < 0, ss, 0.0)), index=c.index) / 100 / 252
        r = r + pos.abs() * sw
    return r.dropna()


def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def jpy_carry():
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


def mets(r):
    r = r.dropna(); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min(); cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    fp = sum(1 for a, b in FOLDS if np.isfinite(s := sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))])) and s > 0)
    return sharpe(r), cagr, dd, fp
def boot(r, n=3000, block=15):
    r = r.dropna().values; N = len(r); nb = max(1, N // block); out = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); out.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(out, [2.5, 97.5])


# 1. diversified multi-speed trend across the whole universe
trends = {n: ewmac_trend(n) for n in px.columns}
trends = {n: r for n, r in trends.items() if r is not None}
DT = pd.concat(trends, axis=1)
div_trend = DT.mean(axis=1)                                  # equal-risk avg (each already ~vol-targeted)
print(f"multi-speed trend built on {len(trends)} instruments\n")
sh, cg, dd, fp = mets(div_trend); lo, hi = boot(div_trend)
print(f"DIVERSIFIED MULTI-SPEED TREND: Sharpe {sh:+.2f} CI[{lo:+.2f},{hi:+.2f}] CAGR {cg:+.1%} maxDD {dd:.1%} WF {fp}/5")

# compare: OLD single-rule trend (gold+nas Donchian-100 only) — replicate quickly
def donch(nm):
    c = px[nm].dropna(); rt = c.pct_change(); u = c.rolling(100).max().shift(1); dn = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= u] = 1; p[c <= dn] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    sl, ss = SWAP[nm]; sw = pd.Series(np.where(p > 0, sl, np.where(p < 0, ss, 0.0)), index=c.index) / 100 / 252
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST[nm] + p.abs() * sc * sw
old_trend = pd.concat({"g": donch("XAUUSD"), "n": donch("NAS100")}, axis=1).mean(axis=1)
print(f"  (old single Donchian-100 gold+nas trend: Sharpe {mets(old_trend)[0]:+.2f})\n")

# 2. sleeves
carry = jpy_carry()
revb = pd.concat({n: rev(n) for n in ("NAS100", "SP500", "NIKKEI")}, axis=1).mean(axis=1)
for nm, r in (("JPY carry", carry), ("equity reversal", revb)):
    s2 = mets(r); print(f"{nm:16}: Sharpe {s2[0]:+.2f} CAGR {s2[1]:+.1%} maxDD {s2[2]:.1%}")

# 3. combined book (risk-weight: trend gets more, carry less) + correlations
B = pd.concat({"trend": div_trend, "carry": carry, "rev": revb}, axis=1).dropna()
print("\nsleeve correlation:\n" + B.corr().round(2).to_string())
for name, w in (("equal-weight", [1,1,1]), ("trend-heavy (2,0.5,1)", [2,0.5,1])):
    P = (B * w).sum(axis=1) / sum(w)
    s, cg, dd, fp = mets(P); lo, hi = boot(P)
    print(f"\nCOMBINED [{name}]: Sharpe {s:+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  CAGR {cg:+.1%}  maxDD {dd:.1%}  WF {fp}/5")
print("\nvs OLD crude book Sharpe 0.70. Higher = the construction upgrade was real (diversification, not overfit).")
print("DONE")
