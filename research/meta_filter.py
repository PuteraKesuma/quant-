"""META-LABELING as a FILTER (Lopez de Prado) on the reversal edge — the honest way: primary signal =
RSI-2 reversal; a model learns (from PAST trades only) which entries to SKIP; measured by PURGED
walk-forward OOS (train on trades that finished before the OOS window). The only verdict that counts:
does OOS FILTERED Sharpe beat OOS UNFILTERED? If not, meta-labeling is overfit here -> discard.

Run: python research/meta_filter.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe
from sklearn.ensemble import HistGradientBoostingClassifier

np.random.seed(73)
COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
ASSETS = ["NAS100", "SP500", "NIKKEI"]


def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def trades(name):
    c = px[name].dropna(); ret = c.pct_change()
    r2 = rsi(c, 2); s200 = c.rolling(200).mean(); s50 = c.rolling(50).mean(); s5 = c.rolling(5).mean()
    vol = ret.rolling(20).std(); hi20 = c.rolling(20).max()
    r2v, s2v, s5v, s50v, cv = r2.values, s200.values, s5.values, s50.values, c.values
    volv, hiv, retv = vol.values, hi20.values, ret.values
    idx = c.index
    # daily position + per-day trade id
    pos = np.zeros(len(c)); tid = np.full(len(c), -1); feats = {}; entry_ix = {}
    ip = False; cur = -1; downs = 0
    for i in range(1, len(c)):
        downs = downs + 1 if retv[i] < 0 else 0
        if not ip and r2v[i] < 10 and cv[i] > s2v[i]:
            ip = True; cur += 1; entry_ix[cur] = i
            feats[cur] = [r2v[i], cv[i]/s2v[i]-1, cv[i]/s50v[i]-1, cv[i]/s5v[i]-1,
                          (cv[i]/cv[i-5]-1) if i >= 5 else 0.0, volv[i], cv[i]/hiv[i]-1,
                          float(downs), float(idx[i].weekday())]
        elif ip and cv[i] > s5v[i]:
            ip = False
        if ip:
            pos[i] = 1.0; tid[i] = cur
    pos = pd.Series(pos, index=idx); tid = pd.Series(tid, index=idx)
    # per-trade round-trip incl the recovery/exit day (entry close -> exit-day close)
    rows = []
    for t in range(cur + 1):
        days = np.where(tid.values == t)[0]
        if len(days) == 0: continue
        ei = entry_ix[t]; xi = min(days[-1] + 1, len(c) - 1)   # exit = first day AFTER the held window (the c>SMA5 recovery)
        tret = cv[xi] / cv[ei] - 1
        rows.append((name, idx[ei], idx[xi], tret > 0, feats[t], t))
    return pos, tid, ret, rows


P0, TID, RET, ROWS = {}, {}, {}, []
for a in ASSETS:
    p, t, r, rows = trades(a); PO = p
    P0[a] = p; TID[a] = t; RET[a] = r; ROWS += rows
df = pd.DataFrame(ROWS, columns=["asset", "entry", "exit", "win", "feat", "tid"]).sort_values("entry").reset_index(drop=True)
X = np.array(df["feat"].tolist()); y = df["win"].astype(int).values
print(f"reversal trades pooled: {len(df)}  (win rate {y.mean():.0%})")

# purged walk-forward: OOS windows; train on trades that EXITED before the window start
WIN = [("2018-01-01","2020-01-01"),("2020-01-01","2022-01-01"),("2022-01-01","2024-01-01"),("2024-01-01","2026-07-01")]
keep = pd.Series(True, index=df.index)                    # predicted keep-flag per trade
for a0, b0 in WIN:
    t0 = pd.Timestamp(a0, tz="UTC"); t1 = pd.Timestamp(b0, tz="UTC")
    tr = df["exit"] < t0                                  # purge: only trades finished before OOS
    oos = (df["entry"] >= t0) & (df["entry"] < t1)
    if tr.sum() < 60 or oos.sum() < 5: continue
    m = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=25)
    m.fit(X[tr.values], y[tr.values])
    p = m.predict_proba(X[oos.values])[:, 1]
    thr = np.median(m.predict_proba(X[tr.values])[:, 1])  # keep trades above the train-median prob
    keep.loc[oos] = p >= thr

# build OOS daily streams (unfiltered vs filtered), per asset, over the full OOS span
oos_lo = pd.Timestamp("2018-01-01", tz="UTC")
def stream(filtered):
    per = []
    for a in ASSETS:
        pos = PO if False else P0[a].copy()
        if filtered:
            skip_tids = set(df[(df.asset == a) & (~keep)]["tid"])
            mask = TID[a].isin(skip_tids)
            pos = pos.where(~mask, 0.0)
        r = (pos.shift(1).fillna(0) * RET[a] - pos.diff().abs().fillna(0) * COST[a])
        per.append(r)
    P = pd.concat(per, axis=1).mean(axis=1)
    return P[P.index >= oos_lo].dropna()

uf = stream(False); fl = stream(True)
print(f"\nOOS (2018-2026):")
print(f"  UNFILTERED reversal  Sharpe {sharpe(uf):+.2f}  trades kept {len(df[df.entry>=oos_lo])}")
print(f"  META-FILTERED        Sharpe {sharpe(fl):+.2f}  trades kept {int(keep[df.entry>=oos_lo].sum())}/{len(df[df.entry>=oos_lo])}")
print(f"  -> uplift {sharpe(fl)-sharpe(uf):+.2f}  ({'HELPS' if sharpe(fl)>sharpe(uf)+0.05 else 'no real gain -> discard'})")
print("DONE")
