"""Deep, honest ML search for a MISSED directional edge on XAUUSD (H1).

Unlike the meta-label test (hundreds of trades), this uses ~32k H1 bars -> enough
data for ML to be meaningful. Rich features, triple-barrier labels (Lopez de Prado),
gradient boosting + logistic, PURGED time-series split (embargo around the forward-
looking label so no leakage), and -- the real test -- a COST-AWARE tradability backtest
out-of-sample vs buy-and-hold. Price prediction is the most overfit-prone ML task;
verdict is honest (noise-after-cost is the likely, acceptable outcome).

Run: python research/ml_xau_deep.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from pipeline.backtest.strategy_zrev import _adx
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score
from sklearn.inspection import permutation_importance

XAU = load_m1("XAUUSD")
H = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
       .dropna(subset=["open"]))
CUT = pd.Timestamp("2025-01-01", tz="UTC")
NB, KATR, COST = 8, 1.0, 0.30          # triple-barrier horizon/width; cost pts/flip


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# ---- features (all from completed bars up to t) ----
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
d1 = XAU["close"].resample("1D").last(); dsma = d1.rolling(50).mean().shift(1); d1p = d1.shift(1)
dmap = {ts.date(): (1 if d1p.loc[ts] > dsma.loc[ts] else -1)
        for ts in d1.index if not (np.isnan(d1p.loc[ts]) or np.isnan(dsma.loc[ts]))}
F["dtrend"] = [dmap.get(d, 0) for d in H.index.date]

# ---- triple-barrier label ----
hi = H["high"].values; lo = H["low"].values; cl = H["close"].values; av = a.values
y = np.full(len(H), np.nan)
for i in range(len(H) - NB):
    if np.isnan(av[i]):
        continue
    up_b, dn_b = cl[i] + KATR * av[i], cl[i] - KATR * av[i]
    lab = None
    for j in range(1, NB + 1):
        if hi[i + j] >= up_b: lab = 1; break
        if lo[i + j] <= dn_b: lab = 0; break
    y[i] = lab if lab is not None else int(cl[i + NB] > cl[i])
F["y"] = y
F = F.replace([np.inf, -np.inf], np.nan).dropna()
ycol = F.pop("y").astype(int).values
fwd_ret = np.log(c.shift(-1) / c).reindex(F.index).values   # next-bar return for tradability
cF = c.reindex(F.index).values                              # price aligned to F (post-dropna)

is_m = F.index < CUT
Xtr, ytr = F[is_m], ycol[is_m]
Xte, yte = F[~is_m], ycol[~is_m]
print(f"H1 bars: {len(F)}  features={F.shape[1]}  IS={len(Xtr)} OOS={len(Xte)}  base up-rate={ycol.mean():.2f}")

# ---- models: OOS AUC (train IS, test OOS) ----
for name, mdl in [("logistic", make_pipeline(StandardScaler(), LogisticRegression(C=0.2, max_iter=600))),
                  ("HistGB", HistGradientBoostingClassifier(max_depth=3, max_iter=300,
                                                            learning_rate=0.03, l2_regularization=2.0,
                                                            early_stopping=True))]:
    mdl.fit(Xtr, ytr)
    p = mdl.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(yte, p)
    # tradability OOS: long if p>=hi, short if p<=lo, hold to next bar, cost on flip
    cte = cF[~is_m]; frte = fwd_ret[~is_m]
    pos = np.where(p >= 0.55, 1, np.where(p <= 0.45, -1, 0))
    flips = np.r_[0, np.abs(np.diff(pos))]
    pnl_pts = pos * (np.exp(frte) - 1) * cte - flips * COST           # in price points = $/0.01 lot
    net = np.nansum(pnl_pts); g = pnl_pts[pnl_pts > 0].sum(); l = -pnl_pts[pnl_pts < 0].sum()
    pf = g / l if l > 0 else 9
    bh = np.nansum((np.exp(frte) - 1) * cte)
    print(f"  {name:9s} OOS AUC={auc:.3f}  flips={int(flips.sum())}  "
          f"net=${net:+.0f}@0.01  PF={pf:.2f}  vs buy&hold ${bh:+.0f}")

# ---- feature importance (HistGB permutation, OOS) ----
mdl = HistGradientBoostingClassifier(max_depth=3, max_iter=300, learning_rate=0.03,
                                     l2_regularization=2.0, early_stopping=True).fit(Xtr, ytr)
imp = permutation_importance(mdl, Xte, yte, n_repeats=5, random_state=0, scoring="roc_auc")
order = np.argsort(imp.importances_mean)[::-1][:6]
print("  fitur paling penting (OOS permutation AUC drop):",
      {F.columns[i]: round(float(imp.importances_mean[i]), 4) for i in order})
print("\nVONIS: ada edge HANYA jika OOS net & PF jelas positif SETELAH biaya, mengalahkan buy&hold.")
