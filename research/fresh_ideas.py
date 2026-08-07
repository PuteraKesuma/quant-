"""FRESH BRAIN — three untested angles to push combined Sharpe toward 1.8, each with a real thesis,
each through the rigor gate (cost, WF, bootstrap CI, correlation to the book):
  1. REGIME-SWITCH equity: trade trend when returns are momentum-y (rolling autocorr>0), reversal when
     mean-reverting (autocorr<0) -> right tool per regime, higher Sharpe than either alone.
  2. INDEX SPREAD reversion: NAS/SP & NAS/DOW ratio z-reversion (dollar-neutral) -> market-neutral, uncorrelated.
  3. ENSEMBLE reversal: average RSI-2<10, RSI-2<5, RSI-3<15 signals -> smoother reversal.

Run: python research/fresh_ideas.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS

np.random.seed(71)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")


def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def rsi(c, n):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))
def boot(r, n=3000, block=15):
    r = r.dropna().values; N = len(r); nb = max(1, N // block); o = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); o.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(o, [2.5, 97.5])
def rep(name, r):
    r = r.dropna(); lo, hi = boot(r); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min()
    fp = sum(1 for a, b in FOLDS if np.isfinite(s := sharpe(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))])) and s > 0)
    ok = lo > 0 and fp >= 3
    print(f"  {name:34} Sharpe {sharpe(r):+.2f}  CI[{lo:+.2f},{hi:+.2f}]  maxDD {dd:5.1%}  WF {fp}/5{'  <== SURVIVES' if ok else ''}")
    return r if ok else None


# --- 1. REGIME-SWITCH equity ---
def regime_switch(name, W=40):
    c = px[name].dropna(); ret = c.pct_change()
    ac = ret.rolling(W).apply(lambda x: pd.Series(x).autocorr(lag=1), raw=False)   # +ve = momentum
    # trend signal (Donchian-50 sign) and reversal signal (RSI2<10 long in uptrend)
    up = c.rolling(50).max().shift(1); dn = c.rolling(50).min().shift(1)
    tpos = pd.Series(np.nan, index=c.index); tpos[c >= up] = 1; tpos[c <= dn] = -1; tpos = tpos.ffill()
    r2 = rsi(c, 2); upt = c > c.rolling(200).mean(); ex = c > c.rolling(5).mean()
    rp = np.zeros(len(c)); ip = False; r2v, uv, ev = r2.values, upt.values, ex.values
    for i in range(1, len(c)):
        if not ip and r2v[i] < 10 and uv[i]: ip = True
        elif ip and ev[i]: ip = False
        rp[i] = 1.0 if ip else 0.0
    rp = pd.Series(rp, index=c.index)
    pos = pd.Series(np.where(ac > 0, tpos, rp), index=c.index).shift(1).fillna(0)
    sc = _sc(ret)
    return (pos * sc * ret - pos.diff().abs().fillna(0) * sc * COST[name]).dropna()

# --- 2. INDEX SPREAD reversion ---
def spread_rev(a, b, window=40, entry=1.5, exit=0.4):
    A = px[a].dropna(); B = px[b].dropna()
    J = pd.concat([A.rename("a"), B.rename("b")], axis=1).dropna()
    ar = J["a"].pct_change(); br = J["b"].pct_change()
    ratio = J["a"] / J["b"]; z = (ratio - ratio.rolling(window).mean()) / ratio.rolling(window).std()
    pos = np.zeros(len(z)); cur = 0.0; zv = z.values
    for i in range(len(z)):
        if np.isnan(zv[i]): pos[i] = cur; continue
        if cur == 0:
            if zv[i] < -entry: cur = 1.0
            elif zv[i] > entry: cur = -1.0
        elif cur == 1 and zv[i] >= -exit: cur = 0.0
        elif cur == -1 and zv[i] <= exit: cur = 0.0
        pos[i] = cur
    pos = pd.Series(pos, index=z.index).shift(1).fillna(0)
    raw = pos * (ar - br) - pos.diff().abs().fillna(0) * (COST[a] + COST[b])
    return (raw / (raw.rolling(50).std() * np.sqrt(252)) * 0.10).replace([np.inf, -np.inf], 0).dropna()

# --- 3. ENSEMBLE reversal ---
def ensemble_rev(name):
    c = px[name].dropna(); ret = c.pct_change()
    def one(entry, rn, exsma):
        r2 = rsi(c, rn).values; upt = (c > c.rolling(200).mean()).values; ex = (c > c.rolling(exsma).mean()).values
        pos = np.zeros(len(c)); ip = False
        for i in range(1, len(c)):
            if not ip and r2[i] < entry and upt[i]: ip = True
            elif ip and ex[i]: ip = False
            pos[i] = 1.0 if ip else 0.0
        return pd.Series(pos, index=c.index)
    P = (one(10, 2, 5) + one(5, 2, 5) + one(15, 3, 5)) / 3
    pos = P.shift(1).fillna(0)
    return (pos * ret - pos.diff().abs().fillna(0) * COST[name]).dropna()


print("FRESH IDEAS through the rigor gate:\n")
print("1) REGIME-SWITCH (trend when momentum, reversal when ranging):")
rs = {n: rep(f"regime-switch {n}", regime_switch(n)) for n in ("NAS100", "SP500", "XAUUSD")}
print("\n2) INDEX SPREAD reversion (dollar-neutral):")
sp = {}
for a, b in (("NAS100", "SP500"), ("NAS100", "DOW"), ("SP500", "DOW")):
    sp[f"{a}/{b}"] = rep(f"spread {a}/{b}", spread_rev(a, b))
print("\n3) ENSEMBLE reversal:")
en = {n: rep(f"ensemble-rev {n}", ensemble_rev(n)) for n in ("NAS100", "SP500")}

# any survivors -> correlation to the current daily book (trend+carry+reversal)
def donch(nm):
    c = px[nm].dropna(); rt = c.pct_change(); u = c.rolling(100).max().shift(1); d = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= u] = 1; p[c <= d] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    return p * sc * rt
def rev0(name):
    c = px[name].dropna(); ret = c.pct_change(); r2 = rsi(c, 2).values
    up = (c > c.rolling(200).mean()).values; ex = (c > c.rolling(5).mean()).values
    pos = np.zeros(len(c)); ip = False
    for i in range(1, len(c)):
        if not ip and r2[i] < 10 and up[i]: ip = True
        elif ip and ex[i]: ip = False
        pos[i] = 1.0 if ip else 0.0
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    return (pos * ret).dropna()
book = pd.concat({"trend": (donch("XAUUSD") + donch("NAS100")) / 2,
                  "rev": pd.concat({n: rev0(n) for n in ("NAS100", "SP500")}, axis=1).mean(axis=1)}, axis=1).mean(axis=1)
surv = {k: v for d in (rs, sp, en) for k, v in d.items() if v is not None}
print(f"\nSURVIVORS: {list(surv.keys()) or 'NONE'}")
for k, v in surv.items():
    j = pd.concat([v.rename('x'), book.rename('b')], axis=1).dropna()
    print(f"  {k:22} corr to book = {j['x'].corr(j['b']):+.2f}")
print("DONE")
