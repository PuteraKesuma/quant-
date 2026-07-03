"""FULL re-validation of the Supertrend XAU candidate at the CORRECT, broker-matched stop.

Broker (verified via MT5 symbol_info): XAUUSD contract_size=100, tick_size=0.01, tick_value=$1
=> 1 price unit ($1 gold move) = $1 P&L at 0.01 lot. So a $13 stop = 13 price units => with the
code's MINTICK 0.01, sl_pts=1300 (1300*0.01=13). TP $26 => tp_pts=2600. RR 2:1.

The earlier full battery was run at sl_pts=13000 (= $130 = 24x ATR, an input error). This re-runs
the whole battery at $13/$26 (2.4x ATR, tradeable), PLUS a monthly-PnL diversification check vs the
deployed live book (zrev-XAU + US100-orb). Run:  python research/supertrend_validate13.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
import numpy as np
import pandas as pd
from supertrend_long_xau import run, report
from zrev_dual_trend import sim_dual, daily_map
from portfolio_audit import nas_dollars

SL, TP = 1300, 2600            # $13 / $26 at 0.01 lot (broker-matched)

print("=== broker map: sl_pts=1300 => 13 price units => $13 @0.01 lot (contract100, tick_value$1) ===")

print("\n=== TIMEFRAME SWEEP  LONG-only ($13/$26) ===")
for tf in ("15min", "30min", "1h", "2h", "4h"):
    report(run(tf=tf, tp_pts=TP, sl_pts=SL), f"{tf} LONG")

print("\n=== 1h: long-only vs both-sides ($13/$26) ===")
report(run(tf="1h", tp_pts=TP, sl_pts=SL), "1h LONG-only")
report(run(tf="1h", tp_pts=TP, sl_pts=SL, both_sides=True), "1h BOTH-sides")

print("\n=== 1h LONG-only: per-year R, cost stress, RR & param plateau ($13 base) ===")
b = run(tf="1h", tp_pts=TP, sl_pts=SL)
s = pd.Series([p for _, p in b], index=pd.DatetimeIndex([t for t, _ in b]))
print("  per-year R:", {int(k): round(v, 1) for k, v in s.groupby(s.index.year).sum().items()})
for c in (0.5, 1.0, 2.0, 5.0):
    report(run(tf="1h", tp_pts=TP, sl_pts=SL, cost=c), f"cost=${c}")
for sl, tp in ((1300, 2600), (1000, 2000), (1500, 3000), (1300, 1300), (2000, 4000)):
    report(run(tf="1h", tp_pts=tp, sl_pts=sl), f"sl{sl} tp{tp} RR{tp/sl:.1f}")
for p, m in ((21, 5.5), (14, 3.0), (10, 3.0), (21, 4.0)):
    report(run(tf="1h", tp_pts=TP, sl_pts=SL, period=p, mult=m), f"ATR{p}x{m}")

print("\n=== DIVERSIFICATION vs the live book (monthly PnL correlation) ===")
st = run(tf="1h", tp_pts=TP, sl_pts=SL)
st_s = pd.Series([p for _, p in st], index=pd.DatetimeIndex([t for t, _ in st]))
z = sim_dual(dmap=daily_map(50), use_daily=True)
z_s = pd.Series([t[3] for t in z], index=pd.DatetimeIndex([t[1] for t in z]))
book = pd.concat([z_s, nas_dollars()]).sort_index()
stm = st_s.resample("MS").sum()
al = pd.concat([stm.rename("st"), book.resample("MS").sum().rename("book")], axis=1).dropna()
al2 = pd.concat([stm.rename("st"), z_s.resample("MS").sum().rename("z")], axis=1).dropna()
print(f"  months overlap {len(al)} | monthly corr(supertrend, FULL book) = {al['st'].corr(al['book']):+.2f}")
print(f"  monthly corr(supertrend, zrev-XAU alone) = {al2['st'].corr(al2['z']):+.2f}")
print("  (low corr = timing diversifier; but note it is still LONG-gold-biased = regime concentration)")
print("\nDONE")
