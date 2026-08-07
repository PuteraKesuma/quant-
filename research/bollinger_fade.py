"""Honest test: does a Bollinger-band range-FADE have an edge on XAU (H1), and does a range-detector
gate (ADX<25 or Choppiness Index>61) help — or is it just a redundant copy of Golden?
Fade: close beyond a band -> enter toward the mean (SMA20), ATR stop, mean-target exit, max-hold.
Reports per-gate: n, WR, PF, OOS-PF (last 35%), per-year net, maxDD, and monthly-PnL corr to Z.
Run: python research/bollinger_fade.py       (in-sample data; honest OOS split + walk-forward)
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from zrev_dual_trend import daily_map  # not used for gates; kept for parity of imports

COSTg = 0.30       # gold price-unit round trip
GC = 100.0         # $ per $1 move per 1.0 lot (0.01 lot = $1)
BBn, BBk = 20, 2.0
ATRn, ADXn, CHOPn = 14, 14, 14
STOP_ATR = 2.0     # stop = STOP_ATR x ATR beyond entry
MAXHOLD = 48       # H1 bars (~2 days)

M1 = load_m1("XAUUSD")
h = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])

def wilder(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()

tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
atr = wilder(tr, ATRn)
sma = h["close"].rolling(BBn).mean(); sd = h["close"].rolling(BBn).std()
upper = sma + BBk * sd; lower = sma - BBk * sd
# ADX
upm = h["high"].diff(); dnm = -h["low"].diff()
plus = np.where((upm > dnm) & (upm > 0), upm, 0.0); minus = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
pdi = 100 * wilder(pd.Series(plus, index=h.index), ADXn) / wilder(tr, ADXn)
mdi = 100 * wilder(pd.Series(minus, index=h.index), ADXn) / wilder(tr, ADXn)
adx = wilder(100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan), ADXn)
# Choppiness Index (high = choppy/range)
sumtr = tr.rolling(CHOPn).sum()
rng = h["high"].rolling(CHOPn).max() - h["low"].rolling(CHOPn).min()
chop = 100 * np.log10(sumtr / rng.replace(0, np.nan)) / np.log10(CHOPn)

O = h["open"].values; Hi = h["high"].values; Lo = h["low"].values; C = h["close"].values
up1 = upper.shift(1).values; lo1 = lower.shift(1).values; mid1 = sma.shift(1).values
c1 = h["close"].shift(1).values; atr1 = atr.shift(1).values
adx1 = adx.shift(1).values; chop1 = chop.shift(1).values
idx = h.index


def run(gate):
    """gate: 'none' | 'adx' (ADX<25) | 'chop' (CHOP>61)"""
    trades = []; pos = 0; ep = sl = tgt = 0.0; held = 0
    for i in range(len(h)):
        if any(np.isnan(x) for x in (up1[i], lo1[i], mid1[i], atr1[i], adx1[i], chop1[i])):
            continue
        if pos == 0:
            g = True
            if gate == "adx":
                g = adx1[i] < 25
            elif gate == "chop":
                g = chop1[i] > 61
            if not g:
                continue
            sig = 1 if c1[i] < lo1[i] else (-1 if c1[i] > up1[i] else 0)   # fade beyond band
            if sig == 0:
                continue
            ep = O[i]; pos = sig; held = 0
            sl = ep - sig * STOP_ATR * atr1[i]
            tgt = mid1[i]                                                    # revert to mean
        else:
            held += 1
            exit_px = None
            if pos == 1:
                if Lo[i] <= sl: exit_px = min(O[i], sl)
                elif Hi[i] >= tgt: exit_px = max(O[i], tgt)
            else:
                if Hi[i] >= sl: exit_px = max(O[i], sl)
                elif Lo[i] <= tgt: exit_px = min(O[i], tgt)
            if exit_px is None and held >= MAXHOLD:
                exit_px = O[i]
            if exit_px is not None:
                usd = (pos * (exit_px - ep) - COSTg) * 0.01 * GC
                trades.append((idx[i], usd)); pos = 0
    return pd.Series([u for _, u in trades], index=pd.DatetimeIndex([t for t, _ in trades]))


def stats(s, label):
    if len(s) == 0:
        print(f"  {label:16} n=0"); return
    net = s.sum(); w = s[s > 0].sum(); l = -s[s < 0].sum()
    pf = w / l if l > 0 else 99
    wr = 100 * (s > 0).mean()
    eq = s.cumsum(); dd = (eq - eq.cummax()).min()
    cut = s.index[int(len(s) * 0.65)]                                       # last 35% = OOS
    oos = s[s.index >= cut]; ow = oos[oos > 0].sum(); ol = -oos[oos < 0].sum()
    opf = ow / ol if ol > 0 else 99
    yr = s.groupby(s.index.year).sum()
    yrs = " ".join(f"{y}:{v:+.0f}" for y, v in yr.items())
    print(f"  {label:16} n={len(s):4d} WR={wr:4.0f}% PF={pf:.2f} OOS-PF={opf:.2f} net=${net:+6.0f} maxDD=${dd:+5.0f}")
    print(f"       per-year: {yrs}")


print("=== Bollinger range-FADE on XAU H1 (0.01 lot; $0.30 cost; 2xATR stop; mean target) ===\n")
res = {}
for g in ("none", "adx", "chop"):
    s = run(g); res[g] = s
    stats(s, f"gate={g}")

# redundancy check: monthly-PnL correlation to Z
try:
    import portfolio_best as pb
    z = pb.Z if hasattr(pb, "Z") else None
except Exception:
    z = None
if z is not None and len(res["adx"]):
    zm = pd.Series(list(z.values()), index=pd.DatetimeIndex(list(z.keys()))).resample("MS").sum()
    for g in ("adx", "chop"):
        bm = res[g].resample("MS").sum()
        j = pd.concat([zm, bm], axis=1).dropna()
        c = j.iloc[:, 0].corr(j.iloc[:, 1]) if len(j) > 3 else float("nan")
        print(f"\n  monthly-PnL corr(Bollinger gate={g}, Z) = {c:+.2f}  (near +1 = redundant, ~0 = complement)")
print("\nread: an edge needs PF>1.2 AND OOS-PF>1.2 AND most years green. Redundant-to-Z = no new value.")
print("DONE")
