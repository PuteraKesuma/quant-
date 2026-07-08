"""WMT book walk-forward, last month of data (~2026-05-25..06-25), at WMT SIZING + RULES:
lots 0.02-0.04, XAUUSDx/NQ100x $-per-point, the $90 per-trade risk cap (skip trades whose stop is too
wide), and the daily -$500 stop. From the funded balance $9,691. Shows the per-trade path + summary,
so we see how the deployed WMT config would have traded that month. Run: python research/wmt_lastmonth.py

Honest note: still IN-SAMPLE data; the WMT demo run IS the real forward test.
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from zrev_dual_trend import daily_map
import portfolio_best as pb           # pb.NAS ($ per ORB trade @0.01 US100 = 0.1 NQ100x)

START = 9691.53
CAP = 90.0                            # $ per-trade risk cap (WMT)
DAILY_STOP = 500.0
GOLD_CONTRACT, NQ_CONTRACT = 100.0, 1.0
COSTg = 0.30                          # gold price-unit round trip

M1 = load_m1("XAUUSD")
h1 = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
def atrw(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()
def adxf(h, n=14):
    up = h["high"].diff(); dn = -h["low"].diff()
    p = np.where((up > dn) & (up > 0), up, 0.0); m = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(p, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(m, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()

trades = []   # (exit_ts, strat, usd)

# ---- Z: dual-filter S&R + 3xATR stop, momentum lot 0.02/0.04, $90 cap SKIP ----
N, ema_n, mult = 20, 100, 3.0
O = h1["open"].values; Hi = h1["high"].values; Lo = h1["low"].values; C = h1["close"].values
up = h1["high"].rolling(N).max().shift(1).values; lo = h1["low"].rolling(N).min().shift(1).values
ema = h1["close"].ewm(span=ema_n, adjust=False).mean(); h1up = (h1["close"] > ema).shift(1).values
atr = atrw(h1).shift(1).values
ma20 = h1["close"].rolling(20).mean(); sd20 = h1["close"].rolling(20).std()
dmap = daily_map(50); dates = h1.index.date; idx = h1.index
pos = 0; ep = astop = 0.0; lotz = 0.02
for i in range(len(h1)):
    if any(np.isnan(x) for x in (up[i], lo[i], atr[i])) or (isinstance(h1up[i], float) and np.isnan(h1up[i])):
        continue
    dt = dmap.get(dates[i], 0)
    cl = bool(h1up[i]) and dt == 1; cs = (not bool(h1up[i])) and dt == -1
    if pos == 0:
        sig = 1 if (Hi[i] >= up[i] and cl) else (-1 if (Lo[i] <= lo[i] and cs) else 0)
        if sig == 0:
            continue
        ep0 = max(O[i], up[i]) if sig == 1 else min(O[i], lo[i])
        risk_price = mult * atr[i]
        s = float(sd20.iloc[i]) if np.isfinite(sd20.iloc[i]) and sd20.iloc[i] > 0 else 1
        zdir = ((ep0 - float(ma20.iloc[i])) / s) * sig
        lotz = 0.04 if zdir >= 1 else 0.02
        if risk_price * lotz * GOLD_CONTRACT > CAP:            # $90 cap -> skip (Z whipsaws with wide stops)
            continue
        pos = sig; ep = ep0; astop = ep - sig * mult * atr[i]
    else:
        if pos == 1:
            stop = max(astop, lo[i])
            if Lo[i] <= stop:
                trades.append((idx[i], "Z", ((min(O[i], stop) - ep) - COSTg) * lotz * GOLD_CONTRACT)); pos = 0
        else:
            stop = min(astop, up[i])
            if Hi[i] >= stop:
                trades.append((idx[i], "Z", ((ep - max(O[i], stop)) - COSTg) * lotz * GOLD_CONTRACT)); pos = 0

# ---- Golden: M5 fade EMA15 + skip ADX>40, lot 0.02/0.04, $90 cap ----
c = m5["close"]; NORM = 100
ms = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mn, mx = ms.rolling(NORM).min(), ms.rolling(NORM).max()
mnorm = np.nan_to_num(((ms - mn) / (mx - mn).replace(0, np.nan) * 100).values, nan=50)
pmn, pmx = c.rolling(NORM).min(), c.rolling(NORM).max()
pnorm = np.nan_to_num(((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).values, nan=50)
atr5 = atrw(m5).shift(1).values; o5 = m5["open"].values; hi5 = m5["high"].values; lo5 = m5["low"].values; idx5 = m5.index
t15 = np.sign(h1["close"].ewm(span=15, adjust=False).mean().diff()).reindex(idx5, method="ffill").fillna(0).values
ADXg = adxf(h1).reindex(idx5, method="ffill").fillna(0).values
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80)
pos = 0; entry = sl = tp = 0.0; lotG = 0.02
for i in range(1, len(m5)):
    if pos == 0:
        if not np.isfinite(atr5[i]) or atr5[i] <= 0 or ADXg[i - 1] > 40:
            continue
        sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
        if sig == 0 or not ((sig == 1 and t15[i - 1] > 0) or (sig == -1 and t15[i - 1] < 0)):
            continue
        lotG = 0.04 if ADXg[i - 1] < 20 else 0.02
        risk_price = 3 * atr5[i]
        if risk_price * lotG * GOLD_CONTRACT > CAP:
            continue
        entry = o5[i]; pos = sig; sl = entry - sig * 3 * atr5[i]; tp = entry + sig * 9 * atr5[i]
    else:
        ex = (sl if lo5[i] <= sl else (tp if hi5[i] >= tp else None)) if pos == 1 else \
             (sl if hi5[i] >= sl else (tp if lo5[i] <= tp else None))
        if ex is not None:
            trades.append((idx5[i], "Golden", (pos * (ex - entry) - COSTg) * lotG * GOLD_CONTRACT)); pos = 0

# ---- ORB: NQ100x 0.2 lot = 2x demo (0.1 NQ = demo 0.01 US100); risk stays <$90 ----
for ts, usd01 in pb.NAS.items():
    trades.append((ts, "ORB", float(usd01) * 2.0))            # 0.2 NQ100x

df = pd.DataFrame(trades, columns=["ts", "strat", "usd"]).sort_values("ts")
last = df.ts.max(); W0 = last - pd.Timedelta(days=31)
mo = df[(df.ts >= W0) & (df.ts <= last)].copy().reset_index(drop=True)

# apply daily -$500 governor stop
print(f"=== WMT book — last month of data ({W0.date()}..{last.date()}), start ${START:.0f} ===")
print(f"WMT sizing: Z 0.02/0.04, Golden 0.02/0.04, ORB 0.2 NQ; $90/trade cap; daily -$500 stop\n")
print(f"{'#':>2} {'exit (UTC)':16} {'strat':7} {'P&L$':>8} {'balance':>9}")
bal = START; peak = START; maxdd = 0.0; per = {"Z": 0.0, "Golden": 0.0, "ORB": 0.0}
day_pnl = {}; skipped_daily = 0
for k, r in mo.iterrows():
    d = r.ts.date()
    if day_pnl.get(d, 0.0) <= -DAILY_STOP:                    # daily stop already tripped -> skip
        skipped_daily += 1; continue
    bal += r.usd; peak = max(peak, bal); maxdd = min(maxdd, bal - peak)
    per[r.strat] += r.usd; day_pnl[d] = day_pnl.get(d, 0.0) + r.usd
    if abs(r.usd) >= 30 or k < 3:
        print(f"{k+1:>2} {r.ts.strftime('%m-%d %H:%M'):16} {r.strat:7} {r.usd:+8.2f} {bal:9.2f}")
print(f"\n   ... ({len(mo)} trades total; showing the big ones)")
tot = bal - START; wins = (mo.usd > 0).sum(); loss = (mo.usd < 0).sum()
print(f"\n--- per strategy: Z ${per['Z']:+.0f}  Golden ${per['Golden']:+.0f}  ORB ${per['ORB']:+.0f}")
print(f"Trades: {len(mo)}  WR {100*wins/max(1,len(mo)):.0f}%  Net ${tot:+.2f}  End ${bal:.2f} ({100*tot/START:+.1f}%)")
print(f"Max intramonth DD ${maxdd:.2f} ({100*maxdd/START:+.1f}%)  |  daily-stop skipped {skipped_daily} trades")
print(f"vs WMT limits: max-loss floor $9000 (buffer $691), daily $500")
print("DONE")
