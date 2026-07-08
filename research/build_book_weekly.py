"""Rebuild research/book_weekly.csv = weekly $ PnL of the DEPLOYED 3-strategy book (Z + ORB + Golden),
so forward_tracker's MC cone reflects the full live book. Golden is REGIME-SIZED (0.02 @ H1-ADX<20
else 0.01, matching live); Z + ORB at 0.01. Run: python research/build_book_weekly.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
import portfolio_best as pb          # pb.Z, pb.NAS : $ @0.01 lot

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

gold = []; pos = 0; entry = sl = tp = 0.0; e_ts = None; a_ent = 0.0
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
            mult = 2.0 if a_ent < 20 else 1.0            # regime-sized (0.02 @ ADX<20)
            gold.append((idx5[i], (pos * (ex - entry) - COST) * mult)); pos = 0
GOLD = pd.Series([p for _, p in gold], index=pd.DatetimeIndex([t for t, _ in gold]))

book = pd.concat([pb.Z, pb.NAS, GOLD]).sort_index()
wk = book.resample("W").sum()
wk = wk[wk.index >= "2021-06-01"]                          # drop sparse warmup weeks
out = pd.DataFrame({"pnl": wk.values}, index=wk.index)
path = r"C:\Quant\research\book_weekly.csv"
out.to_csv(path)
print(f"wrote {path}  weeks={len(out)}  mean=${out.pnl.mean():+.1f}/wk  sd=${out.pnl.std():.1f}  "
      f"positive-weeks={100*(out.pnl>0).mean():.0f}%")
print(f"components: Z n={len(pb.Z)}  NAS n={len(pb.NAS)}  Golden n={len(GOLD)} (regime-sized)")
print("DONE")
