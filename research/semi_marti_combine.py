"""SEMI-MARTI COMBINE — the sweep found H1-EMA20 trend is the big lever (OOS 1.59, avgR +0.29,
6/6 green). Now: (1) confirm EMA20 is a STABLE peak (test 10/15/20/25 — a lone spike = overfit,
a plateau = real), (2) test COMBINING the winning filters (session, strength, depth) on top of
EMA20 — watching that n stays healthy and MC5/per-year hold (combining shrinks the sample = overfit
risk). Accept only OOS+MC5 up AND >=5/6 green AND n>=300. Run: python research/semi_marti_combine.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

COST, NORM = 0.60, 100
M1 = load_m1("XAUUSD")
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
c = m5["close"]
macd_sig = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mn, mx = macd_sig.rolling(NORM).min(), macd_sig.rolling(NORM).max()
macd_norm = np.nan_to_num(((macd_sig - mn) / (mx - mn).replace(0, np.nan) * 100).values, nan=50)
pmn, pmx = c.rolling(NORM).min(), c.rolling(NORM).max()
price_norm = np.nan_to_num(((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).values, nan=50)

def atr_wilder(h, nn=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / nn, adjust=False).mean()
atr = atr_wilder(m5).shift(1).values
o = m5["open"].values; hi_a = m5["high"].values; lo_a = m5["low"].values
idx = m5.index; n = len(m5); hours = idx.hour.values
h1 = M1.resample("1h").agg({"close": "last"}).dropna()
h1_atr = atr_wilder(M1.resample("1h").agg({"close": "last", "high": "max", "low": "min"}).dropna()).reindex(idx, method="ffill").fillna(1).values

def slope(ema_n):
    return np.sign(h1["close"].ewm(span=ema_n, adjust=False).mean().diff()).reindex(idx, method="ffill").fillna(0).values
def dist(ema_n):
    return (h1["close"] - h1["close"].ewm(span=ema_n, adjust=False).mean()).abs().reindex(idx, method="ffill").fillna(0).values

def sim(t1, strength=None, lo_lvl=15, hi_lvl=80, am=3.0, tp_r=3.0, sess=None):
    rb = (macd_norm <= lo_lvl) & (price_norm <= lo_lvl)
    rs = (macd_norm >= hi_lvl) & (price_norm >= hi_lvl)
    tr = []; pos = 0; entry = sl = tp = 0.0; e_ts = None
    for i in range(1, n):
        if pos == 0:
            if not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
            if sig == 0:
                continue
            if sess and not (sess[0] <= hours[i] < sess[1]):
                continue
            wt = (sig == 1 and t1[i - 1] > 0) or (sig == -1 and t1[i - 1] < 0)
            if strength is not None and strength[0][i - 1] < strength[1] * h1_atr[i - 1]:
                wt = False
            if not wt:
                continue
            entry = o[i]; pos = sig; e_ts = idx[i]
            sl = entry - sig * am * atr[i]; tp = entry + sig * tp_r * am * atr[i]
        else:
            risk = abs(entry - sl)
            if pos == 1:
                ex = sl if lo_a[i] <= sl else (tp if hi_a[i] >= tp else None)
            else:
                ex = sl if hi_a[i] >= sl else (tp if lo_a[i] <= tp else None)
            if ex is not None:
                tr.append((e_ts, idx[i], (pos * (ex - entry) - COST) / risk)); pos = 0
    return tr

def rep(tag, tr):
    if len(tr) < 40:
        print(f"  {tag:40s} n={len(tr):4d} (few)"); return
    r = np.array([t[2] for t in tr]); _, oos = split([(t[1], t[2]) for t in tr])
    opf = stats(oos)["pf"]; mc = mc_pf_p5(list(r))
    yrs = (tr[-1][1] - tr[0][0]).days / 365.25; permo = len(tr) / yrs / 12
    py = [r[[pd.Timestamp(t[1]).year == y for t in tr]].sum() for y in range(2021, 2027)]
    green = sum(1 for x in py if x > 0)
    healthy = opf > 1.15 and mc >= 1.0 and r.mean() > 0 and len(tr) >= 300 and green >= 5
    print(f"  {tag:40s} n={len(tr):4d} {permo:4.1f}/mo WR={100*(r>0).mean():3.0f}% OOS={opf:4.2f} "
          f"MC5={mc:4.2f} avgR={r.mean():+.2f} moR={permo*r.mean():+4.1f} yr+={green}/6 {'[OK]' if healthy else ''}")
    if len(tr) >= 200:
        print("       per-yr R: " + " ".join(f"{y%100}:{v:+.0f}" for y, v in zip(range(2021, 2027), py)))

print("### peak check: is EMA20 a stable plateau or a lone spike? ###")
for e in (10, 15, 20, 25, 35):
    rep(f"H1 EMA{e} slope (base)", sim(slope(e)))

T20 = slope(20)
print("\n### combine winning filters on top of EMA20 ###")
rep("EMA20 + session7-20", sim(T20, sess=(7, 20)))
rep("EMA20 + depth10/90", sim(T20, lo_lvl=10, hi_lvl=90))
rep("EMA20 + strength1.0", sim(T20, strength=(dist(20), 1.0)))
rep("EMA20 + session7-20 + depth10/90", sim(T20, sess=(7, 20), lo_lvl=10, hi_lvl=90))
rep("EMA20 + session7-20 + strength1.0", sim(T20, sess=(7, 20), strength=(dist(20), 1.0)))
rep("EMA20 + sess + depth + strength (ALL)", sim(T20, sess=(7, 20), lo_lvl=10, hi_lvl=90, strength=(dist(20), 1.0)))
print("\nNOTE: [OK] = OOS>1.15 & MC5>=1.0 & avgR>0 & n>=300 & >=5/6 green. Watch n shrinking = overfit.")
print("DONE")
