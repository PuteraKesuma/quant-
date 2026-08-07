"""SL-WIDTH SWEEP + $500 monthly-return reality check for the live 3-sleeve book.
User hypothesis: "logic SL jangan lebar" (stops too wide). Test empirically per sleeve, and translate
to a $500 account. HONEST: at min-lot 0.01 the $ P/L is FIXED -> $500 vs $1000 only changes %/DD/ruin-risk.
Backtest on real M1 (XAU/NAS) + daily parquet. Sharpe = annualized on business-day-aligned daily P/L.
"""
import os, sys, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from pipeline.backtest.strategy_zrev import resample_1h
from zoneinfo import ZoneInfo

ENTRY_N = EXIT_N = 20; EMA = 100; DAILY_SMA = 50; COST = 0.30
LOT_MIN, LOT_MAX, Z_LO, Z_HI = 0.01, 0.02, 0.5, 1.0
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")


def accurate_z_usd(m1, atr_mult=3.0):
    h = resample_1h(m1.assign(volume=0)); c = h["close"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"]-h["low"], (h["high"]-c.shift()).abs(), (h["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    d1 = to_d1(m1); dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtr = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dtd = {ts.date(): (0 if pd.isna(dtr.loc[ts]) else int(dtr.loc[ts])) for ts in d1.index}
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
    idx = h.index; C, H, L, O = c.values, h["high"].values, h["low"].values, h["open"].values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    m20, s20 = ma20.values, sd20.values; dtl = [dtd.get(idx[i].date(), 0) for i in range(len(idx))]
    def dl(i, d):
        if s20[i] <= 0 or np.isnan(s20[i]): return LOT_MIN
        z = (C[i]-m20[i])/s20[i]; zd = z if d == 1 else -z; f = max(0., min(1., (zd-Z_LO)/max(Z_HI-Z_LO, 1e-9)))
        return round(max(LOT_MIN, min(LOT_MAX, round((LOT_MIN+f*(LOT_MAX-LOT_MIN))/0.01)*0.01)), 2)
    pos = 0; ep = sl = elot = None; out = []
    for i in range(1, len(idx)):
        if np.isnan(upv[i]) or np.isnan(emv[i]) or np.isnan(atv[i]): continue
        ut = C[i-1] > emv[i]; dd = dtl[i]; cl = ut and dd == 1; cs = (not ut) and dd == -1
        if pos == 0:
            if H[i] >= upv[i] and cl: pos, ep, sl, elot = 1, max(O[i], upv[i]), max(O[i], upv[i])-atr_mult*atv[i], dl(i, 1)
            elif L[i] <= dnv[i] and cs: pos, ep, sl, elot = -1, min(O[i], dnv[i]), min(O[i], dnv[i])+atr_mult*atv[i], dl(i, -1)
            continue
        if pos == 1:
            sl = max(sl, C[i-1]-atr_mult*atv[i])
            if L[i] <= sl: out.append((idx[i], ((min(O[i], sl)-ep)-COST)*elot*100)); pos = 0
            elif L[i] <= xdv[i]:
                out.append((idx[i], ((min(O[i], xdv[i])-ep)-COST)*elot*100))
                if L[i] <= dnv[i] and cs: pos, ep, sl, elot = -1, min(O[i], dnv[i]), min(O[i], dnv[i])+atr_mult*atv[i], dl(i, -1)
                else: pos = 0
        else:
            sl = min(sl, C[i-1]+atr_mult*atv[i])
            if H[i] >= sl: out.append((idx[i], ((ep-max(O[i], sl))-COST)*elot*100)); pos = 0
            elif H[i] >= xuv[i]:
                out.append((idx[i], ((ep-max(O[i], xuv[i]))-COST)*elot*100))
                if H[i] >= upv[i] and cl: pos, ep, sl, elot = 1, max(O[i], upv[i]), max(O[i], upv[i])-atr_mult*atv[i], dl(i, 1)
                else: pos = 0
    return out


def _nas_open_min(d):
    et = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return (13*60+30) + (60 if et.dst() == dt.timedelta(0) else 0)

def orb_usd(m1, sl_mult=1.0, rmin=30, be=0.5, send=20*60, cost=2.0):
    """sl_mult: stop distance as fraction of ORB size (1.0 = current 'wide' 1R, <1 = tighter)."""
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
        tp = en+d*sz; slv = en-d*sz*sl_mult; risk = sz; cr = cost/risk; armed = False; pnl = None; xi = None
        for j in range(ei, b):
            if mod[j] >= send: pnl = d*(C[j]-en)/risk-cr; xi = j; break
            if d == 1:
                if not armed and (H[j]-en) >= be*risk: armed = True
                if armed and L[j] <= en: pnl = -cr; xi = j; break
                if L[j] <= slv: pnl = -sl_mult-cr; xi = j; break
                if H[j] >= tp: pnl = 1-cr; xi = j; break
            else:
                if not armed and (en-L[j]) >= be*risk: armed = True
                if armed and H[j] >= en: pnl = -cr; xi = j; break
                if H[j] >= slv: pnl = -sl_mult-cr; xi = j; break
                if L[j] <= tp: pnl = 1-cr; xi = j; break
        if pnl is None: pnl = d*(C[b-1]-en)/risk-cr; xi = b-1
        out.append((m1.index[xi], pnl*risk*0.10))
    return out


def rsi(c, n=2):
    dd = c.diff(); u = dd.clip(lower=0).rolling(n).mean(); d = (-dd.clip(upper=0)).rolling(n).mean()
    return 100-100/(1+u/d.replace(0, np.nan))

def reversal_usd(name, stop_pct=None):
    """stop_pct: hard % stop below entry (None = stopless mean-reversion, current live=disaster 5% only).
    Tighter test: exit at entry*(1-stop_pct) intrabar-approx on daily close touching."""
    c = px[name].dropna(); r2 = rsi(c, 2); s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    lo = px[name].dropna()  # only close available in daily parquet -> use close for stop check (approx)
    out = []; ip = False; ei = None; stop = None
    for i in range(1, len(c)):
        if not ip and r2.iloc[i] < 10 and c.iloc[i] > s200.iloc[i]:
            ip = True; ei = i; stop = c.iloc[i]*(1-stop_pct) if stop_pct else None
        elif ip:
            if stop is not None and c.iloc[i] <= stop:
                out.append((c.index[i], (c.iloc[i]-c.iloc[ei])*0.10)); ip = False
            elif c.iloc[i] > s5.iloc[i]:
                out.append((c.index[i], (c.iloc[i]-c.iloc[ei])*0.10)); ip = False
    return out


def daily(items):
    if not items: return pd.Series(dtype=float)
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC")).sort_index()
    return s.groupby(s.index.normalize()).sum()

def stats(items, y0="2026-01-01"):
    d = daily(items)
    d = d[d.index >= pd.Timestamp(y0, tz="UTC")]
    if len(d) < 3: return dict(tot=d.sum(), sharpe=np.nan, pf=np.nan, n=len(d))
    cal = pd.date_range(d.index.min(), d.index.max(), freq="B", tz="UTC")
    dc = d.reindex(cal).fillna(0)
    sh = dc.mean()/dc.std()*np.sqrt(252) if dc.std() > 0 else np.nan
    trades = [p for _, p in items if pd.Timestamp([t for t, q in items if q == p][0]).tz_convert("UTC") >= pd.Timestamp(y0, tz="UTC")] if False else [p for t, p in items if t.tz_convert("UTC") >= pd.Timestamp(y0, tz="UTC")]
    g = sum(x for x in trades if x > 0); b = -sum(x for x in trades if x < 0)
    return dict(tot=dc.sum(), sharpe=sh, pf=(g/b if b > 0 else np.inf), n=len([x for x in trades]))


print("="*74)
print("SL-WIDTH SWEEP  (Jan 2026->now, real backtest, 0.01 lot, $/pt XAU=1*dynlot US100=0.10)")
print("="*74)

zc = load_m1("XAUUSD"); nc = load_m1("NAS100")

print("\n[Z / XAU]  trailing ATR-stop multiple (live=3.0; tighter=smaller):")
for m in [1.5, 2.0, 2.5, 3.0]:
    s = stats(accurate_z_usd(zc, atr_mult=m)); tag = "  <- LIVE" if m == 3.0 else ""
    print(f"   ATRx{m:<4}  tot ${s['tot']:+7.0f}  Sharpe {s['sharpe']:.2f}  PF {s['pf']:.2f}  trades {s['n']}{tag}")

print("\n[ORB / NAS]  stop distance as fraction of ORB size (live=1.0R; tighter=smaller):")
for m in [0.5, 0.75, 1.0, 1.5]:
    s = stats(orb_usd(nc, sl_mult=m)); tag = "  <- LIVE" if m == 1.0 else ""
    print(f"   SL {m:<4}R  tot ${s['tot']:+7.0f}  Sharpe {s['sharpe']:.2f}  PF {s['pf']:.2f}  trades {s['n']}{tag}")

print("\n[Reversal / NAS]  hard %-stop (live=disaster 5% only; tighter=smaller). None=stopless:")
for sp in [None, 0.05, 0.03, 0.02, 0.01]:
    s = stats(reversal_usd("NAS100", stop_pct=sp)); lab = "stopless" if sp is None else f"{sp:.0%}"
    tag = "  <- LIVE(~disaster only)" if sp in (None, 0.05) else ""
    print(f"   stop {lab:<9} tot ${s['tot']:+7.0f}  Sharpe {s['sharpe']:.2f}  PF {s['pf']:.2f}  trades {s['n']}{tag}")

print("\n" + "="*74)
print("CURRENT BOOK -> $500 equity: monthly P/L distribution (0.01 lot, $ fixed regardless of equity)")
print("="*74)
Z = daily(accurate_z_usd(zc, 3.0)); O = daily(orb_usd(nc, 1.0)); R = daily(reversal_usd("NAS100", None))
alld = pd.concat([Z, O, R], axis=1).fillna(0).sum(axis=1)
alld = alld[alld.index >= pd.Timestamp("2026-01-01", tz="UTC")]
cal = pd.date_range(alld.index.min(), alld.index.max(), freq="B", tz="UTC")
book = alld.reindex(cal).fillna(0)
mo = book.groupby(book.index.to_period("M")).sum()
print(f"\n  Per-month book P/L ($):")
for p, v in mo.items():
    print(f"    {p}   ${v:+7.0f}   = {v/500:+.0%} on $500   |  {v/1000:+.0%} on $1000")
eq = 500 + book.cumsum(); dd = (eq-eq.cummax()); ddp = (dd/eq.cummax()).min()
print(f"\n  Avg month ${mo.mean():+.0f}  (median ${mo.median():+.0f})  |  best ${mo.max():+.0f}  worst ${mo.min():+.0f}")
print(f"  On $500: avg month {mo.mean()/500:+.0%}  |  MAX DD {ddp:.0%}  (leverage-amplified, min-lot forced)")
print(f"  Months hitting user target $300-500: {int(((mo>=300)&(mo<=500)).sum())} | >$500: {int((mo>500).sum())} | <$300: {int((mo<300).sum())} of {len(mo)}")
print("DONE")
