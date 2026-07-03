"""Full backtest + ML evaluation of the LIQUIDITY strategy (Supertrend flat-limit) on XAU, as
deployed: 15min ATR21x5.5, BUY LIMIT at the flat SUPPORT band / SELL LIMIT at the flat RESISTANCE,
one position, both-sides, TP $26 / SL $13 (broker-matched). Efficient M5 first-touch fill/exit
(no M1 hang). Then ML meta-labeling with PURGED time-series CV to test whether the win/loss of a
trade is PREDICTABLE (and whether ML-filtering improves PF) -- the honest test of an ML edge.
Run: python research/liquidity_backtest_ml.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, per_year, mc_pf_p5, CUT

SL_D, TP_D, COST = 13.0, 26.0, 0.30

M1 = load_m1("XAUUSD")
agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
M15 = M1.resample("15min").agg(agg).dropna(subset=["open"])
M5 = M1.resample("5min").agg(agg).dropna(subset=["open"])
print(f"XAU 15min {len(M15)} bars, 5min {len(M5)} bars ({M15.index[0]} -> {M15.index[-1]})  IS/OOS cut {CUT.date()}")


def supertrend(h, per=21, mult=5.5):
    hh = h["high"].values; ll = h["low"].values; cc = h["close"].values
    pc = np.roll(cc, 1); pc[0] = cc[0]
    tr = np.maximum(hh - ll, np.maximum(np.abs(hh - pc), np.abs(ll - pc)))
    atr = np.full(len(cc), np.nan); atr[per - 1] = tr[:per].mean()
    for i in range(per, len(cc)):
        atr[i] = (atr[i - 1] * (per - 1) + tr[i]) / per
    src = (hh + ll) / 2.0
    up = np.full(len(cc), np.nan); dn = np.full(len(cc), np.nan); trend = np.ones(len(cc), int)
    for i in range(len(cc)):
        if not np.isfinite(atr[i]):
            up[i] = src[i]; dn[i] = src[i]; trend[i] = 1; continue
        bu = src[i] - mult * atr[i]; bd = src[i] + mult * atr[i]
        u1 = up[i - 1] if i > 0 and np.isfinite(up[i - 1]) else bu
        d1 = dn[i - 1] if i > 0 and np.isfinite(dn[i - 1]) else bd
        up[i] = max(bu, u1) if (i > 0 and cc[i - 1] > u1) else bu
        dn[i] = min(bd, d1) if (i > 0 and cc[i - 1] < d1) else bd
        t = trend[i - 1] if i > 0 else 1
        if t == -1 and cc[i] > d1: t = 1
        elif t == 1 and cc[i] < u1: t = -1
        trend[i] = t
    return up, dn, trend, atr


up, dn, trend, atr15 = supertrend(M15)
# resting limit level = last FLAT band in the current trend (Pine lastLongLimit/lastShortLimit)
last_long = np.full(len(M15), np.nan); last_short = np.full(len(M15), np.nan)
age = np.zeros(len(M15), int)                       # bars since the last trend flip
ll = ls = np.nan
for i in range(len(M15)):
    age[i] = 0 if (i == 0 or trend[i] != trend[i - 1]) else age[i - 1] + 1
    if trend[i] == 1:
        if i == 0 or trend[i - 1] != 1: ll = np.nan
        ls = np.nan
        if i >= 2 and up[i] == up[i - 1] == up[i - 2]: ll = up[i]
    else:
        if i == 0 or trend[i - 1] != -1: ls = np.nan
        ll = np.nan
        if i >= 2 and dn[i] == dn[i - 1] == dn[i - 2]: ls = dn[i]
    last_long[i] = ll; last_short[i] = ls

# features on the 15min frame (all no-lookahead: use bar i's completed values)
ret20 = M15["close"].pct_change(20).values          # momentum
vol20 = M15["close"].pct_change().rolling(20).std().values
ema100 = M15["close"].ewm(span=100, adjust=False).mean().values
feat15 = pd.DataFrame({
    "atr": atr15, "trend": trend, "age": age, "ret20": ret20, "vol20": vol20,
    "band_w": (dn - up) / np.maximum(atr15, 1e-9),
    "dist_ema": (M15["close"].values - ema100) / np.maximum(atr15, 1e-9),
    "hour": M15.index.hour, "dow": M15.index.dayofweek,
}, index=M15.index)

# map the 15min resting levels + features onto M5 (forward-fill completed 15min values)
lvl = pd.DataFrame({"ll": last_long, "ls": last_short, "trend": trend}, index=M15.index)
m5 = M5.join(lvl.reindex(M5.index, method="ffill")).dropna(subset=["trend"])
lo = m5["low"].values; hi = m5["high"].values; idx5 = m5.index
mll = m5["ll"].values; mls = m5["ls"].values; mtr = m5["trend"].values
feat15_ff = feat15.reindex(M5.index, method="ffill").reindex(m5.index)

# --- sequential M5 sim (one position; first-touch fill then first-touch TP/SL, SL wins ties) ---
trades = []            # (exit_ts, R, side, fill_ts)
X_rows = []
pos = 0; entry = tp = sl = 0.0; fill_i = -1
for i in range(len(m5)):
    if pos == 0:
        if mtr[i] == 1 and np.isfinite(mll[i]) and lo[i] <= mll[i]:
            pos, entry = 1, mll[i]; sl, tp = entry - SL_D, entry + TP_D; fill_i = i
        elif mtr[i] == -1 and np.isfinite(mls[i]) and hi[i] >= mls[i]:
            pos, entry = -1, mls[i]; sl, tp = entry + SL_D, entry - TP_D; fill_i = i
        if pos != 0:
            X_rows.append(feat15_ff.iloc[i].to_dict())       # features at the fill bar
    else:
        r = None
        if pos == 1:
            if lo[i] <= sl: r = -(SL_D + COST) / SL_D
            elif hi[i] >= tp: r = (TP_D - COST) / SL_D
        else:
            if hi[i] >= sl: r = -(SL_D + COST) / SL_D
            elif lo[i] <= tp: r = (TP_D - COST) / SL_D
        if r is not None:
            trades.append((idx5[i], r, pos, idx5[fill_i])); pos = 0

items = [(t[0], t[1]) for t in trades]
allp = [p for _, p in items]
print(f"\n=== BACKTEST (15min flat-limit, both-sides, $13/$26) ===")
if len(items) < 20:
    print(f"  only {len(items)} trades"); sys.exit()
i_, o = split(items)
eq = np.cumsum(allp); mdd = (eq - np.maximum.accumulate(eq)).min()
s = stats(allp); py = per_year(items); grn = sum(1 for v in py.values() if v[0] >= 1.0)
print(f"  n={s['n']} WR={s['wr']:.0f}% PF={s['pf']:.2f} netR={sum(allp):+.0f} maxDD={mdd:.0f}R "
      f"green {grn}/{len(py)} | OOS-PF={stats(o)['pf']:.2f} MC5={mc_pf_p5(o):.2f}")
print("  per-year R:", {int(k): round(v, 0) for k, v in pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items])).groupby(pd.DatetimeIndex([t for t, _ in items]).year).sum().items()})

# ================= ML META-LABELING (purged time-series CV) =================
print("\n=== ML META-LABELING: can a model predict win/loss? (purged CV) ===")
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

X = pd.DataFrame(X_rows).astype(float).fillna(0.0)
y = np.array([1 if p > 0 else 0 for p in allp])       # win=1, loss=0
ts = pd.DatetimeIndex([t[3] for t in trades])         # fill time (for time-ordered CV)
order = np.argsort(ts.values)
X = X.iloc[order].reset_index(drop=True); y = y[order]; Rarr = np.array(allp)[order]
print(f"  {len(X)} trades, base win-rate {100*y.mean():.0f}%, features: {list(X.columns)}")

n = len(X); folds = 5; emb = max(5, n // 50)
gb_auc, lr_auc = [], []; oos_prob = np.full(n, np.nan)
for k in range(1, folds):
    cut = int(n * k / folds)
    tr_idx = np.arange(0, cut - emb); te_idx = np.arange(cut, int(n * (k + 1) / folds))
    if len(tr_idx) < 50 or len(te_idx) < 20: continue
    sc = StandardScaler().fit(X.iloc[tr_idx])
    Xtr, Xte = sc.transform(X.iloc[tr_idx]), sc.transform(X.iloc[te_idx])
    if len(np.unique(y[tr_idx])) < 2: continue
    gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=1).fit(X.iloc[tr_idx], y[tr_idx])
    lr = LogisticRegression(max_iter=1000).fit(Xtr, y[tr_idx])
    pg, pl = gb.predict_proba(X.iloc[te_idx])[:, 1], lr.predict_proba(Xte)[:, 1]
    if len(np.unique(y[te_idx])) == 2:
        gb_auc.append(roc_auc_score(y[te_idx], pg)); lr_auc.append(roc_auc_score(y[te_idx], pl))
    oos_prob[te_idx] = pg

print(f"  OOS AUC  gradient-boost {np.mean(gb_auc):.3f}   logistic {np.mean(lr_auc):.3f}   (0.50 = no skill)")
# feature importance (full-fit GB)
gb_full = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=1).fit(X, y)
imp = sorted(zip(X.columns, gb_full.feature_importances_), key=lambda z: -z[1])
print("  top features:", ", ".join(f"{c}={v:.2f}" for c, v in imp[:5]))

# does ML-filtering improve PF? take only OOS trades the model likes (prob >= median)
m = np.isfinite(oos_prob)
if m.sum() > 30:
    thr = np.median(oos_prob[m])
    keep = m & (oos_prob >= thr)
    base_pf = stats(list(Rarr[m]))["pf"]; filt_pf = stats(list(Rarr[keep]))["pf"]
    print(f"  ML-filter (keep prob>=median): PF {base_pf:.2f} -> {filt_pf:.2f} "
          f"(kept {keep.sum()}/{m.sum()} OOS trades)")
    print("  VERDICT:", "ML adds a real, usable edge" if (np.mean(gb_auc) > 0.55 and filt_pf > base_pf + 0.2)
          else "NO usable ML edge -- outcomes ~unpredictable (AUC~0.5), filtering doesn't help. Same as Z.")
print("\nDONE")
