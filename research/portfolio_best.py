"""PORTFOLIO CONSTRUCTION STUDY — the pro-quant answer to 'PF 2 + small DD + real edge'.

A PF-2 book with small drawdown comes from COMBINING uncorrelated validated edges, not from one
hero strategy. This computes, from the SAME audited sims used all along ($ at 0.01 lot):

  components:  Z    = zrev XAU 1H dual-filter (archived; OOS PF ~2.2, the strongest earner)
               NAS  = US100 ORB (live; thin but ~zero-corr diversifier)
               LIQ  = liquidity 15min flat-limit both-sides $13/$26 (live; PF 1.33, mirrors dev)

  books:       CURRENT  = NAS + LIQ          (what runs today)
               PREVIOUS = Z + NAS            (what ran before Z was archived)
               FULL     = Z + NAS + LIQ      (all three)

For each: n, PF, net$, maxDD$, OOS-PF, MC5, worst year (2022 stress), and the monthly-PnL
correlation matrix. Run: python research/portfolio_best.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5
from zrev_dual_trend import sim_dual, daily_map
from portfolio_audit import nas_dollars

SL_D, TP_D, COST = 13.0, 26.0, 0.30

# ---------------------------------------------------------------- components
z = sim_dual(dmap=daily_map(50), use_daily=True)
Z = pd.Series([t[3] for t in z], index=pd.DatetimeIndex([t[1] for t in z])).sort_index()
NAS = nas_dollars().sort_index()

# LIQ: 15min flat-limit both-sides, M5 first-touch fill (same sim as liquidity_backtest_ml)
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
        if t == -1 and cc[i] > d1: t = 1
        elif t == 1 and cc[i] < u1: t = -1
        trend[i] = t
    return up, dn, trend


up, dn, trend = supertrend(M15)
last_long = np.full(len(M15), np.nan); last_short = np.full(len(M15), np.nan)
ll = ls = np.nan
for i in range(len(M15)):
    if trend[i] == 1:
        if i == 0 or trend[i - 1] != 1: ll = np.nan
        ls = np.nan
        if i >= 2 and up[i] == up[i - 1] == up[i - 2]: ll = up[i]
    else:
        if i == 0 or trend[i - 1] != -1: ls = np.nan
        ll = np.nan
        if i >= 2 and dn[i] == dn[i - 1] == dn[i - 2]: ls = dn[i]
    last_long[i] = ll; last_short[i] = ls

lvl = pd.DataFrame({"ll": last_long, "ls": last_short, "trend": trend}, index=M15.index)
m5 = M5.join(lvl.reindex(M5.index, method="ffill")).dropna(subset=["trend"])
lo = m5["low"].values; hi = m5["high"].values; idx5 = m5.index
mll = m5["ll"].values; mls = m5["ls"].values; mtr = m5["trend"].values
liq_tr = []
pos = 0; entry = tp = sl = 0.0
for i in range(len(m5)):
    if pos == 0:
        if mtr[i] == 1 and np.isfinite(mll[i]) and lo[i] <= mll[i]:
            pos, entry = 1, mll[i]; sl, tp = entry - SL_D, entry + TP_D
        elif mtr[i] == -1 and np.isfinite(mls[i]) and hi[i] >= mls[i]:
            pos, entry = -1, mls[i]; sl, tp = entry + SL_D, entry - TP_D
    else:
        d = None
        if pos == 1:
            if lo[i] <= sl: d = -(SL_D + COST)
            elif hi[i] >= tp: d = (TP_D - COST)
        else:
            if hi[i] >= sl: d = -(SL_D + COST)
            elif lo[i] <= tp: d = (TP_D - COST)
        if d is not None:
            liq_tr.append((idx5[i], d)); pos = 0
LIQ = pd.Series([p for _, p in liq_tr], index=pd.DatetimeIndex([t for t, _ in liq_tr])).sort_index()

# ---------------------------------------------------------------- reporting
def comp_line(name, s):
    st = stats(list(s.values)); items = list(zip(s.index, s.values)); _, o = split(items)
    eq = s.cumsum(); dd = float((eq - eq.cummax()).min())
    y22 = float(s[s.index.year == 2022].sum())
    print(f"  {name:9s} n={st['n']:4d} PF={st['pf']:4.2f} OOS={stats(o)['pf']:4.2f} "
          f"net=${s.sum():+6.0f} maxDD=${dd:+6.0f} 2022=${y22:+5.0f}")


def book_line(name, comps):
    s = pd.concat(comps).sort_index()
    st = stats(list(s.values)); items = list(zip(s.index, s.values)); _, o = split(items)
    eq = s.cumsum(); dd = float((eq - eq.cummax()).min())
    y22 = float(s[s.index.year == 2022].sum())
    mc = mc_pf_p5([p for _, p in items])
    print(f"  {name:22s} n={st['n']:4d} PF={st['pf']:4.2f} OOS-PF={stats(o)['pf']:4.2f} "
          f"net=${s.sum():+6.0f} maxDD=${dd:+6.0f} MC5={mc:4.2f} 2022=${y22:+5.0f}")


print("=== COMPONENTS ($ @0.01 lot, 2021-2026) ===")
comp_line("Z", Z); comp_line("NAS", NAS); comp_line("LIQ", LIQ)

print("\n=== MONTHLY-PnL CORRELATION ===")
M = pd.DataFrame({"Z": Z.resample("MS").sum(), "NAS": NAS.resample("MS").sum(),
                  "LIQ": LIQ.resample("MS").sum()}).fillna(0.0)
print(M.corr().round(2).to_string())

print("\n=== BOOKS ===")
book_line("CURRENT  (NAS+LIQ)", [NAS, LIQ])
book_line("PREVIOUS (Z+NAS)", [Z, NAS])
book_line("FULL     (Z+NAS+LIQ)", [Z, NAS, LIQ])
print("\nDONE")
