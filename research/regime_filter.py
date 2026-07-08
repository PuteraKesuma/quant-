"""SIDEWAYS DETECTION + MITIGATION — can we identify range regimes and does turning trading OFF
during them help? Rule-based (backtestable) regime = ADX(14) on H1 XAU (ADX<20 range, >25 trend).
For Z (trend-ride) and Golden (fade-with-trend), tag every trade by ADX-at-entry, show expectancy
per regime bin, then TEST skipping entries when ADX < threshold. Report net/PF/maxDD/OOS filtered
vs baseline. Run: python research/regime_filter.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split
from zrev_dual_trend import sim_dual, daily_map

M1 = load_m1("XAUUSD")
h1 = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])

def adx(h, n=14):
    up = h["high"].diff(); dn = -h["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()

ADX = adx(h1)
adx_at = ADX.reindex(M1.resample("5min").agg({"close": "last"}).index, method="ffill")   # M5 lookup
adx_h1 = ADX   # H1 lookup for Z (hourly entries)

# fraction of time ranging
print(f"### regime split (H1 ADX14): range(<20)={100*(ADX<20).mean():.0f}%  "
      f"transition(20-25)={100*((ADX>=20)&(ADX<25)).mean():.0f}%  trend(>25)={100*(ADX>=25).mean():.0f}%\n")

def analyze(name, trades, adx_lookup, is_dollar=True):
    # trades: list of (entry_ts, exit_ts, pnl)
    rows = []
    for e, x, p in trades:
        a = adx_lookup.asof(e)
        rows.append((e, x, p, a))
    df = pd.DataFrame(rows, columns=["e", "x", "pnl", "adx"]).dropna()
    print(f"--- {name}: expectancy by ADX-at-entry bin ---")
    bins = [(0, 20, "range   <20"), (20, 25, "transit 20-25"), (25, 40, "trend   25-40"), (40, 999, "strong   >40")]
    for lo, hi, lbl in bins:
        g = df[(df.adx >= lo) & (df.adx < hi)]
        if len(g) == 0:
            continue
        pf = g[g.pnl > 0].pnl.sum() / max(1e-9, -g[g.pnl < 0].pnl.sum())
        print(f"    {lbl:14s} n={len(g):4d}  avg=${g.pnl.mean():+6.2f}  net=${g.pnl.sum():+7.0f}  PF={pf:4.2f}  WR={100*(g.pnl>0).mean():3.0f}%")
    print(f"  --- filter test (skip entries with ADX < thr) ---")
    base_net = df.pnl.sum(); base_dd = float((df.sort_values('x').pnl.cumsum() - df.sort_values('x').pnl.cumsum().cummax()).min())
    print(f"    baseline (no filter)  n={len(df):4d} net=${base_net:+7.0f} maxDD=${base_dd:+6.0f} PF={stats(list(df.pnl))['pf']:.2f}")
    for thr in (15, 20, 25):
        g = df[df.adx >= thr].sort_values('x')
        if len(g) < 40:
            continue
        dd = float((g.pnl.cumsum() - g.pnl.cumsum().cummax()).min())
        i_, o = split(list(zip(g.x, g.pnl)))
        print(f"    ADX>={thr}              n={len(g):4d} net=${g.pnl.sum():+7.0f} maxDD=${dd:+6.0f} "
              f"PF={stats(list(g.pnl))['pf']:.2f} OOS={stats(o)['pf']:.2f}")
    print()

# Z trades (entry ts = t[0], exit = t[1], pnl = t[3])
z = sim_dual(dmap=daily_map(50), use_daily=True)
analyze("Z (trend-ride XAU 1H)", [(t[0], t[1], t[3]) for t in z], adx_h1)

# Golden trades
COST, NORM = 0.60, 100
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
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
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80)
gtr = []; pos = 0; entry = sl = tp = 0.0; e_ts = None
for i in range(1, len(m5)):
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
            gtr.append((e_ts, idx5[i], pos * (ex - entry) - COST)); pos = 0
analyze("Golden Strategy 1 (fade-with-trend M5)", gtr, adx_at)
print("DONE")
