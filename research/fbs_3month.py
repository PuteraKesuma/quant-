"""BACKTEST ON BROKER (FBS/MT5) DATA — the book we actually execute, on the quotes we actually get.

Why this matters: every backtest so far used Dukascopy. We trade FBS. Probe (scratchpad) showed FBS
1-min returns correlate 0.9976 (XAU) / 0.9704 (US100) with Dukascopy and the level gap is a constant
broker quote offset -> the edge SHOULD transfer, but "should" is not "does". This measures it.

FBS depth (terminal maxbars=100,000): M1 only ~100d, but H1/D1 go back to 1996 (XAU) / 2013 (US100).
Z is an H1 + daily-gate strategy -> it does NOT need M1. So Z is run straight off FBS H1+D1.
ORB needs M1 (30-min opening range) -> limited to the ~3-month M1 window.

Two windows:
  A) OVERLAP  2026-04-07..2026-06-25 : FBS vs Dukascopy, SAME logic -> parity check.
  B) FBS-ONLY 2026-06-26..2026-07-16 : Dukascopy ends 06-26, so this is FRESH out-of-sample.

MT5 bar times are BROKER server time (+3h); converted to true UTC exactly like pipeline/live/data.py.
NOTE pipeline/fetch/mt5_fetcher.py does NOT apply that offset -- do not reuse it for session logic.
"""
import os, sys, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
import MetaTrader5 as mt5
from audit_live_strategies import load_m1, to_d1
from pipeline.backtest.strategy_zrev import resample_1h
from regime_fix import adx_daily, orb_usd, daily

ENTRY_N = EXIT_N = 20; EMA = 100; DAILY_SMA = 50; ATR_MULT = 3.0; COST = 0.30; ADX_GATE = 28
LOT_MIN, LOT_MAX, Z_LO, Z_HI = 0.01, 0.02, 0.5, 1.0
W_A = ("2026-04-07", "2026-06-25")      # overlap: FBS vs Duka
W_B = ("2026-06-26", "2026-07-16")      # FBS only: fresh OOS


def z_usd_from_bars(h, d1, adx_min=ADX_GATE):
    """EXACT logic of regime_fix.accurate_z_usd, but taking H1 + D1 bars directly instead of
    resampling M1 -- so it can run on FBS H1/D1 (deep) without FBS M1 (shallow)."""
    c = h["close"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"]-h["low"], (h["high"]-c.shift()).abs(), (h["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtr = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dtd = {ts.date(): (0 if pd.isna(dtr.loc[ts]) else int(dtr.loc[ts])) for ts in d1.index}
    adx = adx_daily(d1).shift(1)
    adxd = {ts.date(): (0.0 if pd.isna(adx.loc[ts]) else float(adx.loc[ts])) for ts in d1.index}
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
    idx = h.index; C, H, L, O = c.values, h["high"].values, h["low"].values, h["open"].values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    m20, s20 = ma20.values, sd20.values
    dtl = [dtd.get(idx[i].date(), 0) for i in range(len(idx))]
    axl = [adxd.get(idx[i].date(), 0.0) for i in range(len(idx))]

    def dl(i, d):
        if s20[i] <= 0 or np.isnan(s20[i]): return LOT_MIN
        z = (C[i]-m20[i])/s20[i]; zd = z if d == 1 else -z
        f = max(0., min(1., (zd-Z_LO)/max(Z_HI-Z_LO, 1e-9)))
        return round(max(LOT_MIN, min(LOT_MAX, round((LOT_MIN+f*(LOT_MAX-LOT_MIN))/0.01)*0.01)), 2)

    pos = 0; ep = sl = elot = None; out = []
    for i in range(1, len(idx)):
        if np.isnan(upv[i]) or np.isnan(emv[i]) or np.isnan(atv[i]): continue
        ut = C[i-1] > emv[i]; dd = dtl[i]; strong = axl[i] >= adx_min
        cl = ut and dd == 1 and strong; cs = (not ut) and dd == -1 and strong
        if pos == 0:
            if H[i] >= upv[i] and cl: pos, ep, sl, elot = 1, max(O[i], upv[i]), max(O[i], upv[i])-ATR_MULT*atv[i], dl(i, 1)
            elif L[i] <= dnv[i] and cs: pos, ep, sl, elot = -1, min(O[i], dnv[i]), min(O[i], dnv[i])+ATR_MULT*atv[i], dl(i, -1)
            continue
        if pos == 1:
            sl = max(sl, C[i-1]-ATR_MULT*atv[i])
            if L[i] <= sl: out.append((idx[i], ((min(O[i], sl)-ep)-COST)*elot*100)); pos = 0
            elif L[i] <= xdv[i]:
                out.append((idx[i], ((min(O[i], xdv[i])-ep)-COST)*elot*100))
                if L[i] <= dnv[i] and cs: pos, ep, sl, elot = -1, min(O[i], dnv[i]), min(O[i], dnv[i])+ATR_MULT*atv[i], dl(i, -1)
                else: pos = 0
        else:
            sl = min(sl, C[i-1]+ATR_MULT*atv[i])
            if H[i] >= sl: out.append((idx[i], ((ep-max(O[i], sl))-COST)*elot*100)); pos = 0
            elif H[i] >= xuv[i]:
                out.append((idx[i], ((ep-max(O[i], xuv[i]))-COST)*elot*100))
                if H[i] >= upv[i] and cl: pos, ep, sl, elot = 1, max(O[i], upv[i]), max(O[i], upv[i])-ATR_MULT*atv[i], dl(i, 1)
                else: pos = 0
    return out


def orb_usd_ext(m1, d1, rmin=30, be=0.5, send=20*60, cost=2.0):
    """regime_fix.orb_usd, but the daily-SMA50 trend gate comes from an EXTERNAL (deep) D1 series.
    orb_usd derives it from the M1 it is given -> on FBS's ~100-day M1 the 50-day SMA is NaN for the
    first ~50 sessions and every one of them is skipped (td==0), which looked like 'FBS has no ORB
    signals' when it was really a warm-up artifact of the test. Deep D1 fixes it."""
    from zoneinfo import ZoneInfo
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
        tp = en+d*sz; slv = en-d*sz; risk = sz; cr = cost/risk; armed = False; pnl = None; xi = None
        for j in range(ei, b):
            if mod[j] >= send: pnl = d*(C[j]-en)/risk-cr; xi = j; break
            if d == 1:
                if not armed and (H[j]-en) >= be*risk: armed = True
                if armed and L[j] <= en: pnl = -cr; xi = j; break
                if L[j] <= slv: pnl = -1-cr; xi = j; break
                if H[j] >= tp: pnl = 1-cr; xi = j; break
            else:
                if not armed and (en-L[j]) >= be*risk: armed = True
                if armed and H[j] >= en: pnl = -cr; xi = j; break
                if H[j] >= slv: pnl = -1-cr; xi = j; break
                if L[j] <= tp: pnl = 1-cr; xi = j; break
        if pnl is None: pnl = d*(C[b-1]-en)/risk-cr; xi = b-1
        out.append((m1.index[xi], pnl*risk*0.10))
    return out


def rsi(c, n=2):
    d = c.diff(); u = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100-100/(1+u/dn.replace(0, np.nan))


def reversal_from_d1(close, dpp=0.10):
    r2 = rsi(close, 2); s200 = close.rolling(200).mean(); s5 = close.rolling(5).mean()
    out = []; ip = False; ei = None
    for i in range(1, len(close)):
        if not ip and r2.iloc[i] < 10 and close.iloc[i] > s200.iloc[i]: ip = True; ei = i
        elif ip and close.iloc[i] > s5.iloc[i]:
            out.append((close.index[i], (close.iloc[i]-close.iloc[ei])*dpp)); ip = False
    return out


# ---------------- FBS data (importable helpers; caller must mt5.initialize() first) ----------------
def server_offset():
    """Broker server time minus UTC, whole hours (FBS = +3 summer). Same idea as
    pipeline/live/data.py::_server_offset_hours. MT5 bar times are SERVER time, not UTC."""
    tick = mt5.symbol_info_tick("XAUUSD")
    return round((pd.Timestamp(tick.time, unit="s", tz="UTC") - pd.Timestamp.utcnow()).total_seconds()/3600.0)


def fbs_bars(sym, tf, off, n=99_999):
    """MT5 bars -> DataFrame indexed by TRUE UTC. NOTE pipeline/fetch/mt5_fetcher.py omits this shift."""
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) == 0:
        raise RuntimeError(f"no bars for {sym}: {mt5.last_error()}")
    df = pd.DataFrame(r)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=off)
    return df.set_index("ts")[["open", "high", "low", "close"]].sort_index()


if not mt5.initialize(): print("MT5 init fail", mt5.last_error()); raise SystemExit
OFF = server_offset()


def fbs(sym, tf, n=99_999):
    return fbs_bars(sym, tf, OFF, n)


xau_h1, xau_d1 = fbs("XAUUSD", mt5.TIMEFRAME_H1), fbs("XAUUSD", mt5.TIMEFRAME_D1)
nas_m1 = fbs("US100", mt5.TIMEFRAME_M1)
nas_d1 = fbs("US100", mt5.TIMEFRAME_D1)
xau_m1_fbs = fbs("XAUUSD", mt5.TIMEFRAME_M1)
for s in ("XAUUSD", "US100"):
    i = mt5.symbol_info(s)
    print(f"spec {s:7} point={i.point} tick_size={i.trade_tick_size} tick_val=${i.trade_tick_value} "
          f"contract={i.trade_contract_size}")
mt5.shutdown()

# ---------------- Dukascopy data ----------------
duka_xau_m1 = load_m1("XAUUSD"); duka_nas_m1 = load_m1("NAS100")
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")


def win(items, a, b):
    lo, hi = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC") + pd.Timedelta(days=1)
    return [(t, p) for t, p in items if lo <= t < hi]


def show(tag, z, o, r):
    tot = sum(p for _, p in z) + sum(p for _, p in o) + sum(p for _, p in r)
    print(f"  {tag:<26} Z {len(z):>3}trd {sum(p for _,p in z):>+8.2f} | "
          f"ORB {len(o):>3}trd {sum(p for _,p in o):>+8.2f} | "
          f"Rev {len(r):>2}trd {sum(p for _,p in r):>+7.2f} | TOTAL {tot:>+8.2f}")
    return tot


# FBS book
z_fbs_all = z_usd_from_bars(xau_h1, xau_d1)
o_fbs_all = orb_usd_ext(nas_m1, nas_d1)                       # deep FBS D1 -> gate is warmed up
r_fbs_all = reversal_from_d1(nas_d1["close"])
# Dukascopy book (same external-D1 treatment so the comparison is apples-to-apples)
z_duk_all = z_usd_from_bars(resample_1h(duka_xau_m1.assign(volume=0)), to_d1(duka_xau_m1))
o_duk_all = orb_usd_ext(duka_nas_m1, to_d1(duka_nas_m1))
r_duk_all = reversal_from_d1(px["NAS100"].dropna())

print("\n" + "=" * 100)
print(f"A) OVERLAP {W_A[0]} .. {W_A[1]}   — FBS vs Dukascopy, identical logic (parity check)")
print("=" * 100)
t_fbs = show("FBS (broker data)", win(z_fbs_all, *W_A), win(o_fbs_all, *W_A), win(r_fbs_all, *W_A))
t_duk = show("Dukascopy (our backtest)", win(z_duk_all, *W_A), win(o_duk_all, *W_A), win(r_duk_all, *W_A))
print(f"  {'DELTA':<26} {t_fbs - t_duk:>+8.2f}  ({'FBS better' if t_fbs > t_duk else 'FBS worse'})")

print("\n" + "=" * 100)
print(f"B) FRESH OOS {W_B[0]} .. {W_B[1]}  — FBS only (Dukascopy ends 06-26; never tested before)")
print("=" * 100)
show("FBS (broker data)", win(z_fbs_all, *W_B), win(o_fbs_all, *W_B), win(r_fbs_all, *W_B))

print("\n" + "=" * 100)
print(f"C) FULL FBS M1 span {xau_m1_fbs.index[0]:%Y-%m-%d} .. {nas_m1.index[-1]:%Y-%m-%d}")
print("=" * 100)
show("FBS whole ~3.4 months", win(z_fbs_all, W_A[0], W_B[1]), win(o_fbs_all, W_A[0], W_B[1]),
     win(r_fbs_all, W_A[0], W_B[1]))
# ---------------- D) per-month ----------------
def mser(items):
    if not items: return pd.Series(dtype=float)
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC")).sort_index()
    return s.groupby(s.index.to_period("M")).sum()


print("\n" + "=" * 100)
print("D) FBS book PER CALENDAR MONTH (0.01 lot) — 'apakah tiap bulan profit?'")
print("=" * 100)
zm, om_, rm_ = mser(z_fbs_all), mser(o_fbs_all), mser(r_fbs_all)
mons = sorted({m for s in (zm, om_, rm_) for m in s.index if str(m) >= "2026-04"})
print(f"{'bulan':<10}{'Z$':>10}{'ORB$':>10}{'Rev$':>10}{'TOTAL$':>11}   status")
for m in mons:
    a, b, c2 = float(zm.get(m, 0)), float(om_.get(m, 0)), float(rm_.get(m, 0))
    t = a + b + c2
    note = "  (parsial, s/d 16 Jul)" if str(m) == "2026-07" else ""
    print(f"{str(m):<10}{a:>+10.2f}{b:>+10.2f}{c2:>+10.2f}{t:>+11.2f}   {'HIJAU' if t > 0 else 'MERAH'}{note}")

# ---------------- E) FBS M1 data quality (why ORB fired 26x vs Dukascopy 33x) ----------------
from zoneinfo import ZoneInfo


def open_range_bars(m1, a, b, rmin=30):
    """How many M1 bars each session actually has inside the NY opening range. orb_usd needs >= rmin//2."""
    lo, hi = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC") + pd.Timedelta(days=1)
    x = m1.loc[lo:hi]
    res = {}
    for d, g in x.groupby(x.index.date):
        et = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo("America/New_York"))
        om = (13 * 60 + 30) + (60 if et.dst() == dt.timedelta(0) else 0)
        md = g.index.hour.values * 60 + g.index.minute.values
        res[d] = int(((md >= om) & (md < om + rmin)).sum())
    return res


print("\n" + "=" * 100)
print("E) FBS M1 DATA QUALITY — is the broker feed complete? (ORB 26 vs Duka 33 sessions)")
print("=" * 100)
f_ok = open_range_bars(nas_m1, *W_A); d_ok = open_range_bars(duka_nas_m1, *W_A)
days = sorted(set(f_ok) | set(d_ok))
fbs_bad = [d for d in days if f_ok.get(d, 0) < 15 and d_ok.get(d, 0) >= 15]
duk_bad = [d for d in days if d_ok.get(d, 0) < 15 and f_ok.get(d, 0) >= 15]
bpd_f = nas_m1.loc[W_A[0]:W_A[1]].groupby(nas_m1.loc[W_A[0]:W_A[1]].index.date).size()
bpd_d = duka_nas_m1.loc[W_A[0]:W_A[1]].groupby(duka_nas_m1.loc[W_A[0]:W_A[1]].index.date).size()
print(f"  US100 bars/day  FBS: median {bpd_f.median():.0f} | min {bpd_f.min()} | days {len(bpd_f)}")
print(f"  US100 bars/day  Duka: median {bpd_d.median():.0f} | min {bpd_d.min()} | days {len(bpd_d)}")
print(f"  sessions with a USABLE opening range: FBS {sum(1 for d in days if f_ok.get(d,0)>=15)} | "
      f"Duka {sum(1 for d in days if d_ok.get(d,0)>=15)}")
print(f"  FBS MISSING the range but Duka has it: {len(fbs_bad)} day(s) -> "
      f"{', '.join(str(d) for d in fbs_bad[:12])}{' ...' if len(fbs_bad) > 12 else ''}")
print(f"  Duka missing but FBS has it        : {len(duk_bad)} day(s)")
if fbs_bad:
    print(f"  (bars FBS had on those days: {[f_ok.get(d,0) for d in fbs_bad[:12]]})")

print("\nCAVEAT: ~3 months is a TINY sample (Z ~35 trd/yr at gate 28 -> only ~5 Z trades here). This")
print("CANNOT validate the edge. It checks that the engine reproduces on broker quotes and that the")
print("cost model + session windows line up. Judge PARITY (delta small), NOT profit.")
print("NOT-YET-DONE (the real prize): FBS H1 reaches back to 1996 (XAU) / 2013 (US100), and Z is an")
print("H1+daily strategy -> z_usd_from_bars() above can run it on regimes our 2021-26 Dukascopy data")
print("has NEVER seen (2008 crisis, 2013 gold crash, 2013-15 bear). Verify old-bar quality first:")
print("broker history that far back is often backfilled/synthetic.")
print("DONE")
