"""Phase 2.5 GATE: does ML-confidence sizing beat FIXED lot, out-of-sample?

Win-rate lift is not enough — what matters for sizing is $ expectancy and the
resulting equity curve. We assign each trade an OOS confidence (model trained only on
EARLIER trades via TimeSeriesSplit -> no leakage), then compare:
  - fixed lot (0.02, half-Kelly)
  - confidence-sized lot (0.01..0.03 by OOS confidence, averaging ~0.02 -> same risk)
If confidence-sizing improves return AND/OR drawdown OOS, the ML architecture is worth
building. Else: rules are enough; don't add ML complexity.

Run: python research/ml_sizing_sim.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from ml_metalabel import build, H1
from pipeline.backtest.strategy_zrev import simulate, ZRevParams, trades_to_df, resample_1h
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import TimeSeriesSplit


def pnl_for(use_daily):
    """Re-extract per-trade $ pnl aligned with build()'s filtering."""
    from audit_live_strategies import to_d1, load_m1
    import numpy as np
    XAU = load_m1("XAUUSD")
    T = trades_to_df(simulate(H1, ZRevParams(20, 0, 0.30, trend_filter=True, trend_ema=100)))
    T["entry_ts"] = pd.to_datetime(T["entry_ts"]); T["exit_ts"] = pd.to_datetime(T["exit_ts"])
    return T


def main():
    X, y = build(use_daily=False)              # 824 EMA100 trades (strongest ML)
    # rebuild pnl/exit aligned to the SAME rows build() kept: re-run feat filter
    from ml_metalabel import atr
    from audit_live_strategies import to_d1, load_m1
    XAU = load_m1("XAUUSD")
    T = trades_to_df(simulate(H1, ZRevParams(20, 0, 0.30, trend_filter=True, trend_ema=100)))
    T["entry_ts"] = pd.to_datetime(T["entry_ts"]); T["exit_ts"] = pd.to_datetime(T["exit_ts"])
    T["pnl"] = T["pnl_points"].astype(float)
    # the rows build() kept are those with valid features; reconstruct mask by entry in H1 + finite
    # simplest: build returns same order as iterating T with valid feats -> recompute that mask
    ema = H1["close"].ewm(span=100, adjust=False).mean(); a = atr(H1, 14)
    keep_idx = []
    for i, t in enumerate(T.itertuples()):
        e = t.entry_ts
        if e in H1.index and not np.isnan(float(a.loc[e])):
            keep_idx.append(i)
    T = T.iloc[keep_idx].reset_index(drop=True)
    pnl = T["pnl"].values
    exit_ts = T["exit_ts"].values
    assert len(pnl) == len(y), f"align mismatch {len(pnl)} vs {len(y)}"

    # OOS confidence per trade (train on earlier folds only)
    conf = np.full(len(y), np.nan)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        if len(np.unique(y[tr])) < 2:
            continue
        m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500)).fit(X.iloc[tr], y[tr])
        conf[te] = m.predict_proba(X.iloc[te])[:, 1]
    msk = ~np.isnan(conf)
    p = pnl[msk]; c = conf[msk]; xt = pd.DatetimeIndex(exit_ts[msk])

    # $ expectancy by OOS confidence quintile
    print(f"OOS trades scored: {len(p)}")
    q = pd.qcut(c, 5, labels=False, duplicates="drop")
    print("$ expectancy per 0.01 lot, by OOS confidence quintile (low->high):")
    for k in sorted(np.unique(q)):
        s = p[q == k]
        print(f"  Q{k+1}: n={len(s):3d} conf~{c[q==k].mean():.2f}  exp=${s.mean():+.2f}  WR={100*(s>0).mean():.0f}%")

    # equity: fixed vs confidence-sized (both avg ~2x => same risk budget)
    def mdd(e): peak = np.maximum.accumulate(e); return float((e / peak - 1).min())
    rank = pd.Series(c).rank(pct=True).values
    lot_mult = 1.0 + 2.0 * rank            # 0.01..0.03, mean ~0.02
    lot_mult *= 2.0 / lot_mult.mean()      # normalize to same avg risk as fixed 2x
    order = np.argsort(xt.values)
    pf = p[order]; lm = lot_mult[order]
    eq_fix = 1500 + np.cumsum(pf * 2.0)
    eq_dyn = 1500 + np.cumsum(pf * lm)
    print(f"\nEQUITY (same avg risk; start $1500):")
    print(f"  FIXED 0.02   : final ${eq_fix[-1]:.0f}  net ${eq_fix[-1]-1500:+.0f}  maxDD {100*mdd(np.r_[1500,eq_fix]):.0f}%")
    print(f"  ML-sized     : final ${eq_dyn[-1]:.0f}  net ${eq_dyn[-1]-1500:+.0f}  maxDD {100*mdd(np.r_[1500,eq_dyn]):.0f}%")
    imp_ret = (eq_dyn[-1] - eq_fix[-1]) / (eq_fix[-1] - 1500) * 100
    print(f"  -> return {imp_ret:+.0f}% vs fixed; DD {100*(mdd(np.r_[1500,eq_dyn])-mdd(np.r_[1500,eq_fix])):+.0f}pp")
    print("\nVONIS: ML-sizing layak dibangun HANYA jika net naik &/atau DD turun di sini.")


if __name__ == "__main__":
    main()
