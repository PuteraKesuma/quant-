"""Correlation of the new Semi-Marti fade-WITH-trend (EMA15, M5) vs the book components (Z, NAS-ORB)
and the swing-XAU candidate. Monthly-PnL Pearson corr (scale-invariant). Low corr to Z (<~0.3) =
genuine diversifier (complement); high (>~0.5) = stacks gold risk (redundant, like LIQ was).
Run: python research/semi_marti_corr.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1

print("=== building Z / NAS / LIQ (portfolio_best) ===", flush=True)
import portfolio_best as pb   # Z, NAS, LIQ: $ per trade, indexed by exit ts
Z, NAS = pb.Z, pb.NAS

COST, NORM = 0.60, 100
M1 = load_m1("XAUUSD")

# ---- Semi-Marti EMA15 fade-with-trend (M5) -> R per trade ----
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
c = m5["close"]
macd_sig = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mn, mx = macd_sig.rolling(NORM).min(), macd_sig.rolling(NORM).max()
mnorm = np.nan_to_num(((macd_sig - mn) / (mx - mn).replace(0, np.nan) * 100).values, nan=50)
pmn, pmx = c.rolling(NORM).min(), c.rolling(NORM).max()
pnorm = np.nan_to_num(((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).values, nan=50)

def atrw(h, nn=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / nn, adjust=False).mean()
atr5 = atrw(m5).shift(1).values
o5 = m5["open"].values; hi5 = m5["high"].values; lo5 = m5["low"].values; idx5 = m5.index; n5 = len(m5)
h1 = M1.resample("1h").agg({"close": "last"}).dropna()
t15 = np.sign(h1["close"].ewm(span=15, adjust=False).mean().diff()).reindex(idx5, method="ffill").fillna(0).values
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80)
sm = []; pos = 0; entry = sl = tp = 0.0; e_ts = None
for i in range(1, n5):
    if pos == 0:
        if not np.isfinite(atr5[i]) or atr5[i] <= 0:
            continue
        sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
        if sig == 0:
            continue
        if not ((sig == 1 and t15[i - 1] > 0) or (sig == -1 and t15[i - 1] < 0)):
            continue
        entry = o5[i]; pos = sig; e_ts = idx5[i]; sl = entry - sig * 3 * atr5[i]; tp = entry + sig * 9 * atr5[i]
    else:
        if pos == 1:
            ex = sl if lo5[i] <= sl else (tp if hi5[i] >= tp else None)
        else:
            ex = sl if hi5[i] >= sl else (tp if lo5[i] <= tp else None)
        if ex is not None:
            sm.append((idx5[i], pos * (ex - entry) - COST)); pos = 0
SM = pd.Series([p for _, p in sm], index=pd.DatetimeIndex([t for t, _ in sm])).sort_index()

# ---- Swing-XAU 1D Donchian10 ATR2 TP3R +EMA50 -> R per trade ----
d1 = M1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
up = d1["high"].rolling(10).max().shift(1).values; dn = d1["low"].rolling(10).min().shift(1).values
atrd = atrw(d1).shift(1).values; ema50 = d1["close"].ewm(span=50, adjust=False).mean().shift(1).values
od = d1["open"].values; hid = d1["high"].values; lod = d1["low"].values; cd = d1["close"].values; idd = d1.index
sw = []; pos = 0; entry = sl = tp = 0.0; e_ts = None
for i in range(len(d1)):
    if np.isnan(up[i]) or np.isnan(atrd[i]) or atrd[i] <= 0:
        continue
    if pos == 0:
        if hid[i] >= up[i] and cd[i - 1] > ema50[i]:
            entry = max(od[i], up[i]); sl = entry - 2 * atrd[i]; tp = entry + 3 * 2 * atrd[i]; pos = 1; e_ts = idd[i]
        elif lod[i] <= dn[i] and cd[i - 1] < ema50[i]:
            entry = min(od[i], dn[i]); sl = entry + 2 * atrd[i]; tp = entry - 3 * 2 * atrd[i]; pos = -1; e_ts = idd[i]
    else:
        risk = abs(entry - sl)
        if pos == 1:
            ex = sl if lod[i] <= sl else (tp if hid[i] >= tp else None)
        else:
            ex = sl if hid[i] >= sl else (tp if lod[i] <= tp else None)
        if ex is not None:
            sw.append((idd[i], (pos * (ex - entry) - COST) / risk)); pos = 0
SW = pd.Series([p for _, p in sw], index=pd.DatetimeIndex([t for t, _ in sw])).sort_index()

# ---- monthly correlation ----
M = pd.DataFrame({
    "Z_xau": Z.resample("MS").sum(),
    "NAS_orb": NAS.resample("MS").sum(),
    "SemiMarti_xau": SM.resample("MS").sum(),
    "Swing_xau": SW.resample("MS").sum(),
}).dropna(how="all").fillna(0.0)

print(f"\nSemi-Marti EMA15: {len(SM)} trades, monthly n={ (SM.resample('MS').sum()!=0).sum() }")
print("\n=== MONTHLY-PnL CORRELATION ===")
print(M.corr().round(2).to_string())
print("\n--- key reads ---")
cz = M.corr().loc["SemiMarti_xau", "Z_xau"]
cn = M.corr().loc["SemiMarti_xau", "NAS_orb"]
csw = M.corr().loc["SemiMarti_xau", "Swing_xau"]
print(f"  SemiMarti vs Z(xau) : {cz:+.2f}  ({'DIVERSIFIER (complement)' if cz < 0.3 else 'stacks gold (redundant)' if cz > 0.5 else 'mild overlap'})")
print(f"  SemiMarti vs NAS    : {cn:+.2f}")
print(f"  SemiMarti vs Swing  : {csw:+.2f}  (both XAU)")
print("\nDONE")
