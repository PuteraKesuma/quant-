"""Drawdown anatomy of the live book (zrev EMA100, the dominant DD source): dissect
WHAT drives the drawdown from several angles, so DD-reduction targets the real cause
(not a guess). $ at 0.01 lot ($1/point XAU). Then test one economically-motivated
fix: a losing-streak circuit breaker (pause after K consecutive losses).

Run: python research/dd_anatomy.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5
from pipeline.backtest.strategy_zrev import simulate, ZRevParams, trades_to_df, resample_1h, _adx

XAU = load_m1("XAUUSD"); H1 = resample_1h(XAU.assign(volume=0))
T = trades_to_df(simulate(H1, ZRevParams(20, 0, 0.30, trend_filter=True, trend_ema=100)))
T["entry_ts"] = pd.to_datetime(T["entry_ts"]); T["exit_ts"] = pd.to_datetime(T["exit_ts"])
T["pnl"] = T["pnl_points"].astype(float)
adx = _adx(H1, 14).shift(1)
T["adx"] = T["entry_ts"].map(lambda t: float(adx.loc[t]) if t in adx.index else np.nan)
T["hour"] = T["entry_ts"].dt.hour
T["month"] = T["entry_ts"].dt.to_period("M").astype(str)
pnl = T["pnl"].values


def pf(s):
    s = np.asarray(s); w = s[s > 0].sum(); l = -s[s < 0].sum(); return (w / l) if l > 0 else float("inf")
def mdd(s):
    e = np.cumsum(s); return float((e - np.maximum.accumulate(e)).min())


print(f"zrev EMA100: {len(T)} trades  net=${pnl.sum():+.0f}  PF={pf(pnl):.2f}  maxDD=${mdd(pnl):.0f}")
w = pnl[pnl > 0]; l = pnl[pnl < 0]
print(f"  WR={100*len(w)/len(pnl):.0f}%  avgWin=${w.mean():.1f}  avgLoss=${l.mean():.1f}  "
      f"payoff={w.mean()/abs(l.mean()):.2f}  exp=${pnl.mean():+.2f}/trade")

# ---- angle 1: WHEN (worst DD window + worst months) ----
eq = np.cumsum(pnl); peak = np.maximum.accumulate(eq); dd = eq - peak
it = dd.argmin(); ip = eq[:it + 1].argmax()
print(f"\n[1] KAPAN: max DD ${dd[it]:.0f}  {T['entry_ts'].iloc[ip].date()} -> {T['exit_ts'].iloc[it].date()} "
      f"({it-ip} trade)")
mm = T.groupby("month")["pnl"].sum().sort_values()
print("    5 bulan terburuk:", {m: round(v) for m, v in mm.head(5).items()})

# ---- angle 2: DIRECTION ----
print("\n[2] ARAH:")
for d in ("long", "short"):
    s = T[T["direction"] == d]["pnl"].values
    print(f"    {d:5s}: n={len(s):4d} net=${s.sum():+6.0f} PF={pf(s):.2f} DD=${mdd(s):.0f} WR={100*(s>0).mean():.0f}%")

# ---- angle 3: REGIME (ADX bucket at entry) ----
print("\n[3] REGIME (ADX saat entry):")
for lo, hi in [(0, 15), (15, 20), (20, 25), (25, 100)]:
    s = T[(T["adx"] >= lo) & (T["adx"] < hi)]["pnl"].values
    if len(s):
        print(f"    ADX {lo:>2}-{hi:<3}: n={len(s):4d} net=${s.sum():+6.0f} PF={pf(s):.2f} exp=${s.mean():+.2f}")

# ---- angle 4: LOSING STREAKS ----
streak = mx = 0; worst_cost = 0.0; run = 0.0
streaks = []
for x in pnl:
    if x < 0:
        streak += 1; run += x
    else:
        if streak: streaks.append((streak, run))
        streak = 0; run = 0.0
if streak: streaks.append((streak, run))
mx = max(s for s, _ in streaks); worst = min(streaks, key=lambda z: z[1])
print(f"\n[4] STREAK: max kalah beruntun = {mx}x; streak termahal = {worst[0]}x seharga ${worst[1]:.0f}")
print(f"    distribusi streak >=4: {sum(1 for s,_ in streaks if s>=4)} kali")

# ---- angle 5: CONCENTRATION (top trades) ----
srt = np.sort(pnl)[::-1]
gross_w = pnl[pnl > 0].sum()
print(f"\n[5] KONSENTRASI: top-10 winner = ${srt[:10].sum():.0f} = {100*srt[:10].sum()/gross_w:.0f}% dari gross profit")
print(f"    tanpa top-3 winner: net jadi ${pnl.sum()-srt[:3].sum():.0f}")

# ---- angle by hour (bleed hours) ----
hh = T.groupby("hour")["pnl"].sum()
print(f"\n[+] JAM entry terburuk (UTC): {{ {', '.join(f'{h}:{round(v)}' for h,v in hh.sort_values().head(4).items())} }}")

# ============ FIX TEST: losing-streak circuit breaker ============
print("\n=== FIX: circuit breaker (pause J trade setelah K kalah beruntun) ===")
print(f"    base: net=${pnl.sum():+.0f} DD=${mdd(pnl):.0f} PF={pf(pnl):.2f}")
ts = T["exit_ts"].values
for K in (3, 4, 5):
    for J in (3, 5, 8):
        kept = []; cons = 0; skip = 0
        for x in pnl:
            if skip > 0:
                skip -= 1
                # paused: this trade not taken
                if x < 0: cons += 1
                else: cons = 0
                continue
            kept.append(x)
            if x < 0:
                cons += 1
                if cons >= K:
                    skip = J; cons = 0
            else:
                cons = 0
        kept = np.array(kept)
        print(f"    K={K} J={J}: trades={len(kept):4d} net=${kept.sum():+6.0f} "
              f"DD=${mdd(kept):6.0f} PF={pf(kept):.2f}")
