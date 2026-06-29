"""Deeper validation of the ML meta-label sizing before building it. Four stress
tests that could DEBUNK the earlier result:

  1. WALK-FORWARD (expanding) retraining — the realistic, leakage-safe protocol
     (train on past only, predict next chunk, expand). Does the confidence->$ stay
     monotonic? Does ML-sizing still beat fixed?
  2. ROBUSTNESS — bootstrap net (resample trades) & permute order for DD: in what %
     of resamples does ML-sizing beat fixed on return AND on drawdown?
  3. FEATURE ABLATION — is it ALL z-score? (all vs no-zscore vs zscore-only)
  4. MODEL CHECK — does gradient boosting agree with logistic?

Run: python research/ml_validate_deep.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from ml_metalabel import build, H1, atr
from pipeline.backtest.strategy_zrev import simulate, ZRevParams, trades_to_df
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score

# ---- data: features + pnl, time-ordered ----
X, y = build(use_daily=False)
T = trades_to_df(simulate(H1, ZRevParams(20, 0, 0.30, trend_filter=True, trend_ema=100)))
T["entry_ts"] = pd.to_datetime(T["entry_ts"]); T["pnl"] = T["pnl_points"].astype(float)
ema = H1["close"].ewm(span=100, adjust=False).mean(); a = atr(H1, 14)
keep = [i for i, t in enumerate(T.itertuples()) if t.entry_ts in H1.index and not np.isnan(float(a.loc[t.entry_ts]))]
pnl = T.iloc[keep]["pnl"].values
assert len(pnl) == len(y)


def walk_forward(model_fn, Xf, init=0.4, chunks=6):
    n = len(y); start = int(n * init); conf = np.full(n, np.nan)
    bounds = np.linspace(start, n, chunks + 1).astype(int)
    for k in range(chunks):
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= lo or len(np.unique(y[:lo])) < 2:
            continue
        m = model_fn().fit(Xf.iloc[:lo], y[:lo])
        conf[lo:hi] = m.predict_proba(Xf.iloc[lo:hi])[:, 1]
    return conf


def lr():
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500))


def mdd(e):
    peak = np.maximum.accumulate(e); return float((e / peak - 1).min())


def equity_compare(p, c):
    rank = pd.Series(c).rank(pct=True).values
    lm = 1.0 + 2.0 * rank; lm *= 2.0 / lm.mean()       # same avg risk as fixed 2x
    fix = 1500 + np.cumsum(p * 2.0); dyn = 1500 + np.cumsum(p * lm)
    return (fix[-1] - 1500), (dyn[-1] - 1500), mdd(np.r_[1500, fix]), mdd(np.r_[1500, dyn]), lm


print("=== 1. WALK-FORWARD (expanding retrain, logistic) ===")
conf = walk_forward(lr, X)
m = ~np.isnan(conf); p, c = pnl[m], conf[m]
print(f"  scored {len(p)} trades; OOS AUC={roc_auc_score(y[m], c):.3f}")
q = pd.qcut(c, 5, labels=False, duplicates="drop")
print("  $exp per kuintil conf:", {f"Q{k+1}": round(float(p[q == k].mean()), 1) for k in sorted(np.unique(q))})
fn, dn, fdd, ddd, lm = equity_compare(p, c)
print(f"  FIXED net=${fn:+.0f} DD={100*fdd:.0f}%  |  ML net=${dn:+.0f} DD={100*ddd:.0f}%  "
      f"(ret {100*(dn-fn)/fn:+.0f}%, DD {100*(ddd-fdd):+.0f}pp)")

print("\n=== 2. ROBUSTNESS (resample) ===")
rng = np.random.default_rng(0)
rank = pd.Series(c).rank(pct=True).values; lm = 1.0 + 2.0 * rank; lm *= 2.0 / lm.mean()
win_ret = win_dd = 0; N = 2000
for _ in range(N):
    idx = rng.choice(len(p), len(p), replace=True)
    if (p[idx] * 2.0).sum() < (p[idx] * lm[idx]).sum():
        win_ret += 1
    perm = rng.permutation(len(p))
    if mdd(np.r_[1500, 1500 + np.cumsum(p[perm] * lm[perm])]) > mdd(np.r_[1500, 1500 + np.cumsum(p[perm] * 2.0)]):
        win_dd += 1
print(f"  ML beats fixed on RETURN in {100*win_ret/N:.0f}% of resamples")
print(f"  ML beats fixed on DRAWDOWN in {100*win_dd/N:.0f}% of order-permutations")

print("\n=== 3. FEATURE ABLATION (walk-forward AUC) ===")
for tag, cols in [("all", list(X.columns)),
                  ("tanpa zscore", [c for c in X.columns if c != "zscore"]),
                  ("zscore saja", ["zscore"])]:
    cf = walk_forward(lr, X[cols]); mm = ~np.isnan(cf)
    print(f"  {tag:14s}: AUC={roc_auc_score(y[mm], cf[mm]):.3f}")

print("\n=== 4. MODEL CHECK (gradient boosting, walk-forward) ===")
cf = walk_forward(lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=150,
                                                         learning_rate=0.05, l2_regularization=1.0), X)
mm = ~np.isnan(cf)
print(f"  HistGB AUC={roc_auc_score(y[mm], cf[mm]):.3f}  (logistic was {roc_auc_score(y[m], c):.3f})")
print("\nVONIS: layak bangun jika WF tetap monoton + ML menang >70% resample + bukan 1-fitur.")
