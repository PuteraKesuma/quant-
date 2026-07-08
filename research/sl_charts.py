"""Screenshot the WORST SL-hit trades for Golden (M5) and Z (H1): annotated candlestick charts
around each trade with entry / SL / TP / exit, so we can SEE what the losing setup looked like.
Saves PNGs to _DOC/sl_analysis/. Run: python research/sl_charts.py
"""
import sys
import os
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from zrev_dual_trend import sim_dual, daily_map

OUT = r"C:\Quant\_DOC\sl_analysis"
os.makedirs(OUT, exist_ok=True)
COST, NORM = 0.60, 100


def candles(ax, bars, title):
    x = np.arange(len(bars))
    for i, (_, b) in enumerate(bars.iterrows()):
        up = b["close"] >= b["open"]
        col = "#26a69a" if up else "#ef5350"
        ax.vlines(i, b["low"], b["high"], color=col, lw=0.8)
        lo, hi = (b["open"], b["close"]) if up else (b["close"], b["open"])
        ax.add_patch(Rectangle((i - 0.3, lo), 0.6, max(hi - lo, 1e-6), color=col))
    ax.set_xlim(-1, len(bars)); ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.2)
    return x


def mark(ax, bars, ts, price, txt, color, dy):
    if ts not in bars.index:
        ts = bars.index[bars.index.get_indexer([ts], method="nearest")[0]]
    i = bars.index.get_loc(ts)
    ax.annotate(txt, (i, price), color=color, fontsize=8, fontweight="bold",
                xytext=(i, price + dy), arrowprops=dict(arrowstyle="->", color=color, lw=1.2))


# ================= GOLDEN (M5) =================
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
ADXm5 = adx(h1).reindex(idx5, method="ffill").fillna(0).values
rb = (mnorm <= 15) & (pnorm <= 15); rs = (mnorm >= 80) & (pnorm >= 80)
gt = []; pos = 0; entry = sl = tp = 0.0; e_i = 0; a_ent = 0.0
for i in range(1, len(m5)):
    if pos == 0:
        if not np.isfinite(atr5[i]) or atr5[i] <= 0 or ADXm5[i - 1] > 40:
            continue
        sig = 1 if rb[i - 1] else (-1 if rs[i - 1] else 0)
        if sig == 0 or not ((sig == 1 and t15[i - 1] > 0) or (sig == -1 and t15[i - 1] < 0)):
            continue
        entry = o5[i]; pos = sig; e_i = i; a_ent = ADXm5[i - 1]
        sl = entry - sig * 3 * atr5[i]; tp = entry + sig * 9 * atr5[i]
    else:
        ex = (sl if lo5[i] <= sl else (tp if hi5[i] >= tp else None)) if pos == 1 else \
             (sl if hi5[i] >= sl else (tp if lo5[i] <= tp else None))
        if ex is not None:
            p = pos * (ex - entry) - COST
            gt.append(dict(ei=e_i, xi=i, dir=pos, entry=entry, sl=sl, tp=tp, exit=ex, pnl=p,
                           adx=a_ent, how="SL" if abs(ex - sl) < 1e-9 else "TP")); pos = 0
gdf = pd.DataFrame(gt)
gsl = gdf[(gdf.how == "SL")].nsmallest(3, "pnl")
for k, (_, r) in enumerate(gsl.iterrows(), 1):
    a, b = int(r.ei) - 30, int(r.xi) + 12
    bars = m5.iloc[max(0, a):b]
    fig, ax = plt.subplots(figsize=(11, 5))
    candles(ax, bars, f"GOLDEN SL #{k}  {idx5[int(r.ei)]:%Y-%m-%d %H:%M} UTC  {'BUY' if r.dir==1 else 'SELL'}  "
                       f"pnl ${r.pnl:+.2f}  (ADX {r.adx:.0f})")
    ax.axhline(r.entry, color="#1f77b4", ls="-", lw=1, label=f"entry {r.entry:.1f}")
    ax.axhline(r.sl, color="#c0392b", ls="--", lw=1, label=f"SL {r.sl:.1f}")
    ax.axhline(r.tp, color="#2ecc71", ls=":", lw=1, label=f"TP {r.tp:.1f}")
    mark(ax, bars, idx5[int(r.ei)], r.entry, "ENTRY", "#1f77b4", (bars["high"].max()-bars["low"].min())*0.08)
    mark(ax, bars, idx5[int(r.xi)], r.exit, "SL hit", "#c0392b", -(bars["high"].max()-bars["low"].min())*0.12)
    ax.legend(fontsize=7, loc="upper left"); plt.tight_layout()
    plt.savefig(f"{OUT}/golden_sl_{k}.png", dpi=110); plt.close()
    print(f"saved golden_sl_{k}.png  {idx5[int(r.ei)]:%m-%d %H:%M} {'BUY' if r.dir==1 else 'SELL'} pnl {r.pnl:+.1f}")

# ================= Z (H1) =================
zt = []
for e, x, d, p in sim_dual(dmap=daily_map(50), use_daily=True):
    zt.append(dict(e=e, x=x, dir=d, pnl=p))
zdf = pd.DataFrame(zt)
zsl = zdf[zdf.pnl < 0].nsmallest(3, "pnl")
he = h1.index
for k, (_, r) in enumerate(zsl.iterrows(), 1):
    ei = he.get_indexer([r.e], method="nearest")[0]; xi = he.get_indexer([r.x], method="nearest")[0]
    a, b = ei - 24, xi + 10
    bars = h1.iloc[max(0, a):b]
    entry_px = float(h1["open"].iloc[ei]); exit_px = float(h1["close"].iloc[xi])
    fig, ax = plt.subplots(figsize=(11, 5))
    candles(ax, bars, f"Z SL #{k}  {r.e:%Y-%m-%d %H:%M} UTC  {r.dir.upper()}  pnl ${r.pnl:+.2f}  (S&R whipsaw)")
    ax.axhline(entry_px, color="#1f77b4", ls="-", lw=1, label=f"entry ~{entry_px:.0f}")
    mark(ax, bars, he[ei], entry_px, "ENTRY " + r.dir, "#1f77b4", (bars["high"].max()-bars["low"].min())*0.08)
    mark(ax, bars, he[xi], exit_px, "stop/exit", "#c0392b", -(bars["high"].max()-bars["low"].min())*0.12)
    ax.legend(fontsize=7, loc="upper left"); plt.tight_layout()
    plt.savefig(f"{OUT}/z_sl_{k}.png", dpi=110); plt.close()
    print(f"saved z_sl_{k}.png  {r.e:%m-%d %H:%M} {r.dir} pnl {r.pnl:+.1f}")
print("OUT:", OUT)
print("DONE")
