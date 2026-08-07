"""Test the Gotobi / Tokyo-Fix anomaly on USDJPY (open-source hypothesis, arXiv 2301.13204 / NBER w22820).
Claim: on Gotobi days (day-of-month in {5,10,15,20,25,30}) USDJPY drifts UP into the 09:55 JST fix
(= 00:55 UTC; Japan has no DST so it's fixed year-round), then pulls back. Honest test on 1m data:
pre-fix drift Gotobi vs non-Gotobi, per-year (is it decayed?), a tradeable net after spread, and the
post-fix pullback. Data: USDJPY_1m.duckdb (2023-2026, UTC).

Run: python research/gotobi_usdjpy.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, duckdb

PIP = 0.01; SPREAD = 0.9   # USDJPY pip + round-trip cost in pips
c = duckdb.connect(r"C:\Quant\data\Level_0_Raw\USDJPY_1m.duckdb", read_only=True)
rows = c.execute("select epoch(ts),close from ohlcv order by ts").fetchall(); c.close()
a = np.asarray(rows, "float64"); idx = pd.to_datetime(a[:, 0], unit="s", utc=True)
s = pd.Series(a[:, 1], index=idx)
print(f"USDJPY 1m: {len(s):,} bars  {idx.min():%Y-%m-%d} -> {idx.max():%Y-%m-%d}")


def price_at(hh, mm, win=3):
    """last price in [hh:mm, hh:mm+win) each day -> Series indexed by date."""
    t0 = pd.Timestamp("2000-01-01") + pd.Timedelta(hours=hh, minutes=mm)
    t1 = t0 + pd.Timedelta(minutes=win)
    sub = s.between_time(t0.time(), t1.time(), inclusive="left")
    return sub.groupby(sub.index.normalize()).last()


fix = price_at(0, 55)                       # 00:55 UTC = 09:55 JST fix
post = price_at(3, 0)                        # post-fix pullback reference
days = fix.index
dom = days.day
gotobi = pd.Series(np.isin(dom, [5, 10, 15, 20, 25, 30]), index=days)

print("\n=== pre-fix drift (entry -> 00:55 fix), Gotobi vs non-Gotobi ===")
print(f"{'entry(UTC)':>11} {'Goto mean':>10} {'nonG mean':>10} {'edge(pip)':>10} {'Goto n':>7}")
best=None
for hh, mm in [(21,0),(22,0),(23,0),(0,0),(0,30)]:
    ent = price_at(hh, mm)
    df = pd.concat([ent.rename("e"), fix.rename("f")], axis=1).dropna()
    df["g"] = gotobi.reindex(df.index)
    df["drift"] = (df["f"] - df["e"]) / PIP
    gm = df.loc[df.g, "drift"].mean(); nm = df.loc[~df.g, "drift"].mean()
    print(f"{hh:02d}:{mm:02d}      {gm:>10.2f} {nm:>10.2f} {gm-nm:>10.2f} {int(df.g.sum()):>7}")
    if best is None or (gm-nm) > best[0]: best=((gm-nm), hh, mm, df)

edge, hh, mm, df = best
print(f"\nbest entry {hh:02d}:{mm:02d}  Gotobi drift edge = {edge:+.2f} pip/day")
gd = df[df.g].copy(); gd["net"] = gd["drift"] - SPREAD
print(f"  LONG {hh:02d}:{mm:02d}->00:55 on Gotobi days: n={len(gd)}  win={ (gd['drift']>0).mean():.2f}  "
      f"gross={gd['drift'].mean():+.2f}pip  net(after {SPREAD}pip)={gd['net'].mean():+.2f}pip/trade  total={gd['net'].sum():+.0f}pip")
print("  per-year net(pip) [is it decayed?]:")
gy = gd.groupby(gd.index.year)["net"]
for y, v in gy.sum().items():
    print(f"    {y}: total {v:+.0f}  (avg {gd[gd.index.year==y]['net'].mean():+.2f}/trade, n={ (gd.index.year==y).sum() })")

print("\n=== post-fix pullback (SHORT 00:55 -> 03:00) on Gotobi days ===")
pf = pd.concat([fix.rename("f"), post.rename("p")], axis=1).dropna()
pf["g"] = gotobi.reindex(pf.index)
pf["short"] = (pf["f"] - pf["p"]) / PIP    # short pnl in pips
pg = pf[pf.g]
print(f"  SHORT 00:55->03:00 Gotobi: n={len(pg)} win={(pg['short']>0).mean():.2f} gross={pg['short'].mean():+.2f}pip "
      f"net={ (pg['short']-SPREAD).mean():+.2f}pip/trade total={ (pg['short']-SPREAD).sum():+.0f}pip")
print("\nread: real edge = Gotobi drift clearly > non-Gotobi AND net-positive after spread AND not decayed to ~0 recently.")
print("DONE")
