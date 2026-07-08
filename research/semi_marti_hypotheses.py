"""SEMI-MARTI HYPOTHESIS SWEEP — strengthen the WITH-trend fade, ONE dimension at a time
(controlled experiment, anti-overfit). Baseline = H1-EMA50 slope filter, ATR3 stop, TP3R,
levels 15/80, all sessions (the current best: OOS 1.30 / MC5 1.02 / avgR +0.09).

Hypotheses:
  A) trend EMA period (how much smoothing defines "the trend")
  B) multi-TF trend alignment (H1 + H4 / H1 + Daily) — like Z's dual filter
  C) trend STRENGTH gate (only fade when trend is strong: |close-EMA| > k*ATR)
  D) entry extension depth (how oversold/overbought before we fade)
  E) TP width (let winners run further with-trend)
  F) session (London/NY only)
Accept a change only if OOS-PF AND MC5 both rise AND per-year stays consistent. Report per-year R.
Run: python research/semi_marti_hypotheses.py
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
o = m5["open"].values; hi_a = m5["high"].values; lo_a = m5["low"].values; cl = c.values
idx = m5.index; n = len(m5); hours = idx.hour.values

h1 = M1.resample("1h").agg({"close": "last", "high": "max", "low": "min"}).dropna()
h4 = M1.resample("4h").agg({"close": "last"}).dropna()
d1 = M1.resample("1D").agg({"close": "last"}).dropna()
h1_atr = atr_wilder(h1.assign(close=h1["close"])).reindex(idx, method="ffill").fillna(1).values

def trend_of(src, ema_n, mode):
    ema = src["close"].ewm(span=ema_n, adjust=False).mean()
    t = np.sign(ema.diff()) if mode == "slope" else np.sign(src["close"] - ema)
    return t.reindex(idx, method="ffill").fillna(0).values

def dist_of(ema_n):   # |H1 close - EMA| in ATR units, for strength gate
    ema = h1["close"].ewm(span=ema_n, adjust=False).mean()
    d = (h1["close"] - ema).abs()
    return d.reindex(idx, method="ffill").fillna(0).values

def sim(t1, t2=None, strength=None, lo_lvl=15, hi_lvl=80, am=3.0, tp_r=3.0, sess=None):
    rb = (macd_norm <= lo_lvl) & (price_norm <= lo_lvl)
    rs = (macd_norm >= hi_lvl) & (price_norm >= hi_lvl)
    trades = []; pos = 0; entry = sl = tp = 0.0; e_ts = None
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
            if t2 is not None:
                wt = wt and ((sig == 1 and t2[i - 1] > 0) or (sig == -1 and t2[i - 1] < 0))
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
                trades.append((e_ts, idx[i], (pos * (ex - entry) - COST) / risk)); pos = 0
    return trades

def rep(tag, tr, base_oos=1.30):
    if len(tr) < 60:
        print(f"  {tag:34s} n={len(tr):4d} (few)"); return None
    r = np.array([t[2] for t in tr]); _, oos = split([(t[1], t[2]) for t in tr])
    opf = stats(oos)["pf"]; mc = mc_pf_p5(list(r))
    yrs = (tr[-1][1] - tr[0][0]).days / 365.25; permo = len(tr) / yrs / 12
    py = [r[[pd.Timestamp(t[1]).year == y for t in tr]].sum() for y in range(2021, 2027)]
    green = sum(1 for x in py if x > 0)
    rob = opf > 1.15 and mc >= 1.0 and r.mean() > 0
    star = "[ROBUST]" if rob else ""
    beat = "  <== beats base" if (rob and opf > base_oos and mc >= 1.02) else ""
    print(f"  {tag:34s} n={len(tr):4d} {permo:4.1f}/mo WR={100*(r>0).mean():3.0f}% OOS={opf:4.2f} "
          f"MC5={mc:4.2f} avgR={r.mean():+.2f} moR={permo*r.mean():+4.1f} yr+={green}/6 {star}{beat}")
    return (tag, opf, mc, r.mean(), permo * r.mean(), green) if rob else None

T50s = trend_of(h1, 50, "slope")
res = []
print("### BASELINE ###")
rep("EMA50slope ATR3 TP3R", sim(T50s))

print("\n### A) trend EMA period (H1 slope) ###")
for e in (20, 30, 50, 100, 200):
    res.append(rep(f"A: H1 EMA{e} slope", sim(trend_of(h1, e, "slope"))))

print("\n### B) multi-TF alignment (H1-EMA50 + higher TF) ###")
res.append(rep("B: H1e50 + H4e50", sim(T50s, t2=trend_of(h4, 50, "pos"))))
res.append(rep("B: H1e50 + D1e20", sim(T50s, t2=trend_of(d1, 20, "pos"))))

print("\n### C) trend STRENGTH gate (|H1 close-EMA50| > k*ATR_H1) ###")
for k in (0.5, 1.0, 1.5):
    res.append(rep(f"C: strength k={k}", sim(T50s, strength=(dist_of(50), k))))

print("\n### D) entry extension depth (lo/hi) ###")
for lo_l, hi_l in ((20, 75), (15, 80), (10, 85), (10, 90), (5, 95)):
    res.append(rep(f"D: levels {lo_l}/{hi_l}", sim(T50s, lo_lvl=lo_l, hi_lvl=hi_l)))

print("\n### E) TP width ###")
for tpr in (2.0, 3.0, 4.0, 5.0):
    res.append(rep(f"E: TP{tpr}R", sim(T50s, tp_r=tpr)))

print("\n### F) session (UTC) ###")
for s in ((7, 20), (12, 21), (13, 22)):
    res.append(rep(f"F: session {s[0]}-{s[1]}", sim(T50s, sess=s)))

good = [x for x in res if x]
print("\n=== robust configs ranked by (per-year consistency, then OOS) ===")
for tag, opf, mc, avgR, mR, gr in sorted(good, key=lambda z: (-z[5], -z[1]))[:8]:
    print(f"  {tag:30s} OOS={opf:.2f} MC5={mc:.2f} avgR={avgR:+.2f} moR={mR:+.1f} green={gr}/6")
print("\nNOTE: accept a filter only if it lifts BOTH OOS and MC5 AND keeps >=5/6 green years.")
print("DONE")
