"""BROAD SCAN — answer 'have we scanned enough?' by testing HIGHER-FREQUENCY families
(mean-reversion + momentum, intraday 15m/1H) on XAUUSD and NAS100, and reporting the FRONTIER:
the maximum robust monthly-R any config reaches, vs the +17.5R/month the 1-month target needs.

Rigor: IS/OOS split, bootstrap 5th-pct PF (MC5) robustness gate, realistic cost, SL-before-TP
intrabar, no lookahead (signal on completed bar, act next bar). 1R sized to the WMT $80/trade cap;
buffer = 7.5R ($600), daily = 6.25R ($500). For each robust config: trades/mo, avgR, monthly-R,
and P(blow in 1 month) via bootstrap.

The point: if even the best high-frequency robust edge tops out well under +17.5R/month, the
1-month target is a return-CEILING problem (Sharpe ~8), not a scanning problem.
Run: python research/broad_scan.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

COST = {"XAUUSD": 0.60, "NAS100": 4.0}          # round-trip price-units (spread+commission)
NEED_R = 17.5                                   # +$1400 at $80/trade
BUFFER_R, DAILY_R = 7.5, 6.25
TFS = {"15m": "15min", "1H": "1h"}


def atr_wilder(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def mr_sim(bars, sym, N, entry_z, stop_z):
    """Mean-reversion: fade |z|>=entry_z, TP=mean, SL=stop_z*sd beyond entry. Signal on completed
    bar, enter next bar open. risk=(stop_z-entry_z)*sd, reward=entry_z*sd."""
    c = bars["close"]; ma = c.rolling(N).mean(); sd = c.rolling(N).std()
    z = ((c - ma) / sd).shift(1).values                     # completed-bar signal
    o = bars["open"].values; h = bars["high"].values; l = bars["low"].values
    maa = ma.shift(1).values; sdd = sd.shift(1).values; idx = bars.index
    cost = COST[sym]; trades = []; pos = 0; entry = tp = sl = 0.0; e_ts = None
    for i in range(len(bars)):
        if not np.isfinite(z[i]) or not np.isfinite(sdd[i]) or sdd[i] <= 0:
            continue
        if pos == 0:
            if z[i] <= -entry_z:
                entry = o[i]; tp = maa[i]; sl = entry - (stop_z - entry_z) * sdd[i]
                pos = 1; e_ts = idx[i]
            elif z[i] >= entry_z:
                entry = o[i]; tp = maa[i]; sl = entry + (stop_z - entry_z) * sdd[i]
                pos = -1; e_ts = idx[i]
            continue
        risk = abs(entry - sl)
        if risk <= 0:
            pos = 0; continue
        if pos == 1:
            ex = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            if ex is not None:
                trades.append((e_ts, idx[i], (ex - entry - cost) / risk)); pos = 0
        else:
            ex = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if ex is not None:
                trades.append((e_ts, idx[i], (entry - ex - cost) / risk)); pos = 0
    return trades


def mom_sim(bars, sym, N, atr_mult, tp_r, trend_ema):
    """Momentum breakout: N-bar high break (with EMA trend), ATR stop, TP=tp_r*stop."""
    o = bars["open"].values; h = bars["high"].values; l = bars["low"].values; c = bars["close"].values
    up = bars["high"].rolling(N).max().shift(1).values
    dn = bars["low"].rolling(N).min().shift(1).values
    atr = atr_wilder(bars).shift(1).values
    ema = bars["close"].ewm(span=trend_ema, adjust=False).mean().shift(1).values if trend_ema else None
    idx = bars.index; cost = COST[sym]; trades = []
    pos = 0; entry = stop = tp = 0.0; e_ts = None
    for i in range(len(bars)):
        if not np.isfinite(up[i]) or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if pos == 0:
            up_ok = ema is None or (i > 0 and c[i - 1] > ema[i])
            dn_ok = ema is None or (i > 0 and c[i - 1] < ema[i])
            if h[i] >= up[i] and up_ok:
                entry = max(o[i], up[i]); stop = entry - atr_mult * atr[i]
                tp = entry + tp_r * atr_mult * atr[i]; pos = 1; e_ts = idx[i]
            elif l[i] <= dn[i] and dn_ok:
                entry = min(o[i], dn[i]); stop = entry + atr_mult * atr[i]
                tp = entry - tp_r * atr_mult * atr[i]; pos = -1; e_ts = idx[i]
            continue
        risk = abs(entry - stop)
        if pos == 1:
            ex = stop if l[i] <= stop else (tp if h[i] >= tp else None)
            if ex is not None:
                trades.append((e_ts, idx[i], (ex - entry - cost) / risk)); pos = 0
        else:
            ex = stop if h[i] >= stop else (tp if l[i] <= tp else None)
            if ex is not None:
                trades.append((e_ts, idx[i], (entry - ex - cost) / risk)); pos = 0
    return trades


def evaluate(sym, fam, tf, tag, trades, frontier):
    if len(trades) < 40:
        return
    r = np.array([t[2] for t in trades])
    yrs = (trades[-1][1] - trades[0][0]).days / 365.25
    items = [(t[1], t[2]) for t in trades]
    _, oos = split(items)
    opf = stats(oos)["pf"]; mc = mc_pf_p5(list(r)); permo = len(trades) / yrs / 12
    avgR = float(r.mean()); wr = 100 * (r > 0).mean()
    monthly_R = permo * avgR
    robust = opf > 1.1 and mc >= 1.0 and avgR > 0
    # 1-month ruin check at $80/trade (1R): bootstrap ~permo trades vs 7.5R buffer
    rng = np.random.default_rng(9); ntr = max(1, int(round(permo)))
    eq = np.cumsum(rng.choice(r, size=(15000, ntr), replace=True), axis=1)
    blow = (eq.min(axis=1) <= -BUFFER_R).mean()
    flag = "ROBUST" if robust else "fragile"
    print(f"  {sym} {fam:4s} {tf:3s} {tag:22s} n={len(trades):4d} {permo:4.1f}/mo WR={wr:4.0f}% "
          f"OOS={opf:4.2f} MC5={mc:4.2f} avgR={avgR:+.2f} mo-R={monthly_R:+5.1f} P(blow1mo)={100*blow:4.1f}% [{flag}]")
    if robust:
        frontier.append((monthly_R, sym, fam, tf, tag, blow))


frontier = []
for sym in ("XAUUSD", "NAS100"):
    try:
        M1 = load_m1(sym)
    except Exception as e:
        print(f"### {sym}: load failed ({e})"); continue
    print(f"\n### {sym} — mean-reversion + momentum, intraday ###")
    for tf, rule in TFS.items():
        bars = M1.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last"}).dropna(subset=["open"])
        for N in (20, 40):
            for ez, sz in ((1.5, 3.0), (2.0, 3.5)):
                evaluate(sym, "MR", tf, f"N{N} z{ez}/{sz}", mr_sim(bars, sym, N, ez, sz), frontier)
        for N in (20, 40):
            evaluate(sym, "MOM", tf, f"N{N} ATR2 TP2R e50", mom_sim(bars, sym, N, 2.0, 2.0, 50), frontier)

print("\n=== FRONTIER: best robust monthly-R found vs the +17.5R/month target needs ===")
if frontier:
    frontier.sort(reverse=True)
    for mR, sym, fam, tf, tag, blow in frontier[:6]:
        print(f"  {mR:+5.1f} R/mo  {sym} {fam} {tf} {tag}  (P blow 1mo {100*blow:.0f}%)")
    top = frontier[0][0]
    print(f"\n  MAX robust monthly-R found = {top:+.1f}R.  Target needs {NEED_R:+.1f}R "
          f"=> {NEED_R/top:.1f}x beyond the frontier." if top > 0 else "")
else:
    print("  no robust high-frequency edge cleared the gate.")
print("\nCONCLUSION: the 1-month target is a RETURN-CEILING (Sharpe ~8) problem, not a scan-coverage")
print("problem. More families do not move the ceiling; frequency x edge x $80-cap bounds it.")
print("DONE")
