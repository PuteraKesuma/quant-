"""JULY 2026 FORECAST (checkable). The book (Z + ORB + Golden regime-sized) net for July, as a
DISTRIBUTION (returns aren't point-predictable). Two views: (A) what July-1 would have forecast for
the full month, (B) updated from TODAY given month-to-date PnL. A forecast 'works' if the actual
end-of-July net lands inside the 5-95% band. Run: python research/july_forecast.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
import portfolio_best as pb

COST, NORM = 0.60, 100
JULY1_BAL = 303.0                 # demo balance at the start of July (before July's losses)
TARGET = 122.2                    # governor month target (mean weekly x weeks)
TRIGGER = 91.65                   # 75% stop
N = 20000

# ---- daily book PnL (Z + NAS + Golden regime-sized) ----
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
            gold.append((idx5[i], (pos * (ex - entry) - COST) * (2.0 if a_ent < 20 else 1.0))); pos = 0
GOLD = pd.Series([p for _, p in gold], index=pd.DatetimeIndex([t for t, _ in gold]))
book = pd.concat([pb.Z, pb.NAS, GOLD]).sort_index()
daily = book.resample("D").sum()
daily = daily[daily.index.dayofweek < 5]
D = daily.values

# ---- trading days in July ----
jul = pd.bdate_range("2026-07-01", "2026-07-31")
now = pd.Timestamp.now().normalize().tz_localize(None)
jul = jul.tz_localize(None)
elapsed = int((jul <= now).sum()); total = len(jul); remaining = total - elapsed
rng = np.random.default_rng(7)

def sim_net(days):
    return np.cumsum(rng.choice(D, size=(N, max(days, 1)), replace=True), axis=1)[:, -1]

print("=== JULY 2026 FORECAST — book Z+ORB+Golden (checkable) ===")
print(f"July trading days: {total}  elapsed: {elapsed}  remaining: {remaining}")
print(f"governor target ${TARGET:.0f}  (75% stop ${TRIGGER:.0f})\n")

full = sim_net(total)
p5, p50, p95 = np.percentile(full, [5, 50, 95])
print("A) FULL-JULY forecast (as of July 1):")
print(f"   net: median ${p50:+.0f}   5-95% band  ${p5:+.0f} .. ${p95:+.0f}")
print(f"   P(month >0) = {100*(full>0).mean():.0f}%   P(hit ${TARGET:.0f} target) = {100*(full>=TARGET).mean():.0f}%")

MTD = -103.2
end = MTD + sim_net(remaining)
e5, e50, e95 = np.percentile(end, [5, 50, 95])
print(f"\nB) UPDATED forecast from TODAY (month-to-date ${MTD:+.0f}, {remaining} days left):")
print(f"   end-July net: median ${e50:+.0f}   5-95% band  ${e5:+.0f} .. ${e95:+.0f}")
print(f"   P(end >0) = {100*(end>0).mean():.0f}%   P(recover to hit ${TARGET:.0f}) = {100*(end>=TARGET).mean():.0f}%")
print(f"   equity now ~${JULY1_BAL+MTD:.0f}; end-July equity band ~${JULY1_BAL+e5:.0f} .. ${JULY1_BAL+e95:.0f}")
print(f"\nHOW TO CHECK: on Aug 1, compare July's ACTUAL net to band A (${p5:+.0f}..${p95:+.0f}).")
print("Inside the band = the model is calibrated (works). The median is NOT a target; months vary.")
print("DONE")
