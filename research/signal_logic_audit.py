"""SIGNAL-LOGIC AUDIT: does the LIVE execution match the validated logic, trade by trade?

(A) Z parity  — rebuild the zrev dual-filter+ATR-stop signal stream from MT5 H1/D1 for the last
               ~20 days and compare with the ACTUAL closed/open positions under magic 920622.
               Any entry/exit that the sim doesn't call for = a real bug (except the known,
               already-fixed 06-30 reconcile force-close).
(B) Z grid    — re-verify the parameter plateau on the audited duckdb data: entry/exit_n x EMA x
               daily-SMA around the deployed (20/20, 100, 50). If the deployed cell is far off
               the best cell, calibration is warranted; if it's on the plateau, 'recalibrating
               to wow' would be overfitting, not improvement.
(C) ORB check — for each recent US100 trade: recompute the NY 30-min opening range from MT5 M1
               and verify entry == range break level and SL/TP == 1R as configured.
Run: python research/signal_logic_audit.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")

import MetaTrader5 as mt5
mt5.initialize()
UTC_OFF = 3  # FBS server = UTC+3 (summer); rates come in server time


def h1_utc(symbol="XAUUSD", n=2500):
    r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, n)
    df = pd.DataFrame(r)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=UTC_OFF)
    return df.set_index("dt")


def d1_map(symbol="XAUUSD", sma_n=50):
    r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, sma_n + 40)
    d = pd.DataFrame(r)
    d["dt"] = pd.to_datetime(d["time"], unit="s", utc=True) - pd.Timedelta(hours=UTC_OFF)
    d = d.set_index("dt")
    dc = d["close"]; pc = dc.shift(1); sma = dc.rolling(sma_n).mean().shift(1)
    return {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d.index}


def z_sim_stream(H1, dmap, N=20, ema_n=100, mult=3.0):
    """Replica of the live ZRev logic (dual filter + ATR-stop backstop). Returns event list."""
    O = H1["open"].values; Hi = H1["high"].values; Lo = H1["low"].values
    up = H1["high"].rolling(N).max().shift(1).values
    lo = H1["low"].rolling(N).min().shift(1).values
    emaS = H1["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H1["close"] > emaS).shift(1).values
    tr = pd.concat([H1["high"] - H1["low"], (H1["high"] - H1["close"].shift()).abs(),
                    (H1["low"] - H1["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().shift(1).values
    dates = H1.index.date; idx = H1.index
    ev = []; pos = 0; ep = astop = None
    for i in range(len(H1)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(atr[i]) or (
                isinstance(h1_up[i], float) and np.isnan(h1_up[i])):
            continue
        dt = dmap.get(dates[i], 0)
        can_long = bool(h1_up[i]) and dt == 1
        can_short = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            if Hi[i] >= up[i] and can_long:
                pos, ep = 1, max(O[i], up[i]); astop = ep - mult * atr[i]
                ev.append((idx[i], "ENTRY LONG", round(ep, 2)))
            elif Lo[i] <= lo[i] and can_short:
                pos, ep = -1, min(O[i], lo[i]); astop = ep + mult * atr[i]
                ev.append((idx[i], "ENTRY SHORT", round(ep, 2)))
            continue
        if pos == 1:
            stop = max(astop, lo[i])
            if Lo[i] <= stop:
                ev.append((idx[i], "EXIT LONG", round(min(O[i], stop), 2))); pos = 0
        else:
            stop = min(astop, up[i])
            if Hi[i] >= stop:
                ev.append((idx[i], "EXIT SHORT", round(max(O[i], stop), 2))); pos = 0
    return ev, pos


print("=== (A) Z PARITY: sim events (last 20 days, MT5 data) vs ACTUAL magic 920622 ===")
H1 = h1_utc(); dmap = d1_map()
ev, end_pos = z_sim_stream(H1, dmap)
cut = pd.Timestamp.utcnow() - pd.Timedelta(days=20)
recent = [e for e in ev if e[0] >= cut]
for e in recent:
    print(f"  SIM  {e[0]:%m-%d %H:%M}  {e[1]:11s} @ {e[2]}")
print(f"  SIM end-state: {'LONG' if end_pos==1 else 'SHORT' if end_pos==-1 else 'FLAT'}")
frm = datetime.now(timezone.utc) - timedelta(days=20)
deals = [d for d in (mt5.history_deals_get(frm, datetime.now(timezone.utc) + timedelta(hours=2)) or [])
         if d.magic == 920622]
for d in deals:
    t = datetime.fromtimestamp(d.time, timezone.utc)
    kind = "ENTRY" if d.entry == 0 else "EXIT"
    side = "BUY" if d.type == 0 else "SELL"
    print(f"  ACT  {t:%m-%d %H:%M}  {kind} {side:5s} @ {d.price}  pnl={d.profit:+.2f}")
poss = [p for p in (mt5.positions_get() or []) if p.magic == 920622]
print(f"  ACT end-state: {'LONG' if any(p.type==0 for p in poss) else 'SHORT' if poss else 'FLAT'}")

print("\n=== (C) ORB STRUCTURE CHECK: recent US100 trades vs recomputed opening range ===")
frm2 = datetime.now(timezone.utc) - timedelta(days=10)
odeals = [d for d in (mt5.history_deals_get(frm2, datetime.now(timezone.utc) + timedelta(hours=2)) or [])
          if d.magic == 920617 and d.entry == 0]
oorders = {o.position_id: o for o in (mt5.history_orders_get(frm2, datetime.now(timezone.utc) + timedelta(hours=2)) or [])
           if o.magic == 920617 and o.sl > 0}
for d in odeals:
    t = datetime.fromtimestamp(d.time, timezone.utc)
    day0 = t.replace(hour=13, minute=30, second=0, microsecond=0)   # NY open 13:30 UTC (summer DST)
    # 13:30..13:59 INCLUSIVE — do NOT include the 14:00 bar (the breakout bar itself would
    # contaminate the boundary; that mistake produced false MISMATCH flags on the first run).
    r = mt5.copy_rates_range("US100", mt5.TIMEFRAME_M1,
                             day0 + timedelta(hours=UTC_OFF), day0 + timedelta(minutes=29, hours=UTC_OFF))
    if r is None or len(r) == 0:
        print(f"  {t:%m-%d %H:%M} no M1 range data"); continue
    rr = pd.DataFrame(r)
    rh, rl = rr["high"].max(), rr["low"].min(); rng = rh - rl
    side = "BUY" if d.type == 0 else "SELL"
    lvl = rh if side == "BUY" else rl
    o = oorders.get(d.position_id)
    sl_dist = abs(d.price - o.sl) if o else float("nan")
    ok_e = abs(d.price - lvl) <= max(3.0, rng * 0.02)
    ok_r = (abs(sl_dist - rng) <= rng * 0.15) if o else None
    print(f"  {t:%m-%d %H:%M} {side} @ {d.price}  range[{rl:.1f},{rh:.1f}] size={rng:.1f} "
          f"break_lvl={lvl:.1f} -> entry@level:{'OK' if ok_e else 'MISMATCH'}"
          + (f"  SLdist={sl_dist:.1f} vs range -> {'OK' if ok_r else 'MISMATCH'}" if o else ""))
mt5.shutdown()

print("\n=== (B) Z PARAMETER PLATEAU re-verify (duckdb 2021-2026, OOS PF per cell) ===")
from zrev_overextend import sim
from zrev_dual_trend import daily_map as dmap_hist
from audit_live_strategies import stats, split
DM = dmap_hist(50)
print(f"  {'cell':24s} {'OOSpf':>6} {'net':>7} {'maxDD':>7}")
best = None
for N in (15, 20, 25, 30):
    for ema in (50, 100, 150):
        trades, _ = sim(K=None, N=N, ema_n=ema, dmap=DM)
        items = [(t[1], t[3]) for t in trades]
        if len(items) < 50:
            continue
        _, o = split(items)
        pnl = np.array([p for _, p in items]); eq = np.cumsum(pnl)
        dd = (eq - np.maximum.accumulate(eq)).min()
        pf = stats(o)["pf"]
        tag = f"N={N} ema={ema}" + ("  <== DEPLOYED" if (N == 20 and ema == 100) else "")
        print(f"  {tag:24s} {pf:>6.2f} {pnl.sum():>+7.0f} {dd:>+7.0f}")
        if best is None or pf > best[0]:
            best = (pf, N, ema)
print(f"  best cell: N={best[1]} ema={best[2]} OOSpf={best[0]:.2f}")
print("\nDONE")
