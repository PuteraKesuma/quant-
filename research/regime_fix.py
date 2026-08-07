"""REGIME FIX: add an ADX trend-strength gate to Z so it stops bleeding in choppy-gold 2021, without
killing the 2024-26 trend profit. Goal: green 6/6 years + tolerable DD. ORB/Reversal unchanged (already
robust). No-lookahead: gate uses the PREVIOUS completed daily ADX. Correct peak-to-trough $ DD this time.
"""
import os, sys, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from pipeline.backtest.strategy_zrev import resample_1h
from zoneinfo import ZoneInfo

ENTRY_N = EXIT_N = 20; EMA = 100; DAILY_SMA = 50; ATR_MULT = 3.0; COST = 0.30
LOT_MIN, LOT_MAX, Z_LO, Z_HI = 0.01, 0.02, 0.5, 1.0
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")


def adx_daily(d1, n=14):
    h, l, c = d1["high"], d1["low"], d1["close"]
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100*pd.Series(plus, index=d1.index).ewm(alpha=1/n, adjust=False).mean()/atr
    mdi = 100*pd.Series(minus, index=d1.index).ewm(alpha=1/n, adjust=False).mean()/atr
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()


def accurate_z_usd(m1, adx_min=0.0):
    h = resample_1h(m1.assign(volume=0)); c = h["close"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"]-h["low"], (h["high"]-c.shift()).abs(), (h["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    d1 = to_d1(m1); dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtr = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dtd = {ts.date(): (0 if pd.isna(dtr.loc[ts]) else int(dtr.loc[ts])) for ts in d1.index}
    adx = adx_daily(d1).shift(1)                     # previous completed day -> no lookahead
    adxd = {ts.date(): (0.0 if pd.isna(adx.loc[ts]) else float(adx.loc[ts])) for ts in d1.index}
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
    idx = h.index; C, H, L, O = c.values, h["high"].values, h["low"].values, h["open"].values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    m20, s20 = ma20.values, sd20.values
    dtl = [dtd.get(idx[i].date(), 0) for i in range(len(idx))]
    axl = [adxd.get(idx[i].date(), 0.0) for i in range(len(idx))]
    def dl(i, d):
        if s20[i] <= 0 or np.isnan(s20[i]): return LOT_MIN
        z = (C[i]-m20[i])/s20[i]; zd = z if d == 1 else -z; f = max(0., min(1., (zd-Z_LO)/max(Z_HI-Z_LO, 1e-9)))
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


def _nas_open_min(d):
    et = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return (13*60+30) + (60 if et.dst() == dt.timedelta(0) else 0)

def orb_usd(m1, rmin=30, be=0.5, send=20*60, cost=2.0):
    d1 = to_d1(m1); sma = d1["close"].rolling(50).mean(); pc, ps = d1["close"].shift(1), sma.shift(1)
    tbd = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(ps.loc[ts])) else (1 if pc.loc[ts] > ps.loc[ts] else -1)) for ts in d1.index}
    H, L, C = m1["high"].values, m1["low"].values, m1["close"].values
    mod = m1.index.hour.values*60+m1.index.minute.values; do = m1.index.normalize().asi8
    uq, st = np.unique(do, return_index=True); st = list(st)+[len(m1)]; out = []
    for di in range(len(uq)):
        a, b = st[di], st[di+1]; dd = m1.index[a].date(); om = _nas_open_min(dd); md = mod[a:b]; ix = np.arange(a, b)
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
    dd = c.diff(); u = dd.clip(lower=0).rolling(n).mean(); d = (-dd.clip(upper=0)).rolling(n).mean()
    return 100-100/(1+u/d.replace(0, np.nan))

def reversal_usd(name):
    c = px[name].dropna(); r2 = rsi(c, 2); s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    out = []; ip = False; ei = None
    for i in range(1, len(c)):
        if not ip and r2.iloc[i] < 10 and c.iloc[i] > s200.iloc[i]: ip = True; ei = i
        elif ip and c.iloc[i] > s5.iloc[i]:
            out.append((c.index[i], (c.iloc[i]-c.iloc[ei])*0.10)); ip = False
    return out


def daily(items):
    if not items: return pd.Series(dtype=float)
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC")).sort_index()
    return s.groupby(s.index.normalize()).sum()

def dd_dollar(series):                                # worst peak-to-trough in $ on a running curve
    eq = series.cumsum(); return float((eq - eq.cummax()).min())


xau = load_m1("XAUUSD"); nas = load_m1("NAS100")
otr = orb_usd(nas); rtr = reversal_usd("NAS100"); O, R = daily(otr), daily(rtr)

print("="*76)
print("Z ADX-GATE SCAN — per-year Z$ (find gate that flips 2021 green, keeps 2024-26)")
print("="*76)
print(f"{'adx_min':>8}  " + "".join(f"{y:>9}" for y in range(2021, 2027)) + f"{'TOTAL':>9}{'trades':>8}")
best = None
zcache = {}
for g in [0, 15, 18, 20, 22, 25, 28]:
    ztr = accurate_z_usd(xau, adx_min=g); zcache[g] = ztr
    Z = daily(ztr); yr = {y: Z[Z.index.year == y].sum() for y in range(2021, 2027)}
    row = "".join(f"{yr[y]:>+9.0f}" for y in range(2021, 2027))
    print(f"{g:>8}  {row}{Z.sum():>+9.0f}{len(ztr):>8}")

print("\n" + "="*76)
print("COMBINED BOOK per-year with chosen Z gate (ORB+Reversal unchanged). Correct $ DD.")
print("="*76)

def report(gate, label):
    ztr = zcache[gate]; Z = daily(ztr)
    lo = max(Z.index.min(), O.index.min()); hi = min(Z.index.max(), O.index.max(), R.index.max())
    cal = pd.date_range(lo, hi, freq="B", tz="UTC")
    Zc, Oc, Rc = Z.reindex(cal).fillna(0), O.reindex(cal).fillna(0), R.reindex(cal).fillna(0)
    book = Zc+Oc+Rc
    print(f"\n--- {label} (Z adx_min={gate}) ---")
    print(f"{'Year':6}{'Book$':>9}{'Sharpe':>8}{'PF':>7}{'DD$':>8}   {'Z$':>7}{'ORB$':>7}{'Rev$':>7}  verdict")
    worst_dd = 0.0
    allt = ztr+otr+rtr
    for y in range(2021, 2027):
        m = cal.year == y; bk = book[m]
        if bk.abs().sum() == 0: continue
        sh = bk.mean()/bk.std()*np.sqrt(252) if bk.std() > 0 else np.nan
        ddy = dd_dollar(bk); worst_dd = min(worst_dd, ddy)
        t = [p for ts, p in allt if ts.year == y]; gg = sum(x for x in t if x > 0); bb = -sum(x for x in t if x < 0)
        pf = gg/bb if bb > 0 else np.inf
        v = "GREEN" if bk.sum() > 0 else "RED"
        print(f"{y:<6}{bk.sum():>+9.0f}{sh:>8.2f}{pf:>7.2f}{ddy:>8.0f}   {Zc[m].sum():>+7.0f}{Oc[m].sum():>+7.0f}{Rc[m].sum():>+7.0f}  {v}")
    sh = book.mean()/book.std()*np.sqrt(252); g_ = sum(x for _, x in allt if x > 0); b_ = -sum(x for _, x in allt if x < 0)
    greens = sum(1 for y in range(2021, 2027) if book[cal.year == y].abs().sum() > 0 and book[cal.year == y].sum() > 0)
    tots = sum(1 for y in range(2021, 2027) if book[cal.year == y].abs().sum() > 0)
    full_dd = dd_dollar(book)
    print(f"{'ALL':<6}{book.sum():>+9.0f}{sh:>8.2f}{g_/b_:>7.2f}{full_dd:>8.0f}")
    print(f"  green {greens}/{tots} | full Sharpe {sh:.2f} PF {g_/b_:.2f} | worst-year $DD {worst_dd:.0f} | full $DD {full_dd:.0f}")
    print(f"  => for 10-12% annual DD you need equity ~${abs(worst_dd)/0.12:.0f}-${abs(worst_dd)/0.10:.0f} (worst-year DD / target%)")
    return book, cal

report(0, "BEFORE (no gate, current live)")
# choose the smallest gate that makes 2021 >= 0 with best total
best_gate = None
for g in [18, 20, 22, 25, 28]:
    Z = daily(zcache[g]); y21 = Z[Z.index.year == 2021].sum()
    if y21 >= 0: best_gate = g; break
if best_gate is None: best_gate = 25
report(best_gate, f"AFTER (ADX gate {best_gate})")
print("\nDONE")
