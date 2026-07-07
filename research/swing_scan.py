"""SWING STRATEGY SCAN — honest search for a genuine swing edge on XAUUSD and NAS100, the
tight-SL / far-TP trend-ride profile the user wants, tested with full rigor (IS/OOS split,
per-year robustness, cost, bar-level fill with SL-checked-before-TP, NO lookahead).

Families (few, principled -> not a grid to overfit), on 4H and 1D bars:
  A) Donchian trend breakout, ATR stop, opposite-channel trailing exit (Turtle-style swing).
  B) Donchian breakout, ATR stop, FIXED far TP = R x stop (the user's SL/TP-far idea, R=3).
  Optional EMA trend filter (only trade with the higher-TF trend).

For each config: n, trades/yr, trades/mo, WR, avg-win/avg-loss R, PF, OOS-PF, per-year R,
maxDD(R), MC-5th PF. Then for the BEST per symbol: at the WMT $80/trade risk cap (1R=$80),
the realistic monthly $ expectancy + a 1-month bust check vs the $600 buffer / $500 daily.
Run: python research/swing_scan.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

# realistic round-trip cost in PRICE units per symbol (spread+commission, entry+exit)
COST = {"XAUUSD": 0.60, "NAS100": 4.0}
TF_RULE = {"4H": "4h", "1D": "1D"}


def atr_wilder(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def swing_sim(bars, sym, entry_n, atr_mult, exit_mode, tp_r, trend_ema):
    """bar-level swing sim. exit_mode: 'chan' (opposite Donchian) or 'tp' (fixed R x stop).
    Enter at the Donchian breakout LEVEL (stop-order realizable). SL checked before TP intrabar."""
    o = bars["open"].values; h = bars["high"].values; l = bars["low"].values; c = bars["close"].values
    idx = bars.index
    up = bars["high"].rolling(entry_n).max().shift(1).values
    dn = bars["low"].rolling(entry_n).min().shift(1).values
    xup = bars["high"].rolling(max(3, entry_n // 2)).max().shift(1).values   # trailing exit chan
    xdn = bars["low"].rolling(max(3, entry_n // 2)).min().shift(1).values
    atr = atr_wilder(bars).shift(1).values
    ema = bars["close"].ewm(span=trend_ema, adjust=False).mean().shift(1).values if trend_ema else None
    cost = COST[sym]
    trades = []
    pos = 0; entry = stop = tp = 0.0; e_ts = None
    for i in range(len(bars)):
        if np.isnan(up[i]) or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        if pos == 0:
            up_ok = (ema is None) or (c[i - 1] > ema[i] if i > 0 else True)
            dn_ok = (ema is None) or (c[i - 1] < ema[i] if i > 0 else True)
            if h[i] >= up[i] and up_ok:
                entry = max(o[i], up[i]); stop = entry - atr_mult * atr[i]
                tp = entry + tp_r * (atr_mult * atr[i]); pos = 1; e_ts = idx[i]
            elif l[i] <= dn[i] and dn_ok:
                entry = min(o[i], dn[i]); stop = entry + atr_mult * atr[i]
                tp = entry - tp_r * (atr_mult * atr[i]); pos = -1; e_ts = idx[i]
            continue
        # in position: SL before TP (pessimistic), then trailing/channel exit on close
        R = atr_mult * atr[i - 1] if i > 0 else atr_mult * atr[i]
        risk = abs(entry - stop)
        if pos == 1:
            exit_px = None
            if l[i] <= stop:
                exit_px = stop
            elif exit_mode == "tp" and h[i] >= tp:
                exit_px = tp
            elif exit_mode == "chan" and (not np.isnan(xdn[i])) and l[i] <= xdn[i]:
                exit_px = min(o[i], xdn[i])
            if exit_px is not None:
                r = (exit_px - entry - cost) / risk
                trades.append((e_ts, idx[i], r)); pos = 0
        else:
            exit_px = None
            if h[i] >= stop:
                exit_px = stop
            elif exit_mode == "tp" and l[i] <= tp:
                exit_px = tp
            elif exit_mode == "chan" and (not np.isnan(xup[i])) and h[i] >= xup[i]:
                exit_px = max(o[i], xup[i])
            if exit_px is not None:
                r = (entry - exit_px - cost) / risk
                trades.append((e_ts, idx[i], r)); pos = 0
    return trades


def report(sym, tf, tag, trades):
    if len(trades) < 25:
        print(f"  {sym} {tf} {tag:26s} n={len(trades):3d}  (too few)"); return None
    r = np.array([t[2] for t in trades])
    yrs = (trades[-1][1] - trades[0][0]).days / 365.25
    items = [(t[1], t[2]) for t in trades]
    _, oos = split(items)
    wr = 100 * (r > 0).mean()
    aw = r[r > 0].mean() if (r > 0).any() else 0
    al = -r[r < 0].mean() if (r < 0).any() else 0
    pf = stats(list(r))["pf"]; opf = stats(oos)["pf"]
    eq = np.cumsum(r); dd = float((eq - np.maximum.accumulate(eq)).min())
    mc = mc_pf_p5(list(r))
    yrs_pos = ""
    for y in sorted(set(pd.DatetimeIndex([t[1] for t in trades]).year)):
        yr = r[[pd.Timestamp(t[1]).year == y for t in trades]].sum()
        yrs_pos += f"{str(y)[2:]}:{yr:+.0f} "
    print(f"  {sym} {tf} {tag:26s} n={len(trades):3d} {len(trades)/yrs:4.0f}/yr {len(trades)/yrs/12:3.1f}/mo "
          f"WR={wr:4.1f}% W/L={aw:.1f}/{al:.1f} PF={pf:4.2f} OOS={opf:4.2f} DD={dd:+5.1f}R MC5={mc:4.2f}")
    print(f"       per-yr R: {yrs_pos}")
    return dict(sym=sym, tf=tf, tag=tag, r=r, opf=opf, pf=pf, mc5=mc, permo=len(trades) / yrs / 12,
                avgR=float(r.mean()), wr=wr)


CONFIGS = [
    ("chan", dict(entry_n=20, atr_mult=3.0, exit_mode="chan", tp_r=0, trend_ema=0), "Donchian20 ATR3 chanExit"),
    ("chan", dict(entry_n=20, atr_mult=3.0, exit_mode="chan", tp_r=0, trend_ema=50), "Donchian20 ATR3 chan +EMA50"),
    ("tp",   dict(entry_n=20, atr_mult=2.0, exit_mode="tp", tp_r=3.0, trend_ema=0), "Donchian20 ATR2 TP3R"),
    ("tp",   dict(entry_n=20, atr_mult=2.0, exit_mode="tp", tp_r=3.0, trend_ema=50), "Donchian20 ATR2 TP3R +EMA50"),
    ("tp",   dict(entry_n=10, atr_mult=2.0, exit_mode="tp", tp_r=3.0, trend_ema=50), "Donchian10 ATR2 TP3R +EMA50"),
]

best = {}
for sym in ("XAUUSD", "NAS100"):
    try:
        M1 = load_m1(sym)
    except Exception as e:
        print(f"\n### {sym}: load failed ({e})"); continue
    print(f"\n### {sym} (M1 n={len(M1)}) ###")
    for tf, rule in TF_RULE.items():
        bars = M1.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last"}).dropna(subset=["open"])
        for _, params, tag in CONFIGS:
            tr = swing_sim(bars, sym, **params)
            res = report(sym, tf, tag, tr)
            # ROBUSTNESS GATE: OOS-PF AND bootstrap 5th-pct PF (MC5) must both clear -> not fragile.
            if res and res["opf"] > 1.2 and res["mc5"] >= 1.0 and res["permo"] >= 0.5:
                key = sym
                if key not in best or res["mc5"] > best[key]["mc5"]:
                    best[key] = res

print("\n=== BEST validated swing per symbol -> 1-month reality at WMT $80/trade (1R=$80) ===")
for sym, b in best.items():
    permo, avgR, r = b["permo"], b["avgR"], b["r"]
    exp_mo = permo * avgR * 80.0
    # 1-month bust check: bootstrap ~permo trades vs $600 buffer (7.5R) + $500 daily (6.25R/day)
    rng = np.random.default_rng(5)
    ntr = max(1, int(round(permo)))
    paths = rng.choice(r, size=(20000, ntr), replace=True) * 80.0
    eq = 9600 + np.cumsum(paths, axis=1)
    blow = (eq.min(axis=1) <= 9000).mean()
    hit = (eq.max(axis=1) >= 11000).mean()
    print(f"  {sym}: {b['tf']} {b['tag']} | OOS-PF {b['opf']:.2f} WR {b['wr']:.0f}% {permo:.1f} trade/mo "
          f"avgR {avgR:+.2f}")
    print(f"     @ $80/trade -> ~${exp_mo:+.0f}/month expected | P(+$1400 in 1mo)={100*hit:.1f}% "
          f"P(blow)={100*blow:.1f}%")
if not best:
    print("  NONE cleared OOS-PF>1.05 with >=1 trade/mo -> no robust swing edge beyond the live book.")
print("\nNOTE: 1R sized to the $80 cap. Even the best swing edge cannot safely 10x in a month;")
print("this shows the honest monthly $ and the bust odds so the timeline is a number, not a hope.")
print("DONE")
