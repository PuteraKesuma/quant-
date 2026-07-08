"""REGIME-ADAPTIVE Golden: (1) validate the ADX-ceiling filter (skip when trend over-extended) with
WALK-FORWARD 6-month windows, (2) test the user's idea: in STRONG trends (ADX>X) don't FADE — FOLLOW
(buy strength / sell weakness). Sweep the ADX threshold. Accept only if OOS AND walk-forward windows
AND per-year all improve vs baseline (else it's overfit / not worth the added complexity).

Base = Golden fade-with-trend (EMA15, M5, ATR3 stop, TP3R). Regime = H1 ADX(14).
Run: python research/regime_adaptive.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split

COST, NORM = 0.60, 100
M1 = load_m1("XAUUSD")
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
h1 = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
c = m5["close"]
ms = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mn, mx = ms.rolling(NORM).min(), ms.rolling(NORM).max()
mnorm = np.nan_to_num(((ms - mn) / (mx - mn).replace(0, np.nan) * 100).values, nan=50)
pmn, pmx = c.rolling(NORM).min(), c.rolling(NORM).max()
pnorm = np.nan_to_num(((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).values, nan=50)
def atrw(h, nn=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / nn, adjust=False).mean()
atr5 = atrw(m5).shift(1).values
o5 = m5["open"].values; hi5 = m5["high"].values; lo5 = m5["low"].values; idx5 = m5.index
t15 = np.sign(h1["close"].ewm(span=15, adjust=False).mean().diff()).reindex(idx5, method="ffill").fillna(0).values

def adx(h, n=14):
    up = h["high"].diff(); dn = -h["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()
ADXv = adx(h1).reindex(idx5, method="ffill").fillna(0).values
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80)

def sim(skip_above=None, follow_above=None):
    tr = []; pos = 0; entry = sl = tp = 0.0; e_ts = None
    for i in range(1, len(m5)):
        if pos == 0:
            if not np.isfinite(atr5[i]) or atr5[i] <= 0:
                continue
            a = ADXv[i - 1]; up = t15[i - 1] > 0; dn = t15[i - 1] < 0
            sig = 0
            strong = follow_above is not None and a > follow_above
            if strong:                                   # FOLLOW: buy strength / sell weakness
                if rs[i - 1] and up:
                    sig = 1
                elif rb[i - 1] and dn:
                    sig = -1
            else:                                        # FADE with-trend
                if skip_above is not None and a > skip_above:
                    continue
                if rb[i - 1] and up:
                    sig = 1
                elif rs[i - 1] and dn:
                    sig = -1
            if sig == 0:
                continue
            entry = o5[i]; pos = sig; e_ts = idx5[i]; sl = entry - sig * 3 * atr5[i]; tp = entry + sig * 9 * atr5[i]
        else:
            if pos == 1:
                ex = sl if lo5[i] <= sl else (tp if hi5[i] >= tp else None)
            else:
                ex = sl if hi5[i] >= sl else (tp if lo5[i] <= tp else None)
            if ex is not None:
                tr.append((e_ts, idx5[i], pos * (ex - entry) - COST)); pos = 0
    return tr

def wf_windows(tr):
    s = pd.Series([p for _, _, p in tr], index=pd.DatetimeIndex([x for _, x, _ in tr])).sort_index()
    w = s.groupby(pd.Grouper(freq="6MS")).sum()
    w = w[w != 0]
    return int((w > 0).sum()), len(w), float(w.min())

def rep(tag, tr, base=None):
    r = np.array([t[2] for t in tr]); _, oos = split([(t[1], t[2]) for t in tr])
    opf = stats(list(r))["pf"]; oo = stats(oos)["pf"]
    py = [np.array([t[2] for t in tr if pd.Timestamp(t[1]).year == y]).sum() for y in range(2021, 2027)]
    gy = sum(1 for v in py if v > 0)
    wfp, wfn, wfworst = wf_windows(tr)
    net = r.sum(); s = pd.Series([t[2] for t in tr], index=pd.DatetimeIndex([t[1] for t in tr])).sort_index()
    dd = float((s.cumsum() - s.cumsum().cummax()).min())
    print(f"  {tag:26s} n={len(tr):4d} net=${net:+6.0f} maxDD=${dd:+5.0f} PF={opf:4.2f} OOS={oo:4.2f} "
          f"WF={wfp}/{wfn}win yr+={gy}/6")
    return dict(tag=tag, net=net, oos=oo, wf=(wfp, wfn), gy=gy, dd=dd)

print("### BASELINE (fade all regimes) ###")
b = rep("baseline", sim())

print("\n### A) skip when trend over-extended (ADX ceiling) — validate w/ walk-forward ###")
for x in (35, 40, 45):
    rep(f"skip ADX>{x}", sim(skip_above=x))

print("\n### B) regime-switch: FADE in mild, FOLLOW in strong trend (ADX>X) ###")
for x in (30, 35, 40):
    rep(f"follow>{x} (else fade)", sim(follow_above=x))

print("\n### C) combine: follow>40 AND skip nothing (adaptive) vs baseline ###")
rep("adaptive follow>35", sim(follow_above=35))
print(f"\n  baseline: net=${b['net']:+.0f} DD=${b['dd']:+.0f} OOS={b['oos']:.2f} WF={b['wf'][0]}/{b['wf'][1]} yr+={b['gy']}/6")
print("  ACCEPT a variant only if net UP, DD not worse, OOS UP, WF windows UP, yr+ >= baseline.")
print("DONE")
