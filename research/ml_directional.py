"""Deep, honest directional-ML test for ANY symbol (generalised from ml_xau_deep).
Rich features + triple-barrier labels + gradient boosting/logistic + time split +
COST-AWARE OOS tradability vs buy&hold. Run: python research/ml_directional.py US100|XAU
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from pipeline.backtest.strategy_zrev import _adx
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score
from sklearn.inspection import permutation_importance

ARG = sys.argv[1].upper() if len(sys.argv) > 1 else "NAS100"
SYM = {"US100": "NAS100", "NAS100": "NAS100", "XAU": "XAUUSD", "XAUUSD": "XAUUSD"}[ARG]
COST = {"NAS100": 2.0, "XAUUSD": 0.30}[SYM]          # spread+slip in points
DPP = {"NAS100": 0.10, "XAUUSD": 1.0}[SYM]           # $ per point @0.01 lot
CUT = pd.Timestamp("2025-01-01", tz="UTC")
NB, KATR = 8, 1.0

M1 = load_m1(SYM)
H = (M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
       .dropna(subset=["open"]))


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(c, n):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


c = H["close"]; a = atr(H, 14)
F = pd.DataFrame(index=H.index)
for k in (1, 3, 6, 12, 24):
    F[f"ret{k}"] = np.log(c / c.shift(k))
F["vol24"] = np.log(c / c.shift(1)).rolling(24).std()
F["atr_pct"] = a / a.rolling(200).mean()
F["adx"] = _adx(H, 14)
for e in (20, 50, 100, 200):
    F[f"emadist{e}"] = (c - c.ewm(span=e, adjust=False).mean()) / a
F["rsi14"] = rsi(c, 14); F["rsi2"] = rsi(c, 2)
up20 = H["high"].rolling(20).max(); lo20 = H["low"].rolling(20).min()
F["chanpos"] = (c - lo20) / (up20 - lo20)
F["hour"] = H.index.hour; F["dow"] = H.index.dayofweek
d1 = M1["close"].resample("1D").last().dropna(); dsma = d1.rolling(50).mean().shift(1); d1p = d1.shift(1)
dmap = {ts.date(): (1 if d1p.loc[ts] > dsma.loc[ts] else -1)
        for ts in d1.index if not (np.isnan(d1p.loc[ts]) or np.isnan(dsma.loc[ts]))}
F["dtrend"] = [dmap.get(d, 0) for d in H.index.date]

hi = H["high"].values; lo = H["low"].values; cl = H["close"].values; av = a.values
y = np.full(len(H), np.nan)
for i in range(len(H) - NB):
    if np.isnan(av[i]):
        continue
    ub, db = cl[i] + KATR * av[i], cl[i] - KATR * av[i]; lab = None
    for j in range(1, NB + 1):
        if hi[i + j] >= ub: lab = 1; break
        if lo[i + j] <= db: lab = 0; break
    y[i] = lab if lab is not None else int(cl[i + NB] > cl[i])
F["y"] = y
F = F.replace([np.inf, -np.inf], np.nan).dropna()
yc = F.pop("y").astype(int).values
fwd = np.log(c.shift(-1) / c).reindex(F.index).values
cF = c.reindex(F.index).values
is_m = F.index < CUT
Xtr, ytr, Xte, yte = F[is_m], yc[is_m], F[~is_m], yc[~is_m]
print(f"{SYM} H1 bars={len(F)} feat={F.shape[1]} IS={len(Xtr)} OOS={len(Xte)} up-rate={yc.mean():.2f} "
      f"(cost {COST}pt, ${DPP}/pt)\n")
for name, mdl in [("logistic", make_pipeline(StandardScaler(), LogisticRegression(C=0.2, max_iter=600))),
                  ("HistGB", HistGradientBoostingClassifier(max_depth=3, max_iter=300,
                                                            learning_rate=0.03, l2_regularization=2.0, early_stopping=True))]:
    mdl.fit(Xtr, ytr); p = mdl.predict_proba(Xte)[:, 1]; auc = roc_auc_score(yte, p)
    cte, frte = cF[~is_m], fwd[~is_m]
    pos = np.where(p >= 0.55, 1, np.where(p <= 0.45, -1, 0)); flips = np.r_[0, np.abs(np.diff(pos))]
    pnl = (pos * (np.exp(frte) - 1) * cte - flips * COST) * DPP
    net = np.nansum(pnl); g = pnl[pnl > 0].sum(); l = -pnl[pnl < 0].sum()
    bh = np.nansum((np.exp(frte) - 1) * cte) * DPP
    print(f"  {name:9s} OOS AUC={auc:.3f} flips={int(flips.sum())} net=${net:+.0f}@0.01 "
          f"PF={g/l if l>0 else 9:.2f} vs buy&hold ${bh:+.0f}")
mdl = HistGradientBoostingClassifier(max_depth=3, max_iter=300, learning_rate=0.03,
                                     l2_regularization=2.0, early_stopping=True).fit(Xtr, ytr)
imp = permutation_importance(mdl, Xte, yte, n_repeats=5, random_state=0, scoring="roc_auc")
top = np.argsort(imp.importances_mean)[::-1][:6]
print("  fitur penting (OOS):", {F.columns[i]: round(float(imp.importances_mean[i]), 4) for i in top})
print("\nVONIS: edge HANYA jika OOS net & PF jelas positif setelah biaya & kalahkan buy&hold.")
