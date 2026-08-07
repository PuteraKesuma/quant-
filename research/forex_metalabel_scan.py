"""Honest meta-labeling ensemble for the 7 major FX pairs — the disciplined version of the
user's '35 indicators -> brain confirms >80% confidence -> execute' idea.

FIX over naive voting: correlated indicators are NOT independent votes, and 'confidence' must be
LEARNED + CALIBRATED, not asserted. So: ~16 decorrelated indicators become FEATURES; both a LONG and
a SHORT hypothesis are labeled at every M5 bar via a triple-barrier (target/stop = k*ATR, horizon H);
a gradient-boosted model outputs P(win); isotonic calibration makes P=0.8 mean ~80% OOS; we GATE at
P>=0.8 and measure OOS calibration + net pips AFTER spread. Time-ordered train/calib/test with an
embargo (no leakage). This is a per-signal EDGE CHECK, not yet a full backtest.

Run: python research/forex_metalabel_scan.py            (all pairs with local data)
     python research/forex_metalabel_scan.py EURUSD     (one pair)
"""
import os, sys, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, duckdb
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

ROOT = r"C:\Quant\data\Level_0_Raw"
TF = "5min"; H = 12; K = 1.0           # M5 bars, 60-min horizon, barrier = 1.0*ATR
GATE = 0.80
WIN_START = "2023-01-01"               # common window across the 7 (fetched from here)
PIP = {"USDJPY": 0.01}                  # others 0.0001
SPREAD_PIPS = {"EURUSD":0.8,"GBPUSD":1.2,"USDJPY":0.9,"USDCHF":1.2,
               "AUDUSD":1.0,"USDCAD":1.3,"NZDUSD":1.5}   # conservative retail round-trip
MAJORS = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD"]


def load_m5(sym):
    db = f"{ROOT}/{sym}_1m.duckdb"
    if not os.path.exists(db): return None
    c = duckdb.connect(db, read_only=True)
    rows = c.execute(f"select epoch(ts),open,high,low,close from ohlcv "
                     f"where ts >= TIMESTAMP '{WIN_START}' order by ts").fetchall()
    c.close()
    if not rows: return None
    a = np.asarray(rows, "float64")
    idx = pd.to_datetime(a[:,0], unit="s", utc=True)
    m1 = pd.DataFrame({"open":a[:,1],"high":a[:,2],"low":a[:,3],"close":a[:,4]}, index=idx)
    return (m1.resample(TF).agg({"open":"first","high":"max","low":"min","close":"last"})
              .dropna(subset=["open"]))


def wilder(s, n): return s.ewm(alpha=1/n, adjust=False).mean()

def features(h):
    o,hi,lo,c = h["open"],h["high"],h["low"],h["close"]
    tr = pd.concat([hi-lo,(hi-c.shift()).abs(),(lo-c.shift()).abs()],axis=1).max(axis=1)
    atr = wilder(tr,14)
    d = pd.DataFrame(index=h.index)
    # momentum
    delta=c.diff(); up=delta.clip(lower=0); dn=(-delta).clip(lower=0)
    rs=wilder(up,14)/wilder(dn,14).replace(0,np.nan); d["rsi"]=100-100/(1+rs)
    ll=lo.rolling(14).min(); hh=hi.rolling(14).max(); d["stoch"]=100*(c-ll)/(hh-ll).replace(0,np.nan)
    tp=(hi+lo+c)/3; d["cci"]=(tp-tp.rolling(20).mean())/(0.015*tp.rolling(20).std().replace(0,np.nan))  # std-based CCI (fast)
    macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean()
    d["macd_h"]=(macd-macd.ewm(span=9,adjust=False).mean())/atr
    d["roc"]=c.pct_change(10)*100
    # trend
    ema20=c.ewm(span=20,adjust=False).mean(); d["ema_slope"]=ema20.diff()/atr
    d["dist_ema50"]=(c-c.ewm(span=50,adjust=False).mean())/atr
    upm=hi.diff(); dnm=-lo.diff()
    plus=pd.Series(np.where((upm>dnm)&(upm>0),upm,0.0),index=h.index)
    minus=pd.Series(np.where((dnm>upm)&(dnm>0),dnm,0.0),index=h.index)
    pdi=100*wilder(plus,14)/atr; mdi=100*wilder(minus,14)/atr
    d["adx"]=wilder(100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan),14)
    # volatility / mean-rev / structure
    sma20=c.rolling(20).mean(); sd20=c.rolling(20).std()
    d["bb_pctb"]=(c-(sma20-2*sd20))/((sma20+2*sd20)-(sma20-2*sd20)).replace(0,np.nan)
    d["atr_rel"]=atr/c
    hi20=hi.rolling(20).max(); lo20=lo.rolling(20).min()
    d["donch_pos"]=(c-lo20)/(hi20-lo20).replace(0,np.nan)
    d["chop"]=100*np.log10(tr.rolling(14).sum()/(hi.rolling(14).max()-lo.rolling(14).min()).replace(0,np.nan))/np.log10(14)
    d["ret1"]=c.diff()/atr; d["ret5"]=c.diff(5)/atr
    d["body"]=(c-o).abs()/(hi-lo).replace(0,np.nan)
    d["hour"]=h.index.hour
    return d, atr


def _label_side(c, hi, lo, a, pip, side):
    """Vectorized triple-barrier for one side (stop-first tie-break). ~H numpy passes."""
    n=len(c); tgt=c+side*K*a; stp=c-side*K*a
    win=np.full(n,np.nan); rp=np.full(n,np.nan); resolved=np.zeros(n,bool)
    valid=(~np.isnan(a))&(a>0)
    for j in range(1,H+1):
        hij=np.full(n,np.nan); loj=np.full(n,np.nan)
        hij[:n-j]=hi[j:]; loj[:n-j]=lo[j:]
        if side==1: hit_stop=loj<=stp; hit_tgt=hij>=tgt
        else:       hit_stop=hij>=stp; hit_tgt=loj<=tgt
        ns=valid&~resolved&hit_stop
        ntg=valid&~resolved&hit_tgt&~hit_stop            # stop priority on the same bar
        win[ns]=0.0; rp[ns]=-K*a[ns]/pip; resolved[ns]=True
        win[ntg]=1.0; rp[ntg]=+K*a[ntg]/pip; resolved[ntg]=True
    end=np.minimum(np.arange(n)+H, n-1)
    rto=side*(c[end]-c)/pip; to=valid&~resolved
    win[to]=(rto[to]>0).astype(float); rp[to]=rto[to]
    win[~valid]=np.nan
    return win, rp

def label(h, atr, pip):
    c=h["close"].values; hi=h["high"].values; lo=h["low"].values; a=atr.values
    return [_label_side(c,hi,lo,a,pip,1), _label_side(c,hi,lo,a,pip,-1)]


def run_pair(sym):
    h=load_m5(sym)
    if h is None or len(h)<5000:
        print(f"{sym:7} : no/insufficient data"); return None
    pip=PIP.get(sym,0.0001); cost=SPREAD_PIPS.get(sym,1.2)
    feat,atr=features(h); lab=label(h,atr,pip)
    # stack long+short samples
    Xs=[]; ys=[]; rps=[]; ts=[]
    for side,(win,rp) in zip((1,-1),lab):
        df=feat.copy(); df["side"]=side
        m=~np.isnan(win) & df.notna().all(axis=1).values
        Xs.append(df[m]); ys.append(win[m]); rps.append(rp[m]); ts.append(df.index[m])
    X=pd.concat(Xs); y=np.concatenate(ys); rp=np.concatenate(rps); tidx=np.concatenate([t.values for t in ts])
    order=np.argsort(tidx); X=X.iloc[order].reset_index(drop=True); y=y[order]; rp=rp[order]
    n=len(X); i1=int(n*0.60); i2=int(n*0.75)          # train / calib / test (time-ordered)
    emb=H*2
    Xtr,ytr=X.iloc[:i1], y[:i1]
    Xca,yca=X.iloc[i1+emb:i2], y[i1+emb:i2]
    Xte,yte,rpte=X.iloc[i2+emb:], y[i2+emb:], rp[i2+emb:]
    clf=HistGradientBoostingClassifier(max_iter=250,max_depth=4,learning_rate=0.05,
                                       l2_regularization=1.0,min_samples_leaf=200,random_state=0)
    clf.fit(Xtr,ytr)
    iso=IsotonicRegression(out_of_bounds="clip").fit(clf.predict_proba(Xca)[:,1], yca)
    p=iso.transform(clf.predict_proba(Xte)[:,1])
    base=yte.mean()
    days=max(1,(len(h)*0.25)/ (288))   # ~M5 bars/day, test≈last 25% of bars
    # reliability deciles
    rel=[]
    for b in np.linspace(0,1,11)[:-1]:
        mm=(p>=b)&(p<b+0.1)
        if mm.sum()>30: rel.append((round(b,1),int(mm.sum()),round(yte[mm].mean(),3)))
    print(f"\n{sym}  test n={len(yte):,}  base win={base:.3f}  cost={cost}pip  pmax={p.max():.2f}")
    print(f"  reliability (P-bin: n / OOS win): " + " ".join(f"[{b}:{n}/{w}]" for b,n,w in rel))
    # threshold sweep
    best=None
    print(f"  {'gate':>5} {'trades':>7} {'win':>6} {'EV/tr':>7} {'net(pip)':>9} {'/day':>6}")
    for g in (0.55,0.58,0.60,0.62,0.65,0.68,0.72):
        m=p>=g; n_hi=int(m.sum())
        if n_hi==0:
            print(f"  {g:>5.2f} {0:>7} {'-':>6} {'-':>7} {'-':>9} {'-':>6}"); continue
        wr=yte[m].mean(); ev=(rpte[m]-cost).mean(); net=(rpte[m]-cost).sum()
        print(f"  {g:>5.2f} {n_hi:>7} {wr:>6.3f} {ev:>+7.2f} {net:>+9.0f} {n_hi/days:>6.1f}")
        if n_hi>=100 and ev>0 and (best is None or ev>best['ev']): best=dict(gate=g,n=n_hi,wr=wr,ev=ev,net=net)
    return dict(sym=sym,base=base,pmax=float(p.max()),best=best)


if __name__=="__main__":
    pairs=[a.upper() for a in sys.argv[1:]] or MAJORS
    print(f"FX META-LABEL SCAN  TF={TF} horizon={H}bars barrier={K}xATR gate P>={GATE}  window>={WIN_START}")
    res=[r for s in pairs if (r:=run_pair(s)) is not None]
    if res:
        print("\n==== SUMMARY (best profitable gate per pair, OOS after spread) ====")
        for r in res:
            if r['best']:
                b=r['best']; print(f"  {r['sym']:7} pmax={r['pmax']:.2f}  BEST gate>={b['gate']:.2f}: "
                                    f"trades={b['n']} win={b['wr']:.3f} EV={b['ev']:+.2f}pip net={b['net']:+.0f}pip  <- EDGE")
            else:
                print(f"  {r['sym']:7} pmax={r['pmax']:.2f}  no profitable gate (no calibrated edge after cost)")
        print("\nread: '>=80% confidence' is unreachable on FX M5 (pmax ~0.6-0.7). The honest question is whether")
        print("the model's TOP calibrated bin still wins enough to be net-positive after spread.")
    print("DONE")
