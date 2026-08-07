"""Partner research #2 — MEAN-REVERSION on FX majors, with WALK-FORWARD + cost-stress + a regime gate.
Tests the hypothesis that FX mean-reverts (unlike trend): z-score fade to the SMA, both sides, one
position, exit on revert-to-mean / stop / max-hold. Compares NO-GATE vs ADX<gate (range-only, the
meta-label hint). Walk-forward = 4 time folds (per-fold net + win), cost-stress 1x/1.5x/2x.

Run: python research/mean_reversion_fx.py            (7 majors)
     python research/mean_reversion_fx.py EURUSD
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, duckdb

ROOT = r"C:\Quant\data\Level_0_Raw"
TF = "1h"; N = 20; EZ = 2.0; SZ = 3.5; MAXH = 24; ADX_GATE = 25
MAJORS = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD"]
PIP = {"USDJPY":0.01}
SPREAD = {"EURUSD":0.8,"GBPUSD":1.2,"USDJPY":0.9,"USDCHF":1.2,"AUDUSD":1.0,"USDCAD":1.3,"NZDUSD":1.5}
FOLDS = [("2023-01-01","2023-10-01"),("2023-10-01","2024-07-01"),
         ("2024-07-01","2025-04-01"),("2025-04-01","2026-06-26")]


def load(sym):
    p=f"{ROOT}/{sym}_1m.duckdb"
    if not os.path.exists(p): return None
    c=duckdb.connect(p, read_only=True)
    rows=c.execute("select epoch(ts),open,high,low,close from ohlcv order by ts").fetchall(); c.close()
    a=np.asarray(rows,"float64"); idx=pd.to_datetime(a[:,0],unit="s",utc=True)
    m1=pd.DataFrame({"open":a[:,1],"high":a[:,2],"low":a[:,3],"close":a[:,4]},index=idx)
    return (m1.resample(TF).agg({"open":"first","high":"max","low":"min","close":"last"}).dropna())


def adx(h,n=14):
    up=h["high"].diff(); dn=-h["low"].diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=h.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=h.index)
    tr=pd.concat([h["high"]-h["low"],(h["high"]-h["close"].shift()).abs(),(h["low"]-h["close"].shift()).abs()],axis=1).max(axis=1)
    a=tr.ewm(alpha=1/n,adjust=False).mean()
    pdi=100*plus.ewm(alpha=1/n,adjust=False).mean()/a; mdi=100*minus.ewm(alpha=1/n,adjust=False).mean()/a
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean()


def trades(sym, gate):
    h=load(sym)
    if h is None or len(h)<3000: return None
    c=h["close"].values; pip=PIP.get(sym,0.0001)
    ma=h["close"].rolling(N).mean(); sd=h["close"].rolling(N).std()
    z=((h["close"]-ma)/sd).values
    ax=adx(h).values; idx=h.index
    pos=0; entry=0.0; ei=0; tr=[]
    for i in range(N+1,len(h)):
        if pos==0:
            if np.isnan(z[i]) or np.isnan(ax[i]): continue
            if gate and ax[i]>=ADX_GATE: continue
            if z[i]<=-EZ: pos,entry,ei=1,c[i],i
            elif z[i]>=EZ: pos,entry,ei=-1,c[i],i
        else:
            hit=None
            if pos==1:
                if z[i]>=0: hit="mean"
                elif z[i]<=-SZ: hit="stop"
            else:
                if z[i]<=0: hit="mean"
                elif z[i]>=SZ: hit="stop"
            if hit is None and i-ei>=MAXH: hit="time"
            if hit:
                pips=pos*(c[i]-entry)/pip
                tr.append((idx[i], pips)); pos=0
    return pd.Series([p for _,p in tr], index=pd.DatetimeIndex([t for t,_ in tr]))


def metrics(s, cost):
    fold_net=[]
    for a,b in FOLDS:
        t0=pd.Timestamp(a,tz="UTC"); t1=pd.Timestamp(b,tz="UTC")
        seg=s[(s.index>=t0)&(s.index<t1)]
        fold_net.append((seg-cost).sum() if len(seg) else 0.0)
    return dict(n=len(s), wr=(s>0).mean(), ev=(s-cost).mean(), net=(s-cost).sum(),
                fold_net=fold_net, fp=sum(1 for x in fold_net if x>0))


def main():
    pairs=[a.upper() for a in sys.argv[1:]] or MAJORS
    print(f"MEAN-REVERSION FX  TF={TF} z-fade N={N} entry={EZ} stop={SZ} maxHold={MAXH}  walk-forward {len(FOLDS)} folds")
    for gate in (False, True):
        tag = f"ADX<{ADX_GATE} GATE" if gate else "NO GATE"
        print(f"\n===== {tag} =====")
        print(f"  {'pair':7} {'cost':>4} {'n':>5} {'win':>5} {'EV':>6} {'net':>7} {'folds+':>7}  per-fold net")
        series={}
        for sym in pairs:
            s=trades(sym, gate)                       # compute ONCE per (pair,gate)
            if s is None or len(s)==0: print(f"  {sym:7} (no trades)"); continue
            series[sym]=s; cost=SPREAD.get(sym,1.2); m=metrics(s,cost)
            pf=" ".join(f"{x:+.0f}" for x in m["fold_net"])
            print(f"  {sym:7} {cost:>4} {m['n']:>5} {m['wr']:>5.2f} {m['ev']:>+6.2f} {m['net']:>+7.0f} {m['fp']:>5}/{len(FOLDS)}  [{pf}]")
        for mult in (1.0,1.5,2.0):
            tot=sum((series[s]-SPREAD.get(s,1.2)*mult).sum() for s in series)
            print(f"    portfolio net @ {mult}x spread = {tot:+.0f} pip")
    print("\nread: MR robust = net-positive per pair AND most folds positive AND survives 1.5x cost;")
    print("the ADX gate should IMPROVE it if the edge is truly range-regime.")
    print("DONE")


if __name__=="__main__":
    main()
