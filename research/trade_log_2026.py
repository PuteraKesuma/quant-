"""2026 per-entry trade log for the LOCKED 3-sleeve book (GOLD Donch100 + NAS Donch100 + JPY carry@0.5x).
Fresh daily data. Shows every position episode active in 2026: entry date/dir/price -> exit/price, PnL%.
JPY includes accrued swap. Plus 2026-YTD book return at the locked weights.

Run: python research/trade_log_2026.py
"""
import os, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, dukascopy_python

Y0 = pd.Timestamp("2026-01-01", tz="UTC")
RATEDIFF_NOW = 3.0
ASSETS = {"GOLD": "XAU/USD", "NAS": "E_NQ-100", "JPY": "USD/JPY"}


def daily(code):
    df = dukascopy_python.fetch(code, dukascopy_python.INTERVAL_DAY_1, dukascopy_python.OFFER_SIDE_BID,
                                dt.datetime(2015,1,1), dt.datetime.utcnow())
    df = df.rename(columns=str.lower); s = df["close"].copy(); s.index = pd.to_datetime(s.index, utc=True)
    return s.resample("1D").last().dropna()


def donch_pos(c, N=100):
    up=c.rolling(N).max().shift(1); dn=c.rolling(N).min().shift(1)
    p=pd.Series(np.nan,index=c.index); p[c>=up]=1; p[c<=dn]=-1; return p.ffill()


def jpy_pos(c):
    swap=pd.Series(RATEDIFF_NOW/100/252, index=c.index)
    return ((c>c.rolling(100).mean())&(swap>0)).astype(float)


def episodes(pos, price):
    pos=pos.dropna(); segs=[]; st=pos.index[0]; d=pos.iloc[0]
    for i in range(1,len(pos)):
        if pos.iloc[i]!=d:
            segs.append((st, pos.index[i], d, price[st], price[pos.index[i]]))   # exit at flip price
            st=pos.index[i]; d=pos.iloc[i]
    segs.append((st, pos.index[-1], d, price[st], price[pos.index[-1]], "OPEN"))
    return segs


c = {k: daily(v) for k,v in ASSETS.items()}
print(f"2026 TRADE LOG — locked book (GOLD+NAS+JPY@0.5x)   data thru {max(s.index.max() for s in c.values()):%Y-%m-%d}\n")

for name in ("GOLD","NAS"):
    s=c[name]; pos=donch_pos(s,100); segs=episodes(pos, s)
    print(f"=== {name} (Donchian-100 stop&reverse) — episodes active in 2026 ===")
    for seg in segs:
        st,en,d,ep,xp=seg[:5]; is_open=len(seg)>5
        if en<Y0: continue
        dirn="LONG" if d==1 else "SHORT" if d==-1 else "FLAT"
        pnl=(d*(xp/ep-1))*100
        print(f"  in {st:%Y-%m-%d} {dirn:5} @ {ep:.2f}  ->  {'OPEN '+en.strftime('%Y-%m-%d') if is_open else 'out '+en.strftime('%Y-%m-%d')} @ {xp:.2f}   PnL {pnl:+.1f}%")
    print()

# JPY: show LONG episodes (flat = no trade)
s=c["JPY"]; pos=jpy_pos(s); segs=episodes(pos,s); swd=RATEDIFF_NOW/100/252
print("=== JPY (carry: long when >SMA100 & carry>0, else flat) — LONG episodes in 2026 ===")
for seg in segs:
    st,en,d,ep,xp=seg[:5]; is_open=len(seg)>5
    if en<Y0 or d!=1: continue
    ndays=(en-st).days
    pnl=((xp/ep-1)+swd*ndays)*100
    print(f"  in {st:%Y-%m-%d} LONG  @ {ep:.3f}  ->  {'OPEN '+en.strftime('%Y-%m-%d') if is_open else 'out '+en.strftime('%Y-%m-%d')} @ {xp:.3f}   PnL {pnl:+.1f}% (incl ~{swd*ndays*100:.1f}% swap)")

# 2026 YTD book return at locked weights (vol-targeted daily)
def sleeve_ret(name):
    s=c[name]
    if name=="JPY":
        ret=s.pct_change(); swap=pd.Series(swd,index=s.index)
        p=jpy_pos(s).shift(1).fillna(0); vol=ret.rolling(50).std().shift(1)*np.sqrt(252)
        return p*(0.10/vol).clip(upper=3).fillna(0)*(ret+swap)
    ret=s.pct_change(); p=donch_pos(s,100).shift(1).fillna(0)
    vol=ret.rolling(50).std().shift(1)*np.sqrt(252)
    return p*(0.10/vol).clip(upper=3).fillna(0)*ret
S=pd.concat({k:sleeve_ret(k) for k in ASSETS}, axis=1).dropna()
book=(S["GOLD"]+S["NAS"]+0.5*S["JPY"])/2.5
ytd=book[book.index>=Y0]
print(f"\n=== 2026 YTD book return (locked weights) = {ytd.sum()*100:+.1f}%   ({len(ytd)} trading days) ===")
for k in ASSETS:
    print(f"    {k} sleeve 2026 contribution: {S[k][S[k].index>=Y0].sum()*100:+.1f}%")
print("DONE")
