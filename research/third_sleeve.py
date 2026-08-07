"""Add a THIRD uncorrelated sleeve to the live Z(XAU)+ORB(NAS) book to push combined Sharpe toward 1.9+.
Candidate = equity RSI-2 reversal (NAS+SP) — a DIFFERENT mechanism (mean-reversion) from Z (gold trend)
and ORB (NAS breakout), so it should be low-corr. Aligns all on daily streams, shows the correlation
matrix, and compares Z+ORB (2 sleeves) vs Z+ORB+Reversal (3 sleeves). Self-contained (accurate Z copied).

Run: python research/third_sleeve.py
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1, nas_orb
from pipeline.backtest.strategy_zrev import resample_1h

np.random.seed(7)
ENTRY_N = EXIT_N = 20; EMA = 100; DAILY_SMA = 50; ATR_MULT = 3.0; COST = 0.30
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")


def accurate_z(m1):
    h = resample_1h(m1.assign(volume=0)); c = h["close"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"]-h["low"], (h["high"]-c.shift()).abs(), (h["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    d1 = to_d1(m1); dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtr = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dtd = {ts.date(): (0 if pd.isna(dtr.loc[ts]) else int(dtr.loc[ts])) for ts in d1.index}
    idx = h.index; C, H, L, O = c.values, h["high"].values, h["low"].values, h["open"].values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    pos = 0; ep = ets = sl = None; tr_out = []; dtl = [dtd.get(idx[i].date(), 0) for i in range(len(idx))]
    for i in range(1, len(idx)):
        if np.isnan(upv[i]) or np.isnan(emv[i]) or np.isnan(atv[i]): continue
        ut = C[i-1] > emv[i]; dd = dtl[i]; cl = ut and dd == 1; cs = (not ut) and dd == -1
        if pos == 0:
            if H[i] >= upv[i] and cl: pos, ep, ets = 1, max(O[i], upv[i]), idx[i]; sl = ep - ATR_MULT*atv[i]
            elif L[i] <= dnv[i] and cs: pos, ep, ets = -1, min(O[i], dnv[i]), idx[i]; sl = ep + ATR_MULT*atv[i]
            continue
        if pos == 1:
            sl = max(sl, C[i-1] - ATR_MULT*atv[i])
            if L[i] <= sl: tr_out.append((idx[i], (min(O[i], sl)-ep)-COST)); pos = 0
            elif L[i] <= xdv[i]:
                tr_out.append((idx[i], (min(O[i], xdv[i])-ep)-COST))
                if L[i] <= dnv[i] and cs: pos, ep, ets = -1, min(O[i], dnv[i]), idx[i]; sl = ep + ATR_MULT*atv[i]
                else: pos = 0
        else:
            sl = min(sl, C[i-1] + ATR_MULT*atv[i])
            if H[i] >= sl: tr_out.append((idx[i], (ep-max(O[i], sl))-COST)); pos = 0
            elif H[i] >= xuv[i]:
                tr_out.append((idx[i], (ep-max(O[i], xuv[i]))-COST))
                if H[i] >= upv[i] and cl: pos, ep, ets = 1, max(O[i], upv[i]), idx[i]; sl = ep - ATR_MULT*atv[i]
                else: pos = 0
    return tr_out


def rsi(c, n=2):
    d = c.diff(); u = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1 + u/dn.replace(0, np.nan))


def reversal(name):
    c = px[name].dropna(); ret = c.pct_change(); r2 = rsi(c, 2).values
    up = (c > c.rolling(200).mean()).values; ex = (c > c.rolling(5).mean()).values
    pos = np.zeros(len(c)); ip = False
    for i in range(1, len(c)):
        if not ip and r2[i] < 10 and up[i]: ip = True
        elif ip and ex[i]: ip = False
        pos[i] = 1.0 if ip else 0.0
    return (pd.Series(pos, index=c.index).shift(1).fillna(0) * ret).dropna()


def to_daily(items):
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC")).sort_index()
    return s.groupby(s.index.normalize()).sum()


zd = to_daily(accurate_z(load_m1("XAUUSD")))
od = to_daily(nas_orb(load_m1("NAS100"))[0])
rv = (reversal("NAS100") + reversal("SP500")) / 2
rv.index = rv.index.tz_convert("UTC") if rv.index.tz else rv.index.tz_localize("UTC")

# common daily calendar (Z/ORB 0-fill non-trade days; reversal is a daily return series)
lo = max(zd.index.min(), od.index.min(), rv.index.min()); hi = min(zd.index.max(), od.index.max(), rv.index.max())
cal = pd.date_range(lo, hi, freq="B", tz="UTC")
Zs = zd.reindex(cal).fillna(0); Os = od.reindex(cal).fillna(0); Rs = rv.reindex(cal).fillna(0)
U = pd.DataFrame({"Z(XAU)": Zs/Zs.std(), "ORB(NAS)": Os/Os.std(), "Rev(eq)": Rs/Rs.std()}).dropna()

def sh(x): return x.mean()/x.std()*np.sqrt(252)
print("correlation matrix (daily):"); print(U.corr().round(2).to_string())
two = U[["Z(XAU)", "ORB(NAS)"]].mean(axis=1)
three = U.mean(axis=1)
a = three.values; N = len(a); nb = max(1, N//10); shs = []
for _ in range(3000):
    ix = (np.random.randint(0, N-10, nb)[:, None]+np.arange(10)).ravel(); s = a[ix]; sd = s.std()
    shs.append(s.mean()/sd*np.sqrt(252) if sd > 0 else 0.0)
lo3, hi3 = np.percentile(shs, [2.5, 97.5])
print(f"\nZ + ORB (2 sleeves):            Sharpe {sh(two):+.2f}")
print(f"Z + ORB + Reversal (3 sleeves): Sharpe {sh(three):+.2f}  95%CI[{lo3:+.2f},{hi3:+.2f}]")
print(f"  reversal corr -> Z {U['Rev(eq)'].corr(U['Z(XAU)']):+.2f}   ORB {U['Rev(eq)'].corr(U['ORB(NAS)']):+.2f}")
print("DONE")
