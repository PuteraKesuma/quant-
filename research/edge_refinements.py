"""EDGE REFINEMENTS — two candidate improvements most retail never tests, run with full rigor.

(1) ATR-SCALED STOP for LIQ: the deployed $13/$26 is FIXED — it does not scale with volatility
    (flagged as the open refinement in the supertrend audit). Test SL = k*ATR15(14) at entry,
    TP = 2*SL (same 1:2 shape), vs the fixed baseline, on the SAME sim (M5 first-touch fill,
    cost $0.30). VERDICT RULE PRE-REGISTERED (before seeing results): adopt an ATR variant ONLY
    if it beats fixed on OOS-PF *and* WF-green *and* net — otherwise KEEP FIXED and say so.
(2) ENTRY-HOUR / WEEKDAY decomposition of the baseline LIQ trades: DESCRIPTIVE ONLY (retail
    'session filters' are usually data mining; nothing here gets deployed without its own
    IS/OOS validation). Purpose: spot structural cost traps (e.g. rollover-hour fills where
    real spread is a big fraction of a $13 stop).

Run: python research/edge_refinements.py   (rebuilds the LIQ sim only; ~2-5 min)
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

COST = 0.30
SL_FIX, TP_FIX = 13.0, 26.0

print("=== loading M1 + building 15min supertrend levels ===", flush=True)
M1 = load_m1("XAUUSD")
agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
M15 = M1.resample("15min").agg(agg).dropna(subset=["open"])
M5 = M1.resample("5min").agg(agg).dropna(subset=["open"])


def supertrend(h, per=21, mult=5.5):
    hh = h["high"].values; ll = h["low"].values; cc = h["close"].values
    pc = np.roll(cc, 1); pc[0] = cc[0]
    tr = np.maximum(hh - ll, np.maximum(np.abs(hh - pc), np.abs(ll - pc)))
    atr = np.full(len(cc), np.nan); atr[per - 1] = tr[:per].mean()
    for i in range(per, len(cc)):
        atr[i] = (atr[i - 1] * (per - 1) + tr[i]) / per
    src = (hh + ll) / 2.0
    up = np.full(len(cc), np.nan); dn = np.full(len(cc), np.nan); trend = np.ones(len(cc), int)
    for i in range(len(cc)):
        if not np.isfinite(atr[i]):
            up[i] = src[i]; dn[i] = src[i]; trend[i] = 1; continue
        bu = src[i] - mult * atr[i]; bd = src[i] + mult * atr[i]
        u1 = up[i - 1] if i > 0 and np.isfinite(up[i - 1]) else bu
        d1 = dn[i - 1] if i > 0 and np.isfinite(dn[i - 1]) else bd
        up[i] = max(bu, u1) if (i > 0 and cc[i - 1] > u1) else bu
        dn[i] = min(bd, d1) if (i > 0 and cc[i - 1] < d1) else bd
        t = trend[i - 1] if i > 0 else 1
        if t == -1 and cc[i] > d1:
            t = 1
        elif t == 1 and cc[i] < u1:
            t = -1
        trend[i] = t
    return up, dn, trend


up, dn, trend = supertrend(M15)
last_long = np.full(len(M15), np.nan); last_short = np.full(len(M15), np.nan)
ll_ = ls_ = np.nan
for i in range(len(M15)):
    if trend[i] == 1:
        if i == 0 or trend[i - 1] != 1:
            ll_ = np.nan
        ls_ = np.nan
        if i >= 2 and up[i] == up[i - 1] == up[i - 2]:
            ll_ = up[i]
    else:
        if i == 0 or trend[i - 1] != -1:
            ls_ = np.nan
        ll_ = np.nan
        if i >= 2 and dn[i] == dn[i - 1] == dn[i - 2]:
            ls_ = dn[i]
    last_long[i] = ll_; last_short[i] = ls_

# ATR15 (Wilder 14) at each M15 bar, for the ATR-scaled stop variants
tr15 = pd.concat([M15["high"] - M15["low"],
                  (M15["high"] - M15["close"].shift()).abs(),
                  (M15["low"] - M15["close"].shift()).abs()], axis=1).max(axis=1)
atr15 = tr15.ewm(alpha=1 / 14, adjust=False).mean()

lvl = pd.DataFrame({"ll": last_long, "ls": last_short, "trend": trend,
                    "atr": atr15.values}, index=M15.index)
m5 = M5.join(lvl.reindex(M5.index, method="ffill")).dropna(subset=["trend"])
LO = m5["low"].values; HI = m5["high"].values; IDX5 = m5.index
MLL = m5["ll"].values; MLS = m5["ls"].values; MTR = m5["trend"].values; MATR = m5["atr"].values
med_atr = float(np.nanmedian(MATR))
print(f"median ATR15 = ${med_atr:.2f}  (fixed $13 = {13/med_atr:.1f}x median ATR)", flush=True)


def run(mode, k=None, sl_min=6.0, sl_max=30.0):
    """One LIQ sim. mode 'fixed' -> $13/$26; mode 'atr' -> SL=k*ATR15 (clamped), TP=2*SL."""
    trades = []
    pos = 0; entry = tp = sl = 0.0; e_ts = None
    for i in range(len(m5)):
        if pos == 0:
            hit_l = MTR[i] == 1 and np.isfinite(MLL[i]) and LO[i] <= MLL[i]
            hit_s = MTR[i] == -1 and np.isfinite(MLS[i]) and HI[i] >= MLS[i]
            if not (hit_l or hit_s):
                continue
            entry = MLL[i] if hit_l else MLS[i]
            if mode == "fixed":
                sd, td = SL_FIX, TP_FIX
            else:
                if not np.isfinite(MATR[i]):
                    continue
                sd = min(max(k * MATR[i], sl_min), sl_max); td = 2.0 * sd
            e_ts = IDX5[i]
            if hit_l:
                pos, sl, tp = 1, entry - sd, entry + td
            else:
                pos, sl, tp = -1, entry + sd, entry - td
        else:
            d = None
            if pos == 1:
                if LO[i] <= sl:
                    d = -((entry - sl) + COST)
                elif HI[i] >= tp:
                    d = (tp - entry) - COST
            else:
                if HI[i] >= sl:
                    d = -((sl - entry) + COST)
                elif LO[i] <= tp:
                    d = (entry - tp) - COST
            if d is not None:
                trades.append((e_ts, IDX5[i], d)); pos = 0
    return trades


def score(name, trades):
    s = pd.Series([t[2] for t in trades], index=pd.DatetimeIndex([t[1] for t in trades])).sort_index()
    st = stats(list(s.values))
    items = list(zip(s.index, s.values)); i_, o = split(items)
    eq = s.cumsum(); dd = float((eq - eq.cummax()).min())
    win = s.groupby(pd.Grouper(freq="2MS")).sum()
    win = win[win != 0]
    green = int((win > 0).sum())
    mc = mc_pf_p5([p for _, p in items])
    print(f"  {name:22s} n={st['n']:4d} PF={st['pf']:4.2f} OOS={stats(o)['pf']:4.2f} "
          f"net=${s.sum():+6.0f} maxDD=${dd:+6.0f} MC5={mc:4.2f} WFgreen={green}/{len(win)}")
    return dict(name=name, oos=stats(o)["pf"], net=float(s.sum()), green=green,
                nwin=len(win), s=s, trades=trades)


print("\n=== (1) ATR-SCALED STOP vs FIXED $13/$26 (same sim, cost $0.30) ===")
base = score("FIXED $13/$26 (live)", run("fixed"))
k0 = 13.0 / med_atr
variants = []
for f in (0.6, 0.8, 1.0, 1.25, 1.5):
    k = round(k0 * f, 1)
    variants.append(score(f"ATR k={k} (SL~${k*med_atr:.0f})", run("atr", k)))

best = max(variants, key=lambda v: v["oos"])
adopt = (best["oos"] > base["oos"] and best["green"] >= base["green"] and best["net"] > base["net"])
print(f"\n  pre-registered verdict: best ATR variant = {best['name']}")
print(f"  beats fixed on OOS ({best['oos']:.2f} vs {base['oos']:.2f})? {best['oos'] > base['oos']}")
print(f"  WF-green not worse ({best['green']}/{best['nwin']} vs {base['green']}/{base['nwin']})? {best['green'] >= base['green']}")
print(f"  net better (${best['net']:+.0f} vs ${base['net']:+.0f})? {best['net'] > base['net']}")
print(f"  => {'ADOPT candidate (validate per-year before deploy)' if adopt else 'KEEP FIXED $13/$26 — ATR scaling does NOT earn its complexity'}")

if adopt:
    print("\n  per-year net $ (fixed vs best ATR):")
    for y in sorted(set(base["s"].index.year)):
        fy = float(base["s"][base["s"].index.year == y].sum())
        ay = float(best["s"][best["s"].index.year == y].sum())
        print(f"    {y}: fixed {fy:+7.0f}   atr {ay:+7.0f}")

print("\n=== (2) ENTRY-HOUR / WEEKDAY decomposition of baseline LIQ (DESCRIPTIVE ONLY) ===")
bt = base["trades"]
es = pd.Series([t[2] for t in bt], index=pd.DatetimeIndex([t[0] for t in bt]))
byh = es.groupby(es.index.hour).agg(["count", "sum"])
print("  UTC hour | n | net$   (rollover 21-22 UTC = spread trap for a $13 stop)")
for h, row in byh.iterrows():
    flag = "  <== rollover" if h in (21, 22) else ""
    print(f"    {h:02d}      {int(row['count']):4d} {row['sum']:+8.0f}{flag}")
byd = es.groupby(es.index.dayofweek).agg(["count", "sum"])
days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
print("  weekday:")
for d, row in byd.iterrows():
    print(f"    {days[int(d)]}     {int(row['count']):4d} {row['sum']:+8.0f}")
print("  (CAUTION: descriptive; any filter from this table needs its own IS/OOS+WF validation)")
print("\nDONE")
