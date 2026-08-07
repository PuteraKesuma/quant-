"""WHY does ORB lose? Decompose every exit, then test the prime suspect: the breakeven rule.

ORB is RR 1:1 (TP=1R, SL=1R) with `breakeven_r: 0.5` -> once +0.5R is touched, SL moves to entry.
Suspicion: in a 1:1 system that rule converts would-be winners into ~0R scratches (you pay the spread),
because price very often pokes +0.5R, retraces through entry, THEN runs to TP. Every BE exit is a
winner you gave back minus costs.

Sample discipline: FBS M1 is only ~3 months (36 trades) -> CANNOT decide anything. The parameter
question is answered on the FULL Dukascopy history (2021-2026), judged PER YEAR (a lever that only
helps in aggregate but not per-year is noise-fitting). FBS is used only as a consistency check.

Exit kinds (order matches the live logic in orb_stop_manager / regime_fix.orb_usd):
  BE   armed at +be*R, price returns to entry -> -cost
  SL   -1R - cost
  TP   +1R - cost
  TIME session_end close-out -> partial R
"""
import os, sys, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from zoneinfo import ZoneInfo
import MetaTrader5 as mt5
from audit_live_strategies import load_m1, to_d1


def orb_detail(m1, d1, be=0.5, rmin=30, send=20*60, cost=2.0):
    """regime_fix.orb_usd with an EXTERNAL deep D1 trend gate, instrumented with the exit kind."""
    sma = d1["close"].rolling(50).mean(); pc, ps = d1["close"].shift(1), sma.shift(1)
    tbd = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(ps.loc[ts]))
                       else (1 if pc.loc[ts] > ps.loc[ts] else -1)) for ts in d1.index}
    H, L, C = m1["high"].values, m1["low"].values, m1["close"].values
    mod = m1.index.hour.values*60+m1.index.minute.values; do = m1.index.normalize().asi8
    uq, st = np.unique(do, return_index=True); st = list(st)+[len(m1)]; out = []
    for di in range(len(uq)):
        a, b = st[di], st[di+1]; dd = m1.index[a].date()
        et = dt.datetime(dd.year, dd.month, dd.day, 12, tzinfo=ZoneInfo("America/New_York"))
        om = (13*60+30) + (60 if et.dst() == dt.timedelta(0) else 0)
        md = mod[a:b]; ix = np.arange(a, b)
        rm = (md >= om) & (md < om+rmin)
        if rm.sum() < rmin//2: continue
        ri = ix[rm]; oh, ol = H[ri].max(), L[ri].min(); sz = oh-ol
        if sz <= 0: continue
        pi = ix[md >= om+rmin]; ei = d = en = None
        for i in pi:
            if H[i] > oh: ei, d, en = i, 1, oh; break
            if L[i] < ol: ei, d, en = i, -1, ol; break
        if ei is None: continue
        td = tbd.get(dd, 0)
        if td == 0 or (td > 0) != (d == 1): continue
        tp = en+d*sz; slv = en-d*sz; risk = sz; cr = cost/risk
        armed = False; pnl = None; xi = None; kind = None
        for j in range(ei, b):
            if mod[j] >= send: pnl = d*(C[j]-en)/risk-cr; xi = j; kind = "TIME"; break
            if d == 1:
                if not armed and (H[j]-en) >= be*risk: armed = True
                if armed and L[j] <= en: pnl = -cr; xi = j; kind = "BE"; break
                if L[j] <= slv: pnl = -1-cr; xi = j; kind = "SL"; break
                if H[j] >= tp: pnl = 1-cr; xi = j; kind = "TP"; break
            else:
                if not armed and (en-L[j]) >= be*risk: armed = True
                if armed and H[j] >= en: pnl = -cr; xi = j; kind = "BE"; break
                if H[j] >= slv: pnl = -1-cr; xi = j; kind = "SL"; break
                if L[j] <= tp: pnl = 1-cr; xi = j; kind = "TP"; break
        if pnl is None: pnl = d*(C[b-1]-en)/risk-cr; xi = b-1; kind = "TIME"
        out.append(dict(ts=m1.index[xi], usd=pnl*risk*0.10, r=pnl, kind=kind, dir=d))
    return out


def decompose(label, tr):
    df = pd.DataFrame(tr)
    if df.empty: print(f"  {label}: no trades"); return
    print(f"\n  {label}: n={len(df)} | net ${df['usd'].sum():+.2f} | avg {df['r'].mean():+.3f}R")
    print(f"    {'kind':<6}{'n':>5}{'share':>8}{'net$':>10}{'avg R':>9}")
    for k in ("TP", "SL", "BE", "TIME"):
        g = df[df["kind"] == k]
        if len(g) == 0: continue
        print(f"    {k:<6}{len(g):>5}{len(g)/len(df):>8.0%}{g['usd'].sum():>+10.2f}{g['r'].mean():>+9.3f}")


duka_nas = load_m1("NAS100"); duka_d1 = to_d1(duka_nas)
print("=" * 84)
print("1) EXIT DECOMPOSITION — live setting be=0.5, full Dukascopy history 2021-2026")
print("=" * 84)
base = orb_detail(duka_nas, duka_d1, be=0.5)
decompose("Dukascopy 2021-2026", base)

# FBS consistency check
if mt5.initialize():
    tick = mt5.symbol_info_tick("XAUUSD")
    OFF = round((pd.Timestamp(tick.time, unit="s", tz="UTC") - pd.Timestamp.utcnow()).total_seconds()/3600.0)

    def fbs(sym, tf, n=99_999):
        mt5.symbol_select(sym, True); r = mt5.copy_rates_from_pos(sym, tf, 0, n)
        df = pd.DataFrame(r); df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=OFF)
        return df.set_index("ts")[["open", "high", "low", "close"]].sort_index()
    fbs_m1, fbs_d1 = fbs("US100", mt5.TIMEFRAME_M1), fbs("US100", mt5.TIMEFRAME_D1)
    mt5.shutdown()
    decompose("FBS broker ~3.4 months (consistency check only, n too small to decide)",
              orb_detail(fbs_m1, fbs_d1, be=0.5))
else:
    fbs_m1 = fbs_d1 = None
    print("  (MT5 unavailable -> skipping FBS check)")

print("\n" + "=" * 84)
print("2) BREAKEVEN SWEEP on FULL Dukascopy history — judged PER YEAR, not just in aggregate")
print("=" * 84)
print(f"{'be':>6}{'n':>6}{'net$':>10}{'avgR':>8}{'WR%':>7}{'PF':>7}  " + "".join(f"{y:>8}" for y in range(2021, 2027)) + "  green")
rows = []
for be in (0.0, 0.25, 0.5, 0.75, 1.0):
    tr = orb_detail(duka_nas, duka_d1, be=be)
    df = pd.DataFrame(tr); ts = pd.DatetimeIndex(df["ts"])
    g = df.loc[df["usd"] > 0, "usd"].sum(); l = -df.loc[df["usd"] < 0, "usd"].sum()
    yrs = {y: df.loc[ts.year == y, "usd"].sum() for y in range(2021, 2027)}
    ng = sum(1 for y in yrs if yrs[y] > 0)
    tag = " <- LIVE" if be == 0.5 else ""
    print(f"{be:>6.2f}{len(df):>6}{df['usd'].sum():>+10.2f}{df['r'].mean():>+8.3f}"
          f"{100*(df['usd'] > 0).mean():>7.1f}{(g/l if l else np.inf):>7.2f}  "
          + "".join(f"{yrs[y]:>+8.0f}" for y in range(2021, 2027)) + f"  {ng}/6{tag}")
    rows.append((be, df, yrs, ng))

if fbs_m1 is not None:
    print("\n  FBS broker data, same sweep (3.4 months — DIRECTION only, not proof):")
    print(f"  {'be':>6}{'n':>6}{'net$':>10}{'avgR':>8}")
    for be in (0.0, 0.25, 0.5, 0.75, 1.0):
        d2 = pd.DataFrame(orb_detail(fbs_m1, fbs_d1, be=be))
        print(f"  {be:>6.2f}{len(d2):>6}{d2['usd'].sum():>+10.2f}{d2['r'].mean():>+8.3f}"
              + ("  <- LIVE" if be == 0.5 else ""))

print("\nDECISION RULE (pre-registered, so this cannot be fitted after the fact): only propose a change")
print("if a setting beats be=0.50 on (a) net, (b) avgR, AND (c) green-years >= live, on the FULL")
print("Dukascopy history, AND points the same way on FBS. Otherwise the honest answer is: leave it.")
print("DONE")
