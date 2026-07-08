"""WMT risk-cap sweep — the one honest tunable (risk/return dial, not curve-fit). For cap in
{90,120,150,200}: how much of Z's edge is unlocked, and what does it cost in drawdown? Golden is
~cap-insensitive (tight ATR stops), so run it once; Z is what the cap gates. Reports each cap's
monthly median/worst/%-positive + equity max-DD + Z net, vs the $691 WMT buffer.
Run: python research/wmt_cap_sweep.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from zrev_dual_trend import daily_map
import portfolio_best as pb

GC, COSTg, BUFFER = 100.0, 0.30, 691.0
M1 = load_m1("XAUUSD")
h1 = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
def atrw(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(), (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()
def adxf(h, n=14):
    up = h["high"].diff(); dn = -h["low"].diff()
    p = np.where((up > dn) & (up > 0), up, 0.0); m = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(), (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(p, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(m, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    return (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).ewm(alpha=1 / n, adjust=False).mean()

# ---- Golden once (cap-insensitive: tight stops) at cap 200 (near-none) ----
c = m5["close"]; NM = 100
ms = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mn, mx = ms.rolling(NM).min(), ms.rolling(NM).max(); mnorm = np.nan_to_num(((ms - mn) / (mx - mn).replace(0, np.nan) * 100).values, nan=50)
pmn, pmx = c.rolling(NM).min(), c.rolling(NM).max(); pnorm = np.nan_to_num(((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).values, nan=50)
a5 = atrw(m5).shift(1).values; o5 = m5["open"].values; h5 = m5["high"].values; l5 = m5["low"].values; ix5 = m5.index
t15 = np.sign(h1["close"].ewm(span=15, adjust=False).mean().diff()).reindex(ix5, method="ffill").fillna(0).values
AX = adxf(h1).reindex(ix5, method="ffill").fillna(0).values
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80); pos = 0; e = sl = tp = 0.0; lg = 0.02
golden = []
for i in range(1, len(m5)):
    if pos == 0:
        if not np.isfinite(a5[i]) or a5[i] <= 0 or AX[i - 1] > 40:
            continue
        sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
        if sig == 0 or not ((sig == 1 and t15[i - 1] > 0) or (sig == -1 and t15[i - 1] < 0)):
            continue
        lg = 0.04 if AX[i - 1] < 20 else 0.02
        if 3 * a5[i] * lg * GC > 200:
            continue
        e = o5[i]; pos = sig; sl = e - sig * 3 * a5[i]; tp = e + sig * 9 * a5[i]
    else:
        ex = (sl if l5[i] <= sl else (tp if h5[i] >= tp else None)) if pos == 1 else (sl if h5[i] >= sl else (tp if l5[i] <= tp else None))
        if ex is not None:
            golden.append((ix5[i], (pos * (ex - e) - COSTg) * lg * GC)); pos = 0
GOLD = pd.Series([u for _, u in golden], index=pd.DatetimeIndex([t for t, _ in golden]))
ORB = pd.Series([float(u) * 2.0 for u in pb.NAS.values], index=pb.NAS.index)

# ---- Z per cap ----
N, en, mult = 20, 100, 3.0
O = h1["open"].values; Hi = h1["high"].values; Lo = h1["low"].values
up = h1["high"].rolling(N).max().shift(1).values; lo = h1["low"].rolling(N).min().shift(1).values
ema = h1["close"].ewm(span=en, adjust=False).mean(); h1u = (h1["close"] > ema).shift(1).values
atr = atrw(h1).shift(1).values; ma20 = h1["close"].rolling(20).mean(); sd20 = h1["close"].rolling(20).std()
dmap = daily_map(50); dts = h1.index.date; idx = h1.index

def z_trades(cap):
    tr = []; pos = 0; ep = astop = 0.0; lz = 0.02
    for i in range(len(h1)):
        if any(np.isnan(x) for x in (up[i], lo[i], atr[i])) or (isinstance(h1u[i], float) and np.isnan(h1u[i])):
            continue
        dt = dmap.get(dts[i], 0); cl = bool(h1u[i]) and dt == 1; cs = (not bool(h1u[i])) and dt == -1
        if pos == 0:
            sig = 1 if (Hi[i] >= up[i] and cl) else (-1 if (Lo[i] <= lo[i] and cs) else 0)
            if sig == 0:
                continue
            ep0 = max(O[i], up[i]) if sig == 1 else min(O[i], lo[i]); s = float(sd20.iloc[i]) if np.isfinite(sd20.iloc[i]) and sd20.iloc[i] > 0 else 1
            lz = 0.04 if ((ep0 - float(ma20.iloc[i])) / s) * sig >= 1 else 0.02
            if mult * atr[i] * lz * GC > cap:
                continue
            pos = sig; ep = ep0; astop = ep - sig * mult * atr[i]
        else:
            if pos == 1:
                st = max(astop, lo[i])
                if Lo[i] <= st:
                    tr.append((idx[i], ((min(O[i], st) - ep) - COSTg) * lz * GC)); pos = 0
            else:
                st = min(astop, up[i])
                if Hi[i] >= st:
                    tr.append((idx[i], ((ep - max(O[i], st)) - COSTg) * lz * GC)); pos = 0
    return pd.Series([u for _, u in tr], index=pd.DatetimeIndex([t for t, _ in tr]))

print("=== WMT risk-cap sweep — monthly distribution + drawdown per cap ===")
print(f"(Golden n={len(GOLD)} fixed; ORB n={len(ORB)} fixed; Z varies with cap. buffer ${BUFFER:.0f})\n")
print(f"  {'cap':>4} {'Ztrades':>7} {'Znet':>7} {'med/mo':>7} {'worst-mo':>8} {'%pos':>5} {'equity maxDD':>12}")
for cap in (90, 120, 150, 200):
    Z = z_trades(cap)
    book = pd.concat([Z, GOLD, ORB]).sort_index()
    book = book[book.index >= "2021-06-01"]
    mo = book.resample("MS").sum()
    eq = book.cumsum(); dd = float((eq - eq.cummax()).min())
    print(f"  {cap:>4} {len(Z):>7} {Z.sum():>+7.0f} {mo.median():>+7.0f} {mo.min():>+8.0f} "
          f"{100*(mo>0).mean():>4.0f}% {dd:>+12.0f}")
print("\nread: higher cap -> more Z trades + higher median, but deeper worst-month/drawdown.")
print("pick the cap whose WORST drawdown stays comfortably inside the $691 buffer.")
print("DONE")
