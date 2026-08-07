"""Per-trade table for the INTRADAY XAU/NAS book, Jan-Jul 2026, $1000, 0.01 lots.
Z (XAU) via the committed strategy_zrev engine w/ live-ish params (entry20/exit20 + H1-EMA100 trend);
NOTE: this core engine EXCLUDES the live daily-filter + 3xATR stop + dynamic-lot(0.01-0.02) -> APPROX.
ORB (NAS) via a faithful mirror of the committed nas_orb (exact live logic, clean 1R TP/SL).
$/pt at 0.01 lot: XAU $1.00, US100 $0.10.  Golden excluded (no verified standalone backtest).
"""
import os, sys, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from pipeline.backtest.strategy_zrev import simulate as zrev_sim, ZRevParams, resample_1h

Y0 = pd.Timestamp("2026-01-01", tz="UTC"); EQ0 = 1000.0

# ---------- Z (XAU) ----------
m1x = load_m1("XAUUSD"); h1 = resample_1h(m1x.assign(volume=0))
ztr = zrev_sim(h1, ZRevParams(donchian_n=20, exit_n=20, trend_filter=True, trend_ema=100, cost_points=0.30))
Z = []
for t in ztr:
    if t.exit_ts is None or t.exit_ts < Y0:
        continue
    Z.append(("Z (XAU)", t.entry_ts, "LONG" if t.direction == "long" else "SHORT",
              t.entry_price, "S&R", t.exit_ts, t.exit_price, "reverse/flat", t.pnl_points * 1.0))

# ---------- ORB (NAS) — faithful mirror of committed nas_orb, with detail capture ----------
def _nas_open_min(d):
    et = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return (13 * 60 + 30) + (60 if et.dst() == dt.timedelta(0) else 0)

def orb_detail(m1, range_min=30, tp_mult=1.0, sl_mult=1.0, trend_sma=50, breakeven_r=0.5,
               session_end_min=20 * 60, cost_points=2.0):
    d1 = to_d1(m1); dclose = d1["close"]; sma = dclose.rolling(trend_sma).mean()
    pc, ps = dclose.shift(1), sma.shift(1); tbd = {}
    for ts in d1.index:
        a, b = pc.loc[ts], ps.loc[ts]
        tbd[ts.date()] = 0 if (np.isnan(a) or np.isnan(b)) else (1 if a > b else (-1 if a < b else 0))
    H, L, C = m1["high"].values, m1["low"].values, m1["close"].values
    mod = m1.index.hour.values * 60 + m1.index.minute.values
    day_ord = m1.index.normalize().asi8; uniq, starts = np.unique(day_ord, return_index=True)
    starts = list(starts) + [len(m1)]; out = []
    for di in range(len(uniq)):
        a, b = starts[di], starts[di + 1]; day_date = m1.index[a].date(); om = _nas_open_min(day_date)
        modd = mod[a:b]; idx = np.arange(a, b); rmask = (modd >= om) & (modd < om + range_min)
        if rmask.sum() < range_min // 2: continue
        ridx = idx[rmask]; oh = H[ridx].max(); ol = L[ridx].min(); size = oh - ol
        if size <= 0: continue
        pidx = idx[modd >= om + range_min]; ei = d = entry = None
        for i in pidx:
            if H[i] > oh: ei, d, entry = i, 1, oh; break
            if L[i] < ol: ei, d, entry = i, -1, ol; break
        if ei is None: continue
        td = tbd.get(day_date, 0)
        if td == 0 or (td > 0) != (d == 1): continue
        tp = entry + d * size * tp_mult; sl = entry - d * size * sl_mult; risk = size * sl_mult
        cost_r = cost_points / risk; armed = False; pnl = None; xi = None
        for j in range(ei, b):
            if mod[j] >= session_end_min: pnl = d * (C[j] - entry) / risk - cost_r; xi = j; break
            if d == 1:
                if breakeven_r is not None and not armed and (H[j] - entry) >= breakeven_r * risk: armed = True
                if armed and L[j] <= entry: pnl = -cost_r; xi = j; break
                if L[j] <= sl: pnl = -1.0 - cost_r; xi = j; break
                if H[j] >= tp: pnl = tp_mult / sl_mult - cost_r; xi = j; break
            else:
                if breakeven_r is not None and not armed and (entry - L[j]) >= breakeven_r * risk: armed = True
                if armed and H[j] >= entry: pnl = -cost_r; xi = j; break
                if H[j] >= sl: pnl = -1.0 - cost_r; xi = j; break
                if L[j] <= tp: pnl = tp_mult / sl_mult - cost_r; xi = j; break
        if pnl is None: pnl = d * (C[b - 1] - entry) / risk - cost_r; xi = b - 1
        why = ("TP" if abs(pnl - (tp_mult / sl_mult - cost_r)) < 1e-9 else
               "SL" if abs(pnl - (-1.0 - cost_r)) < 1e-9 else "BE/time")
        out.append(("ORB (NAS)", m1.index[ei], "LONG" if d == 1 else "SHORT", entry, sl,
                    m1.index[xi], C[xi] if why == "BE/time" else (tp if why == "TP" else sl), why,
                    pnl * risk * 0.10))   # $ = pnl_R * risk_pts * $0.10/pt
    return out

ORB = [r for r in orb_detail(load_m1("NAS100")) if r[5] >= Y0]

allT = sorted(Z + ORB, key=lambda r: r[1])
hdr = f"{'Sleeve':10} {'Entry(UTC)':16} {'Dir':5} {'EntryPx':>9} {'SL':>9} {'Exit(UTC)':16} {'ExitPx':>9} {'Why':9} {'PnL$':>8} {'Equity':>9}"
print(f"INTRADAY XAU/NAS book — trades exiting Jan-Jul 2026  (${EQ0:.0f}, 0.01 lot)  data thru {load_m1('NAS100').index.max():%Y-%m-%d}\n")
print(hdr); print("-" * len(hdr))
eq = EQ0
for s, en, d, ep, sl, xd, xp, why, pnl in allT:
    eq += pnl
    sls = f"{sl:.2f}" if isinstance(sl, (int, float)) else str(sl)
    xps = f"{xp:.2f}" if isinstance(xp, (int, float)) else str(xp)
    print(f"{s:10} {en:%Y-%m-%d %H:%M} {d:5} {ep:>9.2f} {sls:>9} {xd:%Y-%m-%d %H:%M} {xps:>9} {why:9} {pnl:>+8.2f} {eq:>9.2f}")
print("-" * len(hdr))
zt = sum(r[8] for r in Z); ot = sum(r[8] for r in ORB)
print(f"Z (XAU): {len(Z)} trd  PnL {zt:+.2f}   |   ORB (NAS): {len(ORB)} trd  PnL {ot:+.2f}   |   TOTAL {zt+ot:+.2f}")
print(f"=> ${EQ0:.0f} -> ${EQ0+zt+ot:.2f}  ({(zt+ot)/EQ0:+.1%})   [Z approx, ORB exact, Golden excluded]")
print("DONE")
