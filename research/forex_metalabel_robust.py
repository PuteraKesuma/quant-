"""Robustness layer for the FX meta-label ensemble (forex_metalabel_scan.py).
Adds what the user asked for: WALK-FORWARD (5 purged/embargoed folds) + REGIME breakdown
(where the edge lives: ADX regime + trading session) + COST STRESS (1.0/1.5/2.0x spread).
Keep a pair ONLY if the gated edge is net-positive in MOST folds AND survives 1.5x cost.

Run: python research/forex_metalabel_robust.py            (all pairs with data)
     python research/forex_metalabel_robust.py EURUSD GBPUSD
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from forex_metalabel_scan import load_m5, features, label, MAJORS, PIP, SPREAD_PIPS, H

NFOLDS = 5
EMB = H * 2
GATES = (0.58, 0.60, 0.62, 0.65, 0.68, 0.72)


def build_samples(sym):
    h = load_m5(sym)
    if h is None or len(h) < 20000:
        return None
    feat, atr = features(h)
    lab = label(h, atr, PIP.get(sym, 0.0001))
    Xs, ys, rps = [], [], []
    for side, (win, rp) in zip((1, -1), lab):
        df = feat.copy(); df["side"] = side
        m = (~np.isnan(win)) & df.notna().all(axis=1).values
        Xs.append(df[m]); ys.append(win[m]); rps.append(rp[m])
    X = pd.concat(Xs); y = np.concatenate(ys); rp = np.concatenate(rps)
    # time order via the index (both side-blocks share timestamps)
    order = np.argsort(X.index.values, kind="mergesort")
    return X.iloc[order].reset_index(drop=True), y[order], rp[order]


def walkforward(X, y, rp):
    """Anchored expanding walk-forward. Returns OOS arrays (p,y,rp, fold) over folds 1..NFOLDS-1."""
    n = len(X); bnd = [int(n * i / NFOLDS) for i in range(NFOLDS + 1)]
    P = np.full(n, np.nan); F = np.full(n, -1)
    for k in range(1, NFOLDS):
        tr_end = bnd[k] - EMB
        ci = int(tr_end * 0.85)
        te0, te1 = bnd[k] + EMB, bnd[k + 1]
        if ci < 5000 or te0 >= te1:
            continue
        clf = HistGradientBoostingClassifier(max_iter=250, max_depth=4, learning_rate=0.05,
                                             l2_regularization=1.0, min_samples_leaf=200, random_state=0)
        clf.fit(X.iloc[:ci], y[:ci])
        iso = IsotonicRegression(out_of_bounds="clip").fit(clf.predict_proba(X.iloc[ci:tr_end])[:, 1], y[ci:tr_end])
        P[te0:te1] = iso.transform(clf.predict_proba(X.iloc[te0:te1])[:, 1])
        F[te0:te1] = k
    m = ~np.isnan(P)
    return P[m], y[m], rp[m], F[m], X.iloc[np.where(m)[0]]


def sess(hour):
    return np.where(hour < 7, "Asia", np.where(hour < 13, "London", np.where(hour < 21, "NY", "Late")))


def run(sym):
    s = build_samples(sym)
    if s is None:
        print(f"\n{sym}: no/insufficient data"); return None
    X, y, rp = s
    p, yte, rpte, fold, Xte = walkforward(X, y, rp)
    cost = SPREAD_PIPS.get(sym, 1.2); base = yte.mean()
    print(f"\n{sym}  OOS n={len(yte):,} (walk-forward {NFOLDS-1} folds)  base win={base:.3f}  pmax={p.max():.2f}  cost={cost}pip")
    # threshold sweep (aggregate OOS)
    print(f"  {'gate':>5} {'n':>6} {'win':>6} {'EV':>7} {'net':>8} folds+")
    best = None
    for g in GATES:
        m = p >= g; n = int(m.sum())
        if n < 60:
            print(f"  {g:>5.2f} {n:>6}   (too few)"); continue
        wr = yte[m].mean(); ev = (rpte[m] - cost).mean(); net = (rpte[m] - cost).sum()
        fp = sum(1 for k in range(1, NFOLDS) if (mm := m & (fold == k)).sum() >= 10 and (rpte[mm] - cost).sum() > 0)
        ftot = sum(1 for k in range(1, NFOLDS) if (m & (fold == k)).sum() >= 10)
        print(f"  {g:>5.2f} {n:>6} {wr:>6.3f} {ev:>+7.2f} {net:>+8.0f}  {fp}/{ftot}")
        if n >= 100 and ev > 0 and (best is None or ev > best["ev"]):
            best = dict(g=g, n=n, wr=wr, ev=ev, net=net, fp=fp, ftot=ftot, m=m)
    if not best:
        print("  -> NO profitable gate. REJECT."); return dict(sym=sym, robust=False)
    g = best["g"]; m = best["m"]
    # cost stress at the best gate
    stress = {c: round((rpte[m] - cost * c).sum(), 0) for c in (1.0, 1.5, 2.0)}
    print(f"  best gate {g:.2f}: win={best['wr']:.3f} EV={best['ev']:+.2f} net={best['net']:+.0f} "
          f"folds+={best['fp']}/{best['ftot']}  cost-stress net@[1x,1.5x,2x]={list(stress.values())}")
    # regime breakdown at the best gate
    reg_adx = pd.cut(Xte["adx"][m], [0, 18, 25, 40, 999], labels=["ADX<18", "18-25", "25-40", ">40"])
    print("  regime by ADX:  " + "  ".join(
        f"{lab}: n={int((reg_adx==lab).sum())} win={yte[m][reg_adx==lab].mean():.2f} ev={(rpte[m][reg_adx==lab]-cost).mean():+.2f}"
        for lab in ["ADX<18", "18-25", "25-40", ">40"] if (reg_adx == lab).sum() > 20))
    ss = sess(Xte["hour"][m].values)
    print("  regime by session:  " + "  ".join(
        f"{lab}: n={int((ss==lab).sum())} win={yte[m][ss==lab].mean():.2f} ev={(rpte[m][ss==lab]-cost).mean():+.2f}"
        for lab in ["Asia", "London", "NY", "Late"] if (ss == lab).sum() > 20))
    robust = (best["fp"] >= best["ftot"] - 1) and (stress[1.5] > 0)
    print(f"  VERDICT: {'ROBUST (survives folds + 1.5x cost)' if robust else 'FRAGILE (fails folds or 1.5x cost)'}")
    return dict(sym=sym, robust=robust, gate=g, **{k: best[k] for k in ("wr", "ev", "net", "fp", "ftot")})


if __name__ == "__main__":
    pairs = [a.upper() for a in sys.argv[1:]] or MAJORS
    print(f"FX META-LABEL ROBUSTNESS  walk-forward={NFOLDS-1} folds  horizon={H}  gates={GATES}")
    res = [r for pr in pairs if (r := run(pr)) is not None]
    print("\n==== ROBUST SURVIVORS ====")
    any_r = False
    for r in res:
        if r.get("robust"):
            any_r = True
            print(f"  {r['sym']:7} gate>={r['gate']:.2f} win={r['wr']:.3f} EV={r['ev']:+.2f}pip net={r['net']:+.0f} folds+={r['fp']}/{r['ftot']}")
    if not any_r:
        print("  (none survived — the tail edge is not robust across folds+cost. Honest negative.)")
    print("DONE")
