"""WALK-FORWARD Jan 2026 -> end-of-data (2026-06-25): WHERE and WHY the book (Z + ORB + Golden)
loses. Lists losing days, the worst drawdown stretch, and a structural REASON for each loss
(Z = S&R whipsaw / channel-break reversed; Golden = fade run over by a strengthening move;
ORB = false breakout / session-end), with the H1-ADX context at entry.
Run: python research/loss_forensics_2026.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from zrev_dual_trend import sim_dual, daily_map
import portfolio_best as pb

START = 400.0
Y0, Y1 = pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC")
COST, NORM = 0.60, 100
M1 = load_m1("XAUUSD")
h1 = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])

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
ADX = adx(h1)

rows = []   # (exit_ts, strat, dir, pnl, reason)

# Z
for e, x, d, p in sim_dual(dmap=daily_map(50), use_daily=True):
    a = ADX.asof(e)
    if p < 0:
        rea = f"S&R whipsaw: {d} entry reversed (channel break failed); ADX@entry {a:.0f}" + (" (chop)" if a < 20 else "")
    else:
        rea = f"{d} trend rode ok"
    rows.append((x, "Z", d, p, rea))

# Golden (EMA15 fade-with-trend + skip ADX>40)
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
ADXm5 = ADX.reindex(idx5, method="ffill").fillna(0).values
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80)
pos = 0; entry = sl = tp = 0.0; e_ts = None; a_ent = 0.0
for i in range(1, len(m5)):
    if pos == 0:
        if not np.isfinite(atr5[i]) or atr5[i] <= 0 or ADXm5[i - 1] > 40:
            continue
        sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
        if sig == 0 or not ((sig == 1 and t15[i - 1] > 0) or (sig == -1 and t15[i - 1] < 0)):
            continue
        entry = o5[i]; pos = sig; e_ts = idx5[i]; a_ent = ADXm5[i - 1]
        sl = entry - sig * 3 * atr5[i]; tp = entry + sig * 9 * atr5[i]
    else:
        if pos == 1:
            ex = sl if lo5[i] <= sl else (tp if hi5[i] >= tp else None)
        else:
            ex = sl if hi5[i] >= sl else (tp if lo5[i] <= tp else None)
        if ex is not None:
            p = pos * (ex - entry) - COST
            d = "long" if pos == 1 else "short"
            rea = (f"fade run over: {d} dip/bounce kept going (SL); ADX@entry {a_ent:.0f}"
                   if p < 0 else f"{d} reverted to TP")
            rows.append((idx5[i], "Golden", d, p, rea)); pos = 0

# ORB (only $ + exit ts available)
for x, p in pb.NAS.items():
    rows.append((x, "ORB", "-", float(p), "false breakout / SL or session-end" if p < 0 else "breakout rode ok"))

df = pd.DataFrame(rows, columns=["ts", "strat", "dir", "pnl", "reason"])
df = df[(df.ts >= Y0) & (df.ts < Y1)].sort_values("ts").reset_index(drop=True)
df["bal"] = START + df.pnl.cumsum()
df["peak"] = df.bal.cummax(); df["dd"] = df.bal - df.peak

print(f"=== WALK-FORWARD Jan-Jun 2026 (book Z+ORB+Golden, $400 start, 0.01 lot) ===")
print(f"trades={len(df)}  net=${df.pnl.sum():+.0f}  end=${df.bal.iloc[-1]:.0f}  maxDD=${df.dd.min():+.0f}\n")

print("--- monthly net $ ---")
for (mo,), g in df.groupby([df.ts.dt.to_period('M')]):
    per = g.groupby('strat').pnl.sum()
    print(f"  {mo}: total ${g.pnl.sum():+6.0f}  (Z ${per.get('Z',0):+.0f} / ORB ${per.get('ORB',0):+.0f} / Golden ${per.get('Golden',0):+.0f})  trades={len(g)}")

print("\n--- worst 12 LOSING trades (date, strat, $, reason) ---")
for _, r in df.nsmallest(12, "pnl").sort_values("ts").iterrows():
    print(f"  {r.ts.strftime('%m-%d %H:%M')} {r.strat:6} {r.pnl:+7.2f}  {r.reason}")

print("\n--- worst LOSING DAYS (net) ---")
day = df.groupby(df.ts.dt.date).agg(net=("pnl", "sum"), n=("pnl", "size"))
for d, r in day[day.net < 0].nsmallest(8, "net").iterrows():
    bys = df[df.ts.dt.date == d].groupby('strat').pnl.sum()
    print(f"  {d}  net ${r.net:+.0f}  ({r.n} trades)  " + " ".join(f"{k}:{v:+.0f}" for k, v in bys.items()))

print("\n--- deepest drawdown stretch ---")
trough = df.dd.idxmin()
pk = df.loc[:trough][df.loc[:trough].bal == df.loc[:trough].peak].iloc[-1]
print(f"  peak ${pk.bal:.0f} on {pk.ts.date()} -> trough ${df.loc[trough].bal:.0f} on {df.loc[trough].ts.date()} "
      f"= ${df.dd.min():+.0f} ({100*df.dd.min()/pk.bal:+.0f}%)")

print("\n--- per-strategy loss summary ---")
for s, g in df.groupby("strat"):
    los = g[g.pnl < 0]
    print(f"  {s:6}: {len(los)}L/{len(g)} losses, total loss ${los.pnl.sum():+.0f}, worst ${g.pnl.min():+.0f}")
print("DONE")
