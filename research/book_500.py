"""BLIND-SPOT HUNT for a $500 account. Real finding: gold (XAU) at 0.01 lot = $1/$ and gold is $4029 now
-> 3xATR stop ~$120-180/trade = 24-36% of $500 PER TRADE. Gold is the DD hog, NOT the strategy. The NAS
sleeves (ORB $0.10/pt + Reversal) are tiny-dollar and $500-appropriate. Build a NAS-centric $500 book and
measure real DD% + return. Add extra cheap index-reversal sleeves (any index in the daily parquet) to lift
return WITHOUT blowing DD. True FBS tick values. No-lookahead. Correct peak-to-trough $ DD.
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
EQ = 500.0


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


def accurate_z_usd(m1, adx_min=28.0):
    h = resample_1h(m1.assign(volume=0)); c = h["close"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"]-h["low"], (h["high"]-c.shift()).abs(), (h["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    d1 = to_d1(m1); dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtr = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dtd = {ts.date(): (0 if pd.isna(dtr.loc[ts]) else int(dtr.loc[ts])) for ts in d1.index}
    adx = adx_daily(d1).shift(1); adxd = {ts.date(): (0.0 if pd.isna(adx.loc[ts]) else float(adx.loc[ts])) for ts in d1.index}
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
    idx = h.index; C, H, L, O = c.values, h["high"].values, h["low"].values, h["open"].values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    m20, s20 = ma20.values, sd20.values
    dtl = [dtd.get(idx[i].date(), 0) for i in range(len(idx))]; axl = [adxd.get(idx[i].date(), 0.0) for i in range(len(idx))]
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

def reversal_usd(name, dpp=0.10):     # $ per index point at 0.01 lot (US100/US500/US30 all $0.10/pt on FBS)
    c = px[name].dropna(); r2 = rsi(c, 2); s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    out = []; ip = False; ei = None
    for i in range(1, len(c)):
        if not ip and r2.iloc[i] < 10 and c.iloc[i] > s200.iloc[i]: ip = True; ei = i
        elif ip and c.iloc[i] > s5.iloc[i]:
            out.append((c.index[i], (c.iloc[i]-c.iloc[ei])*dpp)); ip = False
    return out


def daily(items):
    if not items: return pd.Series(dtype=float)
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC")).sort_index()
    return s.groupby(s.index.normalize()).sum()

def combine(series_list):
    lo = max(s.index.min() for s in series_list if len(s)); hi = min(s.index.max() for s in series_list if len(s))
    cal = pd.date_range(lo, hi, freq="B", tz="UTC")
    return sum(s.reindex(cal).fillna(0) for s in series_list), cal

def worst_year_dd(book, cal):
    w = 0.0
    for y in sorted(set(cal.year)):
        bk = book[cal.year == y]
        if bk.abs().sum() == 0: continue
        eq = bk.cumsum(); w = min(w, float((eq-eq.cummax()).min()))
    return w

def summarize(name, series_list, eq=EQ):
    book, cal = combine(series_list)
    years = [y for y in sorted(set(cal.year)) if book[cal.year == y].abs().sum() > 0]
    peryr = {y: book[cal.year == y].sum() for y in years}
    greens = sum(1 for y in years if peryr[y] > 0)
    wdd = worst_year_dd(book, cal); full_dd = float((book.cumsum()-book.cumsum().cummax()).min())
    n_yr = len(years); tot = book.sum(); avg_yr = tot/n_yr
    sh = book.mean()/book.std()*np.sqrt(252) if book.std() > 0 else np.nan
    print(f"\n### {name}")
    print("   " + "  ".join(f"{y}:{peryr[y]:+.0f}" for y in years))
    print(f"   green {greens}/{n_yr} | Sharpe {sh:.2f} | total ${tot:+.0f} over {n_yr}y = ${avg_yr:+.0f}/yr")
    print(f"   worst-year DD ${wdd:.0f} = {wdd/eq:+.0%} on ${eq:.0f}  |  avg return {avg_yr/eq:+.0%}/yr on ${eq:.0f}")
    print(f"   VERDICT: {'PASS (DD<=12%)' if abs(wdd)/eq <= 0.12 else 'DD too high' if greens==n_yr else 'not all-green'}")
    return book, cal

# candidate index symbols in the daily parquet for extra cheap reversal sleeves
idx_syms = [s for s in ["NAS100", "US500", "SPX", "SP500", "US30", "DJI", "GER40", "DAX", "UK100"] if s in px.columns]
print("Index symbols available in daily parquet:", idx_syms)

xau = load_m1("XAUUSD"); nas = load_m1("NAS100")
Z = daily(accurate_z_usd(xau, adx_min=28)); O = daily(orb_usd(nas)); Rn = daily(reversal_usd("NAS100"))

print("="*78); print(f"BOOKS ON ${EQ:.0f} EQUITY (0.01 lot, true FBS tick values, 2021-2026)"); print("="*78)
summarize("A) FULL book WITH gold (Z-gated + ORB + Rev-NAS)  <- current design", [Z, O, Rn])
summarize("B) $500-SAFE: NAS only (ORB + Rev-NAS), NO GOLD", [O, Rn])

# C) add extra uncorrelated cheap index-reversal sleeves (all $0.10/pt) to lift return
extra = []
for s in idx_syms:
    if s == "NAS100": continue
    r = daily(reversal_usd(s))
    if len(r): extra.append((s, r))
if extra:
    books = [O, Rn] + [r for _, r in extra]
    summarize(f"C) $500-SAFE stacked: ORB + Rev-NAS + Rev{[s for s,_ in extra]}", books)

print("\nDONE")
