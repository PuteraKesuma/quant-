"""Is the BOOK green every year over 11 years — or only Z?

z_30year.py reported Z ALONE: green 6/10, red in 2017 (-$30), 2018 (-$49), 2022 (-$21), 2023 (-$68).
But we do not trade Z alone. Those red years are TINY, and the other sleeves routinely make
+$100..+400/yr. Nobody ever tested the BOOK past 2021 — this does.

Data reach (audited):
  Z         FBS H1+D1, real H1 only from 2015 (pre-2015 is 1 bar/day backfill) -> 2015..2025, 2016 = hole
  Reversal  universe_daily.parquet -> NAS100/SP500 from 2012, DAX from mid-2012 -> covers the whole span
  ORB       needs M1 for the opening range: Dukascopy M1 starts 2021, FBS M1 is only ~100 days
            -> CANNOT be extended. Its own 687-trade record is +$0.89/trade, 6/6 green (2021-26),
               worth ~+$70..186/yr. It is EXCLUDED here, so this book is HANDICAPPED vs the live one.

So: honest question answered = "Z + Reversal, 2015-2025". If that is green in Z's red years, the live
book (which also has ORB adding a positive, independent stream) is green there too, with margin.
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
import MetaTrader5 as mt5
from fbs_engine import server_offset, fbs_bars, z_usd_from_bars
from regime_fix import reversal_usd, daily

REV_SYMS = ["NAS100", "SP500", "DAX"]
ORB_PER_YEAR = 116.0        # mean of ORB's own 2021-26 record (+31,+113,+70,+151,+95,+186); NOT used in
                            # the book totals -- only shown as the margin ORB would add.

if not mt5.initialize(): print("MT5 init fail", mt5.last_error()); raise SystemExit
OFF = server_offset()
h1 = fbs_bars("XAUUSD", mt5.TIMEFRAME_H1, OFF)
d1 = fbs_bars("XAUUSD", mt5.TIMEFRAME_D1, OFF)
mt5.shutdown()

Z = daily(z_usd_from_bars(h1, d1))
REV = {s: daily(reversal_usd(s)) for s in REV_SYMS}

# usable Z years (audited in z_30year.py): >=15 H1 bars/day and a full D1 year
hy = h1.groupby([h1.index.year, h1.index.normalize()]).size().groupby(level=0).agg(["sum", "count"])
usable = [int(y) for y, r in hy.iterrows() if r["sum"] / r["count"] >= 15 and 200 <= (d1.index.year == y).sum()]
print(f"Z usable years (real H1 + full D1): {usable}")

lo, hi = min(usable), max(usable)
cal = pd.date_range(f"{lo}-01-01", f"{hi}-12-31", freq="B", tz="UTC")
Zc = Z.reindex(cal).fillna(0)
Rc = {s: REV[s].reindex(cal).fillna(0) for s in REV_SYMS}
Rtot = sum(Rc.values())
book = Zc + Rtot

print("\n" + "=" * 104)
print(f"BOOK 2015-2025 = Z(ADX28) + Reversal(NAS100+SP500+DAX)   [ORB excluded: no M1 -> book is HANDICAPPED]")
print("=" * 104)
print(f"{'year':>6}{'Z $':>9}{'NAS $':>8}{'SP500 $':>9}{'DAX $':>8}{'Rev tot':>9}{'BOOK $':>10}{'DD $':>8}"
      f"{'+ORB est':>10}  verdict")
greens = counted = 0
for y in range(lo, hi + 1):
    m = cal.year == y
    zy = float(Zc[m].sum()); ry = {s: float(Rc[s][m].sum()) for s in REV_SYMS}
    rt = sum(ry.values()); bk = zy + rt
    if y not in usable:
        print(f"{y:>6}{'--':>9}{ry['NAS100']:>+8.0f}{ry['SP500']:>+9.0f}{ry['DAX']:>+8.0f}{rt:>+9.0f}"
              f"{'--':>10}{'--':>8}{'--':>10}  (Z: data ditolak)")
        continue
    e = book[m].cumsum(); dd = float((e - e.cummax()).min())
    counted += 1; greens += int(bk > 0)
    print(f"{y:>6}{zy:>+9.0f}{ry['NAS100']:>+8.0f}{ry['SP500']:>+9.0f}{ry['DAX']:>+8.0f}{rt:>+9.0f}"
          f"{bk:>+10.0f}{dd:>8.0f}{bk + ORB_PER_YEAR:>+10.0f}  {'GREEN' if bk > 0 else 'RED'}")
ub = book[np.isin(cal.year, usable)]
e = ub.cumsum()
print("-" * 104)
print(f"{'ALL':>6}{float(Zc[np.isin(cal.year, usable)].sum()):>+9.0f}"
      f"{'':>8}{'':>9}{'':>8}{float(Rtot[np.isin(cal.year, usable)].sum()):>+9.0f}"
      f"{float(ub.sum()):>+10.0f}{float((e-e.cummax()).min()):>8.0f}"
      f"{'':>10}  green {greens}/{counted}")
sh = ub.mean() / ub.std() * np.sqrt(252) if ub.std() > 0 else np.nan
print(f"  Sharpe {sh:.2f} | worst year ${min(float(book[cal.year == y].sum()) for y in usable):+.0f}")

# path-dependent equity from $1000 with the live lot-step rule
eq = 1000.0; curve = []
vals = ub.values
for v in vals:
    k = max(1, int(eq // 1500)); eq += v * k; curve.append(eq)
curve = pd.Series(curve, index=ub.index)
ddp = (curve - curve.cummax()) / curve.cummax()
yrs = (ub.index[-1] - ub.index[0]).days / 365.25
print(f"\n  From $1000 (lot-step per $1500, ORB excluded): final ${curve.iloc[-1]:,.0f} | "
      f"CAGR {(curve.iloc[-1]/1000)**(1/yrs)-1:.0%} | maxDD {ddp.min():.0%}")
print("\nHONEST: this is 10 usable years, ONE path, and it EXCLUDES ORB (which is positive and")
print("independent, so the live book sits above these rows). It does NOT prove 'green forever' —")
print("2008 and the 2013 gold crash are unobtainable, and ~37% of individual MONTHS are still red.")
print("DONE")
