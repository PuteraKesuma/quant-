"""Partner research #1 — Time-Series Momentum (trend-following) across our 9-asset universe.
The most robust published anomaly (Moskowitz/Ooi/Pedersen 2012). Daily bars from our 1m duckdbs.
Honest gate: cost-aware, IS/OOS split, per-year sign, maxDD, and an equal-risk portfolio.
Signal: long if close > SMA(L) else short (no-lookahead, shift 1). Vol-scaled to ~10% ann. target.

Run: python research/cross_asset_tsmom.py
"""
import os, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, duckdb

ROOT = r"C:\Quant\data\Level_0_Raw"
ASSETS = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","XAUUSD","NAS100"]
PIP = {"USDJPY":0.01,"XAUUSD":0.01,"NAS100":1.0}          # others 0.0001
SPREAD = {"EURUSD":0.8,"GBPUSD":1.2,"USDJPY":0.9,"USDCHF":1.2,"AUDUSD":1.0,"USDCAD":1.3,
          "NZDUSD":1.5,"XAUUSD":20.0,"NAS100":100.0}       # ~round-trip in 'pips' (points)
LKB = [50,100,200]
OOS_FROM = pd.Timestamp("2025-01-01", tz="UTC")
TARGET_VOL = 0.10                                          # annualized vol target for scaling


def load_daily(sym):
    p=f"{ROOT}/{sym}_1m.duckdb"
    if not os.path.exists(p): return None
    c=duckdb.connect(p, read_only=True)
    rows=c.execute("select epoch(ts),open,high,low,close from ohlcv order by ts").fetchall(); c.close()
    a=np.asarray(rows,"float64"); idx=pd.to_datetime(a[:,0],unit="s",utc=True)
    m1=pd.DataFrame({"close":a[:,4]},index=idx)
    return m1["close"].resample("1D").last().dropna()


def stats(r):
    r=r.dropna()
    if len(r)<50: return None
    ann=252
    sharpe=r.mean()/r.std()*np.sqrt(ann) if r.std()>0 else 0
    eq=(1+r).cumprod(); dd=(eq/eq.cummax()-1).min()
    cagr=eq.iloc[-1]**(ann/len(r))-1
    return dict(sharpe=sharpe, cagr=cagr, maxdd=dd, n=len(r))


def run_asset(sym):
    c=load_daily(sym)
    if c is None or len(c)<400: return None
    ret=c.pct_change()
    pip=PIP.get(sym,0.0001); cost_frac=(SPREAD.get(sym,1.0)*pip)/c   # cost per full flip, as return
    out={}
    for L in LKB:
        sma=c.rolling(L).mean()
        pos=np.sign(c-sma).shift(1)                                  # no lookahead
        # vol-scale: size so each asset ~ TARGET_VOL annualized
        vol=ret.rolling(50).std().shift(1)*np.sqrt(252)
        scale=(TARGET_VOL/vol).clip(upper=3).fillna(0)
        gross=pos*scale*ret
        flips=(pos.diff().abs().fillna(0))*scale                    # turnover units
        net=gross-flips*cost_frac
        full=stats(net); oos=stats(net[net.index>=OOS_FROM])
        yr=net.groupby(net.index.year).sum()
        green=int((yr>0).sum()); tot=int(yr.notna().sum())
        out[L]=dict(full=full, oos=oos, green=green, tot=tot, net_series=net)
    return out


def main():
    print(f"TIME-SERIES MOMENTUM (daily, SMA-sign, vol-targeted {TARGET_VOL:.0%})  OOS from {OOS_FROM.date()}")
    print(f"{'asset':8} {'L':>4} {'Sharpe':>7} {'OOS-Sh':>7} {'CAGR':>7} {'maxDD':>7} {'green':>7}")
    port={L:[] for L in LKB}
    survivors=[]
    for s in ASSETS:
        r=run_asset(s)
        if r is None: print(f"{s:8} (no data)"); continue
        for L in LKB:
            d=r[L]; f=d["full"]; o=d["oos"]
            if f is None: continue
            osh=o["sharpe"] if o else float("nan")
            print(f"{s:8} {L:>4} {f['sharpe']:>7.2f} {osh:>7.2f} {f['cagr']:>7.1%} {f['maxdd']:>7.1%} {d['green']:>3}/{d['tot']}")
            port[L].append(d["net_series"].rename(s))
            if f["sharpe"]>0.5 and (o and o["sharpe"]>0.3) and d["green"]>=d["tot"]-1:
                survivors.append((s,L,f["sharpe"],osh))
        print()
    print("==== EQUAL-RISK PORTFOLIO (avg of vol-scaled assets) ====")
    for L in LKB:
        if not port[L]: continue
        P=pd.concat(port[L],axis=1).mean(axis=1)
        f=stats(P); o=stats(P[P.index>=OOS_FROM])
        print(f"  L={L:>3}: Sharpe {f['sharpe']:.2f}  OOS-Sharpe {o['sharpe'] if o else float('nan'):.2f}  "
              f"CAGR {f['cagr']:.1%}  maxDD {f['maxdd']:.1%}")
    print("\n==== ROBUST-ish SURVIVORS (Sharpe>0.5, OOS>0.3, all-but-1 year green) ====")
    if survivors:
        for s,L,sh,osh in sorted(survivors,key=lambda x:-x[2]):
            print(f"  {s:8} L={L:>3}  Sharpe {sh:.2f}  OOS {osh:.2f}")
    else:
        print("  (none — daily TSMOM not robust on this universe/window)")
    print("DONE")


if __name__=="__main__":
    main()
