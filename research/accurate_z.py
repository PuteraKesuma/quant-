"""ACCURATE Z (XAU) port of the LIVE ZRevStrategy: Donchian-20 S&R + H1-EMA100 trend gate +
Daily-SMA50 gate + TRAILING 3xATR stop + dynamic lot 0.01-0.02. Faithful reconstruction of
pipeline/live/signal.ZRevStrategy (read from source, not memory). Sanity-checked vs the known
validated live number (OOS PF ~2.19). Then combined with the EXACT ORB(NAS) for the book Sharpe.

Run: python research/accurate_z.py
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1, nas_orb
from pipeline.backtest.strategy_zrev import resample_1h

np.random.seed(7)
ENTRY_N = EXIT_N = 20; EMA = 100; DAILY_SMA = 50; ATR_MULT = 3.0; COST = 0.30
LOT_MIN, LOT_MAX, Z_LO, Z_HI = 0.01, 0.02, 0.5, 1.0


def accurate_z(m1):
    h = resample_1h(m1.assign(volume=0))
    c, hi, lo, op = h["close"], h["high"], h["low"], h["open"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"]-h["low"], (h["high"]-c.shift()).abs(), (h["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    # daily SMA50 trend, mapped to each hour (uses only completed daily bars -> no lookahead)
    d1 = to_d1(m1); dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtrend = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dt_by_date = {ts.date(): (0 if pd.isna(dtrend.loc[ts]) else int(dtrend.loc[ts])) for ts in d1.index}
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()

    idx = h.index; C, H, L, O = c.values, hi.values, lo.values, op.values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    m20, s20 = ma20.values, sd20.values
    pos = 0; ep = ets = eidx = sl = elot = None; trades = []
    dtl = [dt_by_date.get(idx[i].date(), 0) for i in range(len(idx))]

    def dyn_lot(i, direction):
        if s20[i] <= 0 or np.isnan(s20[i]): return LOT_MIN
        z = (C[i] - m20[i]) / s20[i]; zd = z if direction == 1 else -z
        frac = max(0.0, min(1.0, (zd - Z_LO) / max(Z_HI - Z_LO, 1e-9)))
        return round(max(LOT_MIN, min(LOT_MAX, round((LOT_MIN + frac*(LOT_MAX-LOT_MIN))/0.01)*0.01)), 2)

    for i in range(1, len(idx)):
        if np.isnan(upv[i]) or np.isnan(emv[i]) or np.isnan(atv[i]):
            continue
        up_tr = C[i-1] > emv[i]; dd = dtl[i]
        can_long = up_tr and (dd == 1); can_short = (not up_tr) and (dd == -1)
        if pos == 0:
            if H[i] >= upv[i] and can_long:
                pos, ep, ets, eidx = 1, max(O[i], upv[i]), idx[i], i; sl = ep - ATR_MULT*atv[i]; elot = dyn_lot(i, 1)
            elif L[i] <= dnv[i] and can_short:
                pos, ep, ets, eidx = -1, min(O[i], dnv[i]), idx[i], i; sl = ep + ATR_MULT*atv[i]; elot = dyn_lot(i, -1)
            continue
        if pos == 1:
            sl = max(sl, C[i-1] - ATR_MULT*atv[i])          # trailing ATR stop (ratchet up)
            if L[i] <= sl:                                   # stopped
                fill = min(O[i], sl); trades.append(("long", ets, ep, idx[i], fill, (fill-ep)-COST, elot)); pos = 0
            elif L[i] <= xdv[i]:                              # channel S&R
                fill = min(O[i], xdv[i]); trades.append(("long", ets, ep, idx[i], fill, (fill-ep)-COST, elot))
                if L[i] <= dnv[i] and can_short:
                    pos, ep, ets, eidx = -1, min(O[i], dnv[i]), idx[i], i; sl = ep + ATR_MULT*atv[i]; elot = dyn_lot(i, -1)
                else: pos = 0
        else:
            sl = min(sl, C[i-1] + ATR_MULT*atv[i])
            if H[i] >= sl:
                fill = max(O[i], sl); trades.append(("short", ets, ep, idx[i], fill, (ep-fill)-COST, elot)); pos = 0
            elif H[i] >= xuv[i]:
                fill = max(O[i], xuv[i]); trades.append(("short", ets, ep, idx[i], fill, (ep-fill)-COST, elot))
                if H[i] >= upv[i] and can_long:
                    pos, ep, ets, eidx = 1, max(O[i], upv[i]), idx[i], i; sl = ep - ATR_MULT*atv[i]; elot = dyn_lot(i, 1)
                else: pos = 0
    return trades


def report(items_pts, name):   # items = [(exit_ts, pnl_points)]
    pnl = pd.Series([p for _, p in items_pts], index=pd.DatetimeIndex([t for t, _ in items_pts], tz="UTC")).sort_index()
    n = len(pnl); w = pnl[pnl > 0]; l = pnl[pnl < 0]; pf = w.sum()/abs(l.sum()) if l.sum() else float("inf")
    day = pnl.groupby(pnl.index.normalize()).sum()
    cal = pd.date_range(day.index.min(), day.index.max(), freq="B", tz="UTC"); d = day.reindex(cal).fillna(0.0)
    sh = d.mean()/d.std()*np.sqrt(252) if d.std() > 0 else np.nan
    a = d.values; N = len(a); nb = max(1, N//10); shs = []
    for _ in range(3000):
        ix = (np.random.randint(0, N-10, nb)[:, None]+np.arange(10)).ravel(); s = a[ix]; sd = s.std()
        shs.append(s.mean()/sd*np.sqrt(252) if sd > 0 else 0.0)
    loCI, hiCI = np.percentile(shs, [2.5, 97.5]); yr = pnl.groupby(pnl.index.year).sum()
    print(f"=== {name} ===  trades {n}  win% {len(w)/n:.0%}  PF {pf:.2f}  Sharpe {sh:+.2f}  95%CI[{loCI:+.2f},{hiCI:+.2f}]  yrs+ {int((yr>0).sum())}/{len(yr)}")
    print(f"  per-year: " + "  ".join(f"{y}:{v:+.0f}" for y, v in yr.items()))
    return d, pf


ztr = accurate_z(load_m1("XAUUSD"))
z_items = [(t[3], t[5]) for t in ztr]                       # (exit_ts, pnl_points)
z_items_lot = [(t[3], t[5] * (t[6]/0.01)) for t in ztr]     # $-weighted by dynamic lot (0.01=1x)
print("ACCURATE Z (live logic: EMA100 + Daily-SMA50 + trailing 3xATR + dynamic lot)\n")
zd, zpf = report(z_items, "Z accurate (unit-lot points), FULL 2021-2026")
# proper apples-to-apples: OOS PF (2023-2026), which is what the live '2.19' referred to
oos = [(t, p) for t, p in z_items if t >= pd.Timestamp("2023-01-01", tz="UTC")]
ow = sum(p for _, p in oos if p > 0); ol = sum(p for _, p in oos if p < 0)
oos_pf = ow / abs(ol) if ol else float("inf")
print(f"  SANITY (apples-to-apples): my port OOS-PF(2023-26) = {oos_pf:.2f}  vs live-validated OOS PF ~2.19 "
      f"-> {'CONSISTENT' if 1.8 <= oos_pf <= 2.8 else 'still off, flag honestly'}\n")
od, _ = report(nas_orb(load_m1("NAS100"))[0], "ORB (NAS) exact")

J = pd.concat([zd.rename("z"), od.rename("o")], axis=1).dropna()
zn = J["z"]/J["z"].std(); on = J["o"]/J["o"].std(); comb = (zn+on)/2
print(f"\n=== COMBINED (accurate Z + ORB) ===  Sharpe {comb.mean()/comb.std()*np.sqrt(252):+.2f}   corr {J['z'].corr(J['o']):+.2f}")
print("DONE")
