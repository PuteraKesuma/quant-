"""Phase 2 GATE: can an ML meta-model predict whether a zrev trade WINS, out-of-sample?

This decides whether the 'ML dynamic lot' idea has any foundation. Meta-labeling:
the rule strategy generates the signal; the ML only scores P(win) from features known
AT ENTRY (no lookahead). We test with PURGED time-series CV (train past -> test future).

Honest bar: OOS AUC must beat 0.50 meaningfully AND high-confidence trades must win more
OOS (decile lift). With only hundreds of trades and the main signal already captured by
the trend rules, weak/no edge is the likely (and acceptable) finding.

Run: python research/ml_metalabel.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from pipeline.backtest.strategy_zrev import simulate, ZRevParams, trades_to_df, resample_1h, _adx
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score

XAU = load_m1("XAUUSD"); H1 = resample_1h(XAU.assign(volume=0))


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def build(use_daily):
    T = trades_to_df(simulate(H1, ZRevParams(20, 0, 0.30, trend_filter=True, trend_ema=100)))
    T["entry_ts"] = pd.to_datetime(T["entry_ts"]); T["pnl"] = T["pnl_points"].astype(float)
    ema = H1["close"].ewm(span=100, adjust=False).mean()
    a = atr(H1, 14); adx = _adx(H1, 14)
    ma = H1["close"].rolling(20).mean(); sd = H1["close"].rolling(20).std()
    up = H1["high"].rolling(20).max(); lo = H1["low"].rolling(20).min()
    d1 = to_d1(XAU); dc = d1["close"]; pc = dc.shift(1); dsma = dc.rolling(50).mean().shift(1)
    dmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(dsma.loc[ts]))
                        else (1 if pc.loc[ts] > dsma.loc[ts] else -1)) for ts in d1.index}

    def feat(t):
        e = t.entry_ts
        if e not in H1.index:
            return None
        dirn = 1 if t.direction == "long" else -1
        atr_e = float(a.loc[e]) or 1.0
        h1tr = float(H1["close"].loc[e] - ema.loc[e]) / atr_e
        dtr = dmap.get(e.date(), 0)
        return dict(
            dir=dirn,
            adx=float(adx.loc[e]),
            h1_align=float(np.sign(h1tr) == dirn),
            daily_align=float(dtr == dirn),
            vol=atr_e / float(H1["close"].loc[e]),
            zscore=(float(H1["close"].loc[e] - ma.loc[e]) / float(sd.loc[e]) if sd.loc[e] else 0.0) * dirn,
            chan_w=float(up.loc[e] - lo.loc[e]) / atr_e,
            hour=e.hour, dow=e.dayofweek,
        )
    rows = []; y = []
    for t in T.itertuples():
        f = feat(t)
        if f is None or any(np.isnan(v) for v in f.values()):
            continue
        if use_daily and not (f["h1_align"] and f["daily_align"]):
            continue                      # only the deployed (dual-aligned) trades
        rows.append(f); y.append(1 if t.pnl > 0 else 0)
    X = pd.DataFrame(rows); y = np.array(y)
    return X, y


def evaluate(tag, X, y):
    print(f"\n{tag}: {len(y)} trades, base win-rate={100*y.mean():.0f}%")
    if len(y) < 120:
        print("  (sampel terlalu kecil untuk ML yang andal — hati-hati overfit)")
    tscv = TimeSeriesSplit(n_splits=5)
    aucs = []; lifts = []
    for tr, te in tscv.split(X):
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
            continue
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500))
        model.fit(X.iloc[tr], y[tr])
        p = model.predict_proba(X.iloc[te])[:, 1]
        aucs.append(roc_auc_score(y[te], p))
        med = np.median(p)
        hi = y[te][p >= med].mean(); loo = y[te][p < med].mean()
        lifts.append(hi - loo)
    print(f"  OOS AUC (purged CV): mean={np.mean(aucs):.3f}  folds={[round(a,2) for a in aucs]}")
    print(f"  win-rate lift (top-half conf - bottom-half), OOS: {100*np.mean(lifts):+.1f}pp")
    # full-fit coefficients (direction of signal)
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500)).fit(X, y)
    co = dict(zip(X.columns, m.named_steps["logisticregression"].coef_[0]))
    print("  koef (besar = penting):", {k: round(v, 2) for k, v in sorted(co.items(), key=lambda z: -abs(z[1]))[:5]})
    verdict = "ADA sinyal OOS" if (np.mean(aucs) >= 0.55 and np.mean(lifts) > 0.05) else "TIDAK ada edge OOS (noise)"
    print(f"  VONIS: {verdict}")


if __name__ == "__main__":
    X, y = build(use_daily=False)
    evaluate("A) semua trade EMA100 (824)", X, y)
    X2, y2 = build(use_daily=True)
    evaluate("B) hanya trade dual-aligned (deployed) — apakah ML nambah?", X2, y2)
