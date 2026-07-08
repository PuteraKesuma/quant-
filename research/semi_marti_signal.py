"""SEMI-MARTINGALE SIGNAL — backtest the BARE signal (no martingale) to see if it has a real edge.

Faithful port of Semi_Martingale_full.mq5 v7.0 (Voyager Labs, "optimized for XAUUSD") signal:
  - MACD signal line (5/13/9) min-max normalized to 0-100 over the last `norm`(100) bars.
  - Price min-max normalized 0-100 over `norm` bars (stochastic-like).
  - AND mode: SELL when BOTH >= 80, BUY when BOTH <= 15  (overbought/oversold FADE).
  - Optional pullback+re-break confirmation (drop back below the level, then re-cross).

The EA has NO per-trade stop — it relies on martingale + a $10 global basket TP. That HIDES the
signal's true quality (baskets close at small profit until a whipsaw wipes them). Here we strip the
martingale and trade it FLAT: one position, a REAL ATR stop + TP, cost, SL-before-TP, no lookahead.
If the bare signal has an edge, some config clears OOS-PF>1.1 AND MC5>=1.0. If nothing clears, the
"accuracy" was the martingale illusion. Tested on M5/M15/M30 (EA runs on the chart TF).
Run: python research/semi_marti_signal.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

COST = 0.60          # XAU round-trip price units
HI, LO, NORM = 80.0, 15.0, 100
TFS = {"M5": "5min", "M15": "15min", "M30": "30min"}


def atr_wilder(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def signals(bars):
    c = bars["close"]
    macd_main = c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()
    macd_sig = macd_main.rolling(9).mean()
    mn, mx = macd_sig.rolling(NORM).min(), macd_sig.rolling(NORM).max()
    macd_norm = (macd_sig - mn) / (mx - mn).replace(0, np.nan) * 100
    pmn, pmx = c.rolling(NORM).min(), c.rolling(NORM).max()
    price_norm = (c - pmn) / (pmx - pmn).replace(0, np.nan) * 100
    raw_sell = (macd_norm >= HI) & (price_norm >= HI)
    raw_buy = (macd_norm <= LO) & (price_norm <= LO)
    return raw_buy.fillna(False).values, raw_sell.fillna(False).values, macd_norm.values, price_norm.values


def confirm(raw, norm_series, level, is_high, K=8):
    """pullback+re-break: after a raw signal, require the norm to drop below (high) / rise above
    (low) the level, then re-cross, within K bars. Returns a boolean entry array."""
    out = np.zeros(len(raw), bool)
    waiting = pulled = False
    wt = 0
    for i in range(len(raw)):
        v = norm_series[i]
        if not np.isfinite(v):
            waiting = pulled = False
            continue
        if not waiting:
            if raw[i]:
                waiting, pulled, wt = True, False, 0
        else:
            wt += 1
            if wt > K:
                waiting = pulled = False
                if raw[i]:
                    waiting, pulled, wt = True, False, 0
                continue
            if not pulled:
                if (is_high and v < level) or ((not is_high) and v > level):
                    pulled = True
            else:
                if raw[i]:
                    out[i] = True
                    waiting = pulled = False
    return out


def sim(bars, use_confirm, atr_mult, tp_r):
    rb, rs, mn, pn = signals(bars)
    if use_confirm:
        eb = confirm(rb, pn, LO, False)
        es = confirm(rs, pn, HI, True)
    else:
        eb, es = rb, rs
    o = bars["open"].values; h = bars["high"].values; l = bars["low"].values
    atr = atr_wilder(bars).shift(1).values
    idx = bars.index
    trades = []; pos = 0; entry = sl = tp = 0.0; e_ts = None
    for i in range(1, len(bars)):
        if pos == 0:
            if not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            if eb[i - 1]:           # signal on completed bar -> enter this bar open
                entry = o[i]; sl = entry - atr_mult * atr[i]; tp = entry + tp_r * atr_mult * atr[i]
                pos = 1; e_ts = idx[i]
            elif es[i - 1]:
                entry = o[i]; sl = entry + atr_mult * atr[i]; tp = entry - tp_r * atr_mult * atr[i]
                pos = -1; e_ts = idx[i]
            continue
        risk = abs(entry - sl)
        if pos == 1:
            ex = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            if ex is not None:
                trades.append((e_ts, idx[i], (ex - entry - COST) / risk)); pos = 0
        else:
            ex = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if ex is not None:
                trades.append((e_ts, idx[i], (entry - ex - COST) / risk)); pos = 0
    return trades


def report(tf, tag, trades):
    if len(trades) < 40:
        print(f"  {tf} {tag:28s} n={len(trades):4d} (too few)"); return None
    r = np.array([t[2] for t in trades])
    yrs = (trades[-1][1] - trades[0][0]).days / 365.25
    _, oos = split([(t[1], t[2]) for t in trades])
    opf = stats(oos)["pf"]; mc = mc_pf_p5(list(r))
    wr = 100 * (r > 0).mean(); permo = len(trades) / yrs / 12
    robust = opf > 1.1 and mc >= 1.0 and r.mean() > 0
    py = ""
    for y in sorted(set(pd.DatetimeIndex([t[1] for t in trades]).year)):
        py += f"{str(y)[2:]}:{r[[pd.Timestamp(t[1]).year==y for t in trades]].sum():+.0f} "
    print(f"  {tf} {tag:28s} n={len(trades):4d} {permo:4.1f}/mo WR={wr:4.0f}% OOS={opf:4.2f} "
          f"MC5={mc:4.2f} avgR={r.mean():+.2f} {'[ROBUST]' if robust else ''}")
    print(f"       per-yr R: {py}")
    return robust


any_robust = False
M1 = load_m1("XAUUSD")
print("### Semi-Marti BARE signal on XAUUSD (no martingale, real ATR stop) ###")
for tf, rule in TFS.items():
    bars = M1.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last"}).dropna(subset=["open"])
    for conf in (False, True):
        ctag = "confirm" if conf else "raw"
        for am, tr in ((2.0, 1.0), (2.0, 2.0), (3.0, 1.0)):
            if report(tf, f"{ctag} ATR{am} TP{tr}R", sim(bars, conf, am, tr)):
                any_robust = True

print("\n=== VERDICT ===")
print("bare signal has a robust edge (some config OOS>1.1 & MC5>=1)" if any_robust
      else "NO config is robust -> the signal has NO standalone edge; its 'accuracy' was the\n"
           "martingale closing baskets at small profit until a whipsaw. Stripping martingale\n"
           "leaves a losing/no-edge MR fade (matches the broad-scan MR result). Do NOT deploy.")
print("DONE")
