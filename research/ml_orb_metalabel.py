"""Meta-label the deployed orb30_nas trades: is there a feature (known AT ENTRY) that
predicts which NY-ORB breakout WINS -> a basis for dynamic lot on US100 (parallel to
zrev's z-score sizing). Small sample (~hundreds of trades) -> skeptical; if one simple
feature dominates it's a RULE not ML (the zrev lesson).

Features at entry: relative opening-range size, breakout direction, breakout delay,
gap, day-of-week, intraday momentum. Label = trade win. Purged TimeSeriesSplit.
Run: python research/ml_orb_metalabel.py
"""
import sys
import datetime as dt
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1, _nas_open_min, stats
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score

NAS = load_m1("NAS100")
H = NAS["high"].values; L = NAS["low"].values; C = NAS["close"].values; O = NAS["open"].values
mod = NAS.index.hour.values * 60 + NAS.index.minute.values
dord = NAS.index.normalize().asi8
uniq, starts = np.unique(dord, return_index=True); starts = list(starts) + [len(NAS)]
d1 = to_d1(NAS); dc = d1["close"]; pc = dc.shift(1); sma = dc.rolling(50).mean().shift(1)
tmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts])) else (1 if pc.loc[ts] > sma.loc[ts] else -1))
        for ts in d1.index}
pclose = {ts.date(): float(dc.loc[ts]) for ts in d1.index}

rows = []; ranges = []
for di in range(len(uniq)):
    a, b = starts[di], starts[di + 1]; day = NAS.index[a].date(); om = _nas_open_min(day)
    md = mod[a:b]; idx = np.arange(a, b); rm = (md >= om) & (md < om + 30)
    if rm.sum() < 15:
        continue
    ri = idx[rm]; oh = H[ri].max(); ol = L[ri].min(); size = oh - ol
    if size <= 0:
        continue
    medrange = np.median(ranges[-20:]) if len(ranges) >= 10 else size
    ranges.append(size)
    pidx = idx[md >= om + 30]; ei = d = ent = None
    for i in pidx:
        if H[i] > oh: ei, d, ent = i, 1, oh; break
        if L[i] < ol: ei, d, ent = i, -1, ol; break
    if ei is None:
        continue
    td = tmap.get(day, 0)
    if td == 0 or (td > 0) != (d == 1):
        continue                                   # deployed trend filter
    # outcome (1:1 + breakeven + session-end, as live)
    cr = 2.0 / size; armed = False; pnl = None
    for j in range(ei, b):
        if mod[j] >= 20 * 60: pnl = d * (C[j] - ent) / size - cr; break
        if d == 1:
            if not armed and (H[j] - ent) >= 0.5 * size: armed = True
            if armed and L[j] <= ent: pnl = -cr; break
            if L[j] <= ent - size: pnl = -1 - cr; break
            if H[j] >= ent + size: pnl = 1 - cr; break
        else:
            if not armed and (ent - L[j]) >= 0.5 * size: armed = True
            if armed and H[j] >= ent: pnl = -cr; break
            if H[j] >= ent + size: pnl = -1 - cr; break
            if L[j] <= ent - size: pnl = 1 - cr; break
    if pnl is None:
        pnl = d * (C[b - 1] - ent) / size - cr
    prev = pclose.get(NAS.index[a - 1].date() if a > 0 else day, ent)
    rows.append(dict(
        ts=NAS.index[ei], pnl=pnl, win=int(pnl > 0),
        relrange=size / medrange, brkdir=d, delay=(mod[ei] - om - 30),
        gap=(O[ri[0]] - prev) / size, dow=NAS.index[ei].dayofweek,
        mom=(ent - C[ri[0]]) / size,
    ))

df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
y = df["win"].values; pnl = df["pnl"].values
X = df[["relrange", "brkdir", "delay", "gap", "dow", "mom"]]
print(f"orb30_nas trades={len(df)} win-rate={100*y.mean():.0f}%  (R units)\n")
aucs = []
for tr, te in TimeSeriesSplit(n_splits=5).split(X):
    if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
        continue
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500)).fit(X.iloc[tr], y[tr])
    aucs.append(roc_auc_score(y[te], m.predict_proba(X.iloc[te])[:, 1]))
print(f"OOS AUC (purged CV): {np.mean(aucs):.3f}  folds={[round(a,2) for a in aucs]}")
m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500)).fit(X, y)
co = dict(zip(X.columns, m.named_steps["logisticregression"].coef_[0]))
print("koef:", {k: round(v, 2) for k, v in sorted(co.items(), key=lambda z: -abs(z[1]))})
for col in ["relrange", "mom"]:
    q = pd.qcut(df[col], 4, labels=False, duplicates="drop")
    print(f"R-expectancy per kuartil {col}:", {f"Q{k+1}": round(float(pnl[q == k].mean()), 2) for k in sorted(np.unique(q))})
print("\nVONIS: layak sizing HANYA jika AUC>=0.55 & ada fitur yang ekspektasi-R-nya monoton jelas.")
