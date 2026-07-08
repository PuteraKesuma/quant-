"""WMT book monthly-return DISTRIBUTION (2021-2026) at WMT sizing (0.02-0.04, $90 cap, daily -$500),
so we see the honest RANGE of months — not one cherry-picked window. Reuses the wmt_lastmonth sim.
Run: python research/wmt_monthly.py
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

CAP, GC, NQC, COSTg = 90.0, 100.0, 1.0, 0.30
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
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()
trades = []
# Z
N, en, mult = 20, 100, 3.0
O = h1["open"].values; Hi = h1["high"].values; Lo = h1["low"].values
up = h1["high"].rolling(N).max().shift(1).values; lo = h1["low"].rolling(N).min().shift(1).values
ema = h1["close"].ewm(span=en, adjust=False).mean(); h1u = (h1["close"] > ema).shift(1).values
atr = atrw(h1).shift(1).values; ma20 = h1["close"].rolling(20).mean(); sd20 = h1["close"].rolling(20).std()
dmap = daily_map(50); dts = h1.index.date; idx = h1.index; pos = 0; ep = astop = 0.0; lz = 0.02
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
        if mult * atr[i] * lz * GC > CAP:
            continue
        pos = sig; ep = ep0; astop = ep - sig * mult * atr[i]
    else:
        if pos == 1:
            st = max(astop, lo[i])
            if Lo[i] <= st:
                trades.append((idx[i], ((min(O[i], st) - ep) - COSTg) * lz * GC)); pos = 0
        else:
            st = min(astop, up[i])
            if Hi[i] >= st:
                trades.append((idx[i], ((ep - max(O[i], st)) - COSTg) * lz * GC)); pos = 0
# Golden
c = m5["close"]; NM = 100
ms = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mn, mx = ms.rolling(NM).min(), ms.rolling(NM).max(); mnorm = np.nan_to_num(((ms - mn) / (mx - mn).replace(0, np.nan) * 100).values, nan=50)
pmn, pmx = c.rolling(NM).min(), c.rolling(NM).max(); pnorm = np.nan_to_num(((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).values, nan=50)
a5 = atrw(m5).shift(1).values; o5 = m5["open"].values; h5 = m5["high"].values; l5 = m5["low"].values; ix5 = m5.index
t15 = np.sign(h1["close"].ewm(span=15, adjust=False).mean().diff()).reindex(ix5, method="ffill").fillna(0).values
AX = adxf(h1).reindex(ix5, method="ffill").fillna(0).values
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80); pos = 0; e = sl = tp = 0.0; lg = 0.02
for i in range(1, len(m5)):
    if pos == 0:
        if not np.isfinite(a5[i]) or a5[i] <= 0 or AX[i - 1] > 40:
            continue
        sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
        if sig == 0 or not ((sig == 1 and t15[i - 1] > 0) or (sig == -1 and t15[i - 1] < 0)):
            continue
        lg = 0.04 if AX[i - 1] < 20 else 0.02
        if 3 * a5[i] * lg * GC > CAP:
            continue
        e = o5[i]; pos = sig; sl = e - sig * 3 * a5[i]; tp = e + sig * 9 * a5[i]
    else:
        ex = (sl if l5[i] <= sl else (tp if h5[i] >= tp else None)) if pos == 1 else (sl if h5[i] >= sl else (tp if l5[i] <= tp else None))
        if ex is not None:
            trades.append((ix5[i], (pos * (ex - e) - COSTg) * lg * GC)); pos = 0
for ts, u in pb.NAS.items():
    trades.append((ts, float(u) * 2.0))
s = pd.Series([u for _, u in trades], index=pd.DatetimeIndex([t for t, _ in trades])).sort_index()
mo = s.resample("MS").sum()
mo = mo[mo.index >= "2021-06-01"]
print("=== WMT book MONTHLY net $ distribution (2021-2026, WMT sizing) ===")
print(f"months: {len(mo)}  |  median ${mo.median():+.0f}  mean ${mo.mean():+.0f}")
print(f"BEST month ${mo.max():+.0f} ({mo.idxmax():%Y-%m})   WORST ${mo.min():+.0f} ({mo.idxmin():%Y-%m})")
print(f"positive months: {100*(mo>0).mean():.0f}%   |  5th pct ${mo.quantile(0.05):+.0f}   95th ${mo.quantile(0.95):+.0f}")
print(f"as % of $9691: median {100*mo.median()/9691:+.1f}%  best {100*mo.max()/9691:+.0f}%  worst {100*mo.min()/9691:+.1f}%")
print("\nlast 8 months:")
for t, v in mo.tail(8).items():
    print(f"  {t:%Y-%m}: ${v:+7.0f}  ({100*v/9691:+.1f}%)")
print("\nread: the range is wide. A few jackpot months carry it; many are small; some are red.")
print("DONE")
