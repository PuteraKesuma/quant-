"""Can $500 hit 10-12% DD? The min-lot floor makes each sleeve's worst-year $DD ~$280-320 -> ~60% on $500,
regardless of instrument. The ONLY lever left (equity fixed at $500, lot fixed at floor) is a hard portfolio
DD CIRCUIT-BREAKER: halt trading for the rest of the month once month-to-date DD hits -$cap. This bounds DD
by construction but also caps the year's recovery. Sweep the cap and report real DD% + return on $500.
NAS-centric book (no gold): ORB + Reversal(NAS,SP500,DAX). True FBS tick values ($0.10/pt).
"""
import os, sys, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from zoneinfo import ZoneInfo
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
EQ = 500.0


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

def reversal_usd(name, dpp=0.10):
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


nas = load_m1("NAS100")
series = [daily(orb_usd(nas)), daily(reversal_usd("NAS100")), daily(reversal_usd("SP500")), daily(reversal_usd("DAX"))]
lo = max(s.index.min() for s in series); hi = min(s.index.max() for s in series)
cal = pd.date_range(lo, hi, freq="B", tz="UTC")
book = sum(s.reindex(cal).fillna(0) for s in series)


def apply_cb(book, cal, cap):
    """Halt the rest of a calendar month once that month's peak-to-date DD hits -cap."""
    out = book.copy().values.astype(float); months = cal.to_period("M")
    mtd_peak = 0.0; mtd = 0.0; cur_m = None; halted = False
    for i in range(len(cal)):
        if months[i] != cur_m:
            cur_m = months[i]; mtd = 0.0; mtd_peak = 0.0; halted = False
        if halted: out[i] = 0.0; continue
        mtd += out[i]; mtd_peak = max(mtd_peak, mtd)
        if mtd_peak - mtd >= cap: halted = True     # stop for rest of month (this day's loss already taken)
    return pd.Series(out, index=cal)


def stats(bk, cal, eq=EQ):
    eqc = eq + bk.cumsum(); dd = (eqc - eqc.cummax()); mdd = float(dd.min())
    years = [y for y in sorted(set(cal.year)) if bk[cal.year == y].abs().sum() > 0]
    greens = sum(1 for y in years if bk[cal.year == y].sum() > 0)
    wdd = 0.0
    for y in years:
        b = bk[cal.year == y]; e = b.cumsum(); wdd = min(wdd, float((e-e.cummax()).min()))
    tot = bk.sum(); n = len(years)
    return dict(tot=tot, per=tot/n, mdd=mdd, wdd=wdd, greens=greens, n=n,
                sh=(bk.mean()/bk.std()*np.sqrt(252) if bk.std() > 0 else np.nan))


def apply_cb_trailing(book, cal, cap, reset="M"):
    """Halt when equity is >=cap below its ALL-TIME running peak. reset='M' resumes each new month;
    reset=None halts permanently once triggered."""
    out = book.copy().values.astype(float)
    keys = cal.to_period(reset) if reset else None
    eq = 0.0; peak = 0.0; cur = None; halted = False
    for i in range(len(cal)):
        if reset and keys[i] != cur: cur = keys[i]; halted = False
        if halted: out[i] = 0.0; continue
        eq += out[i]; peak = max(peak, eq)
        if peak - eq >= cap: halted = True
    return pd.Series(out, index=cal)


print("="*80)
print(f"$500 NAS-centric book (ORB + Rev NAS/SP500/DAX)  --  can any DD control hit 10-12%?")
print("="*80)
print(f"{'mechanism':<26}{'total$':>8}{'/yr%':>7}  {'fullDD$':>8}{'fullDD%':>8}  green  Sharpe")
def line(lbl, bk):
    s = stats(bk, cal)
    print(f"{lbl:<26}{s['tot']:>+8.0f}{s['per']/EQ:>+7.0%}  {s['mdd']:>8.0f}{s['mdd']/EQ:>8.0%}  {s['greens']}/{s['n']}  {s['sh']:>5.2f}")
line("raw (no control)", book)
for cap in [90, 60, 50, 40]:
    line(f"monthly CB -${cap}", apply_cb(book, cal, cap))
for cap in [90, 60, 50, 40]:
    line(f"trailing CB -${cap} (mo-reset)", apply_cb_trailing(book, cal, cap, reset="M"))
for cap in [90, 60, 50]:
    line(f"trailing CB -${cap} (permanent)", apply_cb_trailing(book, cal, cap, reset=None))
print("\nTarget: fullDD% between -10% and -12%. Note DD is REALIZED; the stopless Reversal sleeve means live")
print("FLOATING DD + margin can breach sooner on $500 -> real danger is even higher than these numbers.")
print("DONE")
