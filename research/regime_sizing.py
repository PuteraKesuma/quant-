"""REGIME-BASED SIZING for Golden — walk-forward. Golden's edge is regime-dependent (range/low-ADX
PF 2.79 >> trend). So size UP in the favorable regime (low H1-ADX), stay min otherwise, capped
(broker lot step 0.01; keep <= 0.03 for the $ risk cap). Compare flat vs tilt schemes on net, maxDD,
monthly Sharpe, PF, 6-month walk-forward windows, per-year. Accept only if risk-adjusted (Sharpe/PF/
DD) improves AND every scheme stays >=5/6 green years (else overfit). Run: python research/regime_sizing.py
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

# collect Golden trades: (exit_ts, pnl@0.01, adx_at_entry)
tr = []; pos = 0; entry = sl = tp = 0.0; e_ts = None; a_ent = 0.0
for i in range(1, len(m5)):
    if pos == 0:
        if not np.isfinite(atr5[i]) or atr5[i] <= 0 or ADXv[i - 1] > 40:
            continue
        sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
        if sig == 0 or not ((sig == 1 and t15[i - 1] > 0) or (sig == -1 and t15[i - 1] < 0)):
            continue
        entry = o5[i]; pos = sig; e_ts = idx5[i]; a_ent = ADXv[i - 1]
        sl = entry - sig * 3 * atr5[i]; tp = entry + sig * 9 * atr5[i]
    else:
        ex = (sl if lo5[i] <= sl else (tp if hi5[i] >= tp else None)) if pos == 1 else \
             (sl if hi5[i] >= sl else (tp if lo5[i] <= tp else None))
        if ex is not None:
            tr.append((idx5[i], pos * (ex - entry) - COST, a_ent)); pos = 0

df = pd.DataFrame(tr, columns=["ts", "pnl", "adx"]).set_index("ts").sort_index()

def mult_flat(a): return 1.0
def mult_fav2(a): return 2.0 if a < 20 else 1.0
def mult_fav3(a): return 3.0 if a < 20 else 1.0
def mult_grad(a): return 3.0 if a < 15 else (2.0 if a < 25 else 1.0)

def report(name, fn):
    s = df.pnl * df.adx.map(fn)
    eq = s.cumsum(); dd = float((eq - eq.cummax()).min())
    mo = s.resample("MS").sum(); sharpe = mo.mean() / mo.std() * np.sqrt(12) if mo.std() > 0 else 0
    pf = s[s > 0].sum() / max(1e-9, -s[s < 0].sum())
    _, oos = split(list(zip(df.index, s.values)))
    w6 = s.resample("6MS").sum(); w6 = w6[w6 != 0]
    py = [s[s.index.year == y].sum() for y in range(2021, 2027)]
    gy = sum(1 for v in py if v > 0)
    print(f"  {name:12s} net=${s.sum():+6.0f} maxDD=${dd:+5.0f} PF={pf:4.2f} OOSpf={stats(oos)['pf']:4.2f} "
          f"Sharpe={sharpe:4.2f} WF={int((w6>0).sum())}/{len(w6)} yr+={gy}/6")

print(f"### Golden regime-based sizing (walk-forward) — n={len(df)} trades ###")
print(f"  favorable regime = H1 ADX < 20 (Golden's best bin). base lot 0.01; cap 0.03.\n")
report("flat 0.01", mult_flat)
report("fav<20:0.02", mult_fav2)
report("fav<20:0.03", mult_fav3)
report("graduated", mult_grad)
print("\n  accept a tilt only if net UP, Sharpe UP, maxDD not much worse, WF+yr stay green.")
print("DONE")
