"""Does Z survive regimes our 2021-2026 data NEVER contained? FBS H1 gold reaches back to 1996.

Z is the book's engine (78% of profit) and every number we have for it comes from 2021-2026 — which
holds exactly ONE mild gold bear (2022). The regimes that would actually kill a gold trend-follower
are absent: the 2008 crisis, the April-2013 crash (-13% in two days), and the 2013-2015 bear market.
FBS H1/D1 cover all of them, and Z needs no M1 -> research/fbs_engine.z_usd_from_bars runs it directly.

DATA QUALITY IS THE WHOLE QUESTION. Broker history that deep is usually backfilled and thin, and thin
H1 bars silently mutate the strategy: Donchian-20 means "20 completed H1 bars", so at 6 bars/day that
channel spans 3 days instead of one -> a DIFFERENT strategy wearing Z's name. First warning sign is
already in the probe: 69,584 H1 bars over 30 years = ~2,290/yr, but real gold H1 is ~5,800/yr = we
only have ~40%. So: audit per year FIRST, declare a usable span, and only then report P&L.

Anchor: 2021-2026 on FBS must reproduce the known Dukascopy result, or the pipeline is wrong and no
older number can be trusted.
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
import MetaTrader5 as mt5
from fbs_engine import server_offset, fbs_bars, z_usd_from_bars
from regime_fix import accurate_z_usd
from audit_live_strategies import load_m1

MIN_BARS_PER_DAY = 15          # pre-registered: below this the H1 channel is not a real H1 channel
REGIME = {2008: "krisis global", 2011: "puncak emas $1920", 2013: "CRASH emas -13%/2hr",
          2014: "bear emas", 2015: "bear emas (dasar)", 2020: "covid + ATH", 2022: "bear/chop"}

if not mt5.initialize(): print("MT5 init fail", mt5.last_error()); raise SystemExit
OFF = server_offset()
h1 = fbs_bars("XAUUSD", mt5.TIMEFRAME_H1, OFF)
d1 = fbs_bars("XAUUSD", mt5.TIMEFRAME_D1, OFF)
mt5.shutdown()

print("=" * 96)
print(f"1) DATA QUALITY AUDIT — FBS XAUUSD H1 {h1.index[0]:%Y-%m-%d} .. {h1.index[-1]:%Y-%m-%d} "
      f"({len(h1):,} bars) | D1 {len(d1):,} bars")
print("=" * 96)
print(f"{'year':>6}{'H1 bars':>9}{'days':>6}{'bars/day':>10}{'D1 bars':>9}{'px min':>9}{'px max':>9}  verdict")
usable = []
for y in range(h1.index[0].year, h1.index[-1].year + 1):
    hy = h1[h1.index.year == y]
    dy = d1[d1.index.year == y]
    if len(hy) == 0: continue
    ndays = len(np.unique(hy.index.normalize().asi8))
    bpd = len(hy) / max(ndays, 1)
    ok = bpd >= MIN_BARS_PER_DAY and len(dy) > 200
    if ok: usable.append(y)
    print(f"{y:>6}{len(hy):>9,}{ndays:>6}{bpd:>10.1f}{len(dy):>9}{hy['low'].min():>9.1f}{hy['high'].max():>9.1f}"
          f"  {'OK' if ok else 'TOO THIN -> reject'}")

if not usable:
    print("\nNo usable year. STOP."); raise SystemExit
lo_y, hi_y = min(usable), max(usable)
gap = [y for y in range(lo_y, hi_y + 1) if y not in usable]
print(f"\n  usable span: {lo_y}..{hi_y}" + (f"  (holes: {gap})" if gap else "  (contiguous)"))
print(f"  rejected   : {[y for y in range(h1.index[0].year, h1.index[-1].year+1) if y not in usable]}")

# ---------------- 2) anchor: does FBS reproduce our known Dukascopy result? ----------------
print("\n" + "=" * 96)
print("2) ANCHOR 2021-2026 — FBS H1/D1 vs our Dukascopy M1 result (if this fails, ignore everything else)")
print("=" * 96)
tr_all = z_usd_from_bars(h1, d1)
fz = pd.Series([p for _, p in tr_all], index=pd.DatetimeIndex([t for t, _ in tr_all], tz="UTC")).sort_index()
duka = accurate_z_usd(load_m1("XAUUSD"), adx_min=28)
dz = pd.Series([p for _, p in duka], index=pd.DatetimeIndex([t for t, _ in duka], tz="UTC")).sort_index()
print(f"{'year':>6}{'FBS $':>11}{'FBS trd':>9}{'Duka $':>11}{'Duka trd':>10}")
for y in range(2021, 2027):
    f_, d_ = fz[fz.index.year == y], dz[dz.index.year == y]
    print(f"{y:>6}{f_.sum():>+11.0f}{len(f_):>9}{d_.sum():>+11.0f}{len(d_):>10}")
print(f"{'SUM':>6}{fz[fz.index.year >= 2021].sum():>+11.0f}{len(fz[fz.index.year >= 2021]):>9}"
      f"{dz.sum():>+11.0f}{len(dz):>10}")

# ---------------- 3) the actual question ----------------
print("\n" + "=" * 96)
print(f"3) Z ACROSS 30 YEARS (usable span {lo_y}..{hi_y}) — 0.01-0.02 lot, live config w/ ADX-28 gate")
print("=" * 96)
print(f"{'year':>6}{'trades':>8}{'net $':>10}{'DD $':>9}{'gold %':>9}  regime")
tot = 0.0; greens = 0; counted = 0
for y in range(lo_y, hi_y + 1):
    ty = fz[fz.index.year == y]
    dy = d1[d1.index.year == y]
    gold = (dy["close"].iloc[-1] / dy["close"].iloc[0] - 1) * 100 if len(dy) > 1 else np.nan
    if y not in usable:
        print(f"{y:>6}{'':>8}{'':>10}{'':>9}{gold:>+8.1f}%  (data ditolak)"); continue
    eq = ty.cumsum(); dd = float((eq - eq.cummax()).min()) if len(ty) else 0.0
    tot += ty.sum(); counted += 1; greens += int(ty.sum() > 0)
    print(f"{y:>6}{len(ty):>8}{ty.sum():>+10.0f}{dd:>9.0f}{gold:>+8.1f}%  {REGIME.get(y, '')}")
print("-" * 96)
eqa = fz[(fz.index.year >= lo_y)].cumsum()
print(f"{'ALL':>6}{len(fz[fz.index.year >= lo_y]):>8}{tot:>+10.0f}{float((eqa-eqa.cummax()).min()):>9.0f}"
      f"   green {greens}/{counted}")
gy = [y for y in usable if fz[fz.index.year == y].sum() <= 0]
print(f"\n  RED years: {gy if gy else 'none'}")
print("\nREAD THIS BEFORE BELIEVING THE TABLE: these are broker bars, likely backfilled before ~2010s.")
print("A rejected year is rejected because thin H1 bars make Donchian-20 a different strategy, NOT")
print("because the strategy failed there. Only the OK rows carry information.")
print("DONE")
