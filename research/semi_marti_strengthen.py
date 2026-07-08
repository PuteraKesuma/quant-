"""STRENGTHEN the fixed Semi-Marti signal: WITH-trend fade is +edge but THIN (OOS 1.26, avgR +0.04).
Now that entries are WITH the H1 trend (buy dips in uptrend / sell rips in downtrend), let winners
RUN instead of capping at 1R. Test wider/trailing exits, stronger trend filters, and the pullback+
re-break confirmation on top. Goal: turn the thin edge into a real one (or prove it caps out).
Rigor: IS/OOS, MC5 gate, cost, SL-before-TP, no lookahead. Run: python research/semi_marti_strengthen.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

COST = 0.60
HI, LO, NORM = 80.0, 15.0, 100

M1 = load_m1("XAUUSD")
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
c = m5["close"]
macd_sig = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mn, mx = macd_sig.rolling(NORM).min(), macd_sig.rolling(NORM).max()
macd_norm = ((macd_sig - mn) / (mx - mn).replace(0, np.nan) * 100).values
pmn, pmx = c.rolling(NORM).min(), c.rolling(NORM).max()
price_norm = ((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).values
raw_buy = (np.nan_to_num(macd_norm, nan=50) <= LO) & (np.nan_to_num(price_norm, nan=50) <= LO)
raw_sell = (np.nan_to_num(macd_norm, nan=50) >= HI) & (np.nan_to_num(price_norm, nan=50) >= HI)

h1 = M1.resample("1h").agg({"close": "last"}).dropna()

def atr_wilder(h, nn=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / nn, adjust=False).mean()
atr = atr_wilder(m5).shift(1).values
o = m5["open"].values; hi_arr = m5["high"].values; lo_arr = m5["low"].values; cl = c.values
idx = m5.index; n = len(m5)


def trend_arr(ema_n, mode):
    ema = h1["close"].ewm(span=ema_n, adjust=False).mean()
    if mode == "slope":
        t = np.sign(ema.diff())
    else:  # price vs ema
        t = np.sign(h1["close"] - ema)
    return t.reindex(idx, method="ffill").fillna(0).values


def sim(trend, am, exit_mode, tp_r=1.0, trail_mult=2.0, confirm=False):
    """WITH-trend fade. exit_mode: 'tp' fixed R, 'trail' ATR chandelier, 'time' N-bar."""
    trades = []; pos = 0; entry = sl = tp = 0.0; e_ts = None; peak = 0.0
    pend_dir = 0; pend_pulled = False
    for i in range(1, n):
        if pos == 0:
            if not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            sig = 1 if raw_buy[i - 1] else (-1 if raw_sell[i - 1] else 0)
            # confirmation: require a pullback (norm crosses back) then re-signal
            if confirm:
                if sig != 0 and pend_dir == 0:
                    pend_dir, pend_pulled = sig, False; continue
                if pend_dir != 0:
                    nv = price_norm[i - 1]
                    if not pend_pulled:
                        if (pend_dir == 1 and nv > LO) or (pend_dir == -1 and nv < HI):
                            pend_pulled = True
                        continue
                    if sig == pend_dir and pend_pulled:
                        pass  # confirmed -> fall through to entry with sig
                    else:
                        continue
            if sig == 0:
                continue
            wt = (sig == 1 and trend[i - 1] > 0) or (sig == -1 and trend[i - 1] < 0)
            pend_dir = 0; pend_pulled = False
            if not wt:
                continue
            entry = o[i]; pos = sig; e_ts = idx[i]; peak = entry
            sl = entry - sig * am * atr[i]
            tp = entry + sig * tp_r * am * atr[i] if exit_mode == "tp" else 0.0
            hold = 0
            continue
        hold += 1
        risk = abs(entry - sl)
        ex = None
        if pos == 1:
            if lo_arr[i] <= sl:
                ex = sl
            elif exit_mode == "tp" and hi_arr[i] >= tp:
                ex = tp
            elif exit_mode == "trail":
                peak = max(peak, hi_arr[i]); nsl = peak - trail_mult * atr[i]
                if nsl > sl:
                    sl = nsl
                if lo_arr[i] <= sl:
                    ex = sl
            elif exit_mode == "time" and hold >= 120:
                ex = cl[i]
        else:
            if hi_arr[i] >= sl:
                ex = sl
            elif exit_mode == "tp" and lo_arr[i] <= tp:
                ex = tp
            elif exit_mode == "trail":
                peak = min(peak, lo_arr[i]); nsl = peak + trail_mult * atr[i]
                if nsl < sl:
                    sl = nsl
                if hi_arr[i] >= sl:
                    ex = sl
            elif exit_mode == "time" and hold >= 120:
                ex = cl[i]
        if ex is not None:
            trades.append((e_ts, idx[i], (pos * (ex - entry) - COST) / risk)); pos = 0
    return trades


def rep(tag, tr):
    if len(tr) < 40:
        print(f"  {tag:38s} n={len(tr):4d} (few)"); return None
    r = np.array([t[2] for t in tr]); _, oos = split([(t[1], t[2]) for t in tr])
    opf = stats(oos)["pf"]; mc = mc_pf_p5(list(r))
    yrs = (tr[-1][1] - tr[0][0]).days / 365.25; permo = len(tr) / yrs / 12
    rob = opf > 1.15 and mc >= 1.0 and r.mean() > 0
    mR = permo * r.mean()
    print(f"  {tag:38s} n={len(tr):4d} {permo:4.1f}/mo WR={100*(r>0).mean():4.0f}% OOS={opf:4.2f} "
          f"MC5={mc:4.2f} avgR={r.mean():+.2f} moR={mR:+4.1f} {'[ROBUST]' if rob else ''}")
    return (tag, opf, mc, r.mean(), mR) if rob else None


results = []
for ename, emode in (("slope50", ("slope", 50)), ("posEMA50", ("pos", 50)), ("posEMA100", ("pos", 100))):
    tr_arr = trend_arr(emode[1], emode[0])
    print(f"\n### trend={ename} ###")
    for am in (2.0, 3.0):
        r = rep(f"{ename} ATR{am} TP1R", sim(tr_arr, am, "tp", 1.0));  results.append(r)
        r = rep(f"{ename} ATR{am} TP3R", sim(tr_arr, am, "tp", 3.0));  results.append(r)
        r = rep(f"{ename} ATR{am} trail2", sim(tr_arr, am, "trail", trail_mult=2.0)); results.append(r)
        r = rep(f"{ename} ATR{am} trail3", sim(tr_arr, am, "trail", trail_mult=3.0)); results.append(r)
    r = rep(f"{ename} ATR3 TP1R +confirm", sim(tr_arr, 3.0, "tp", 1.0, confirm=True)); results.append(r)

good = [x for x in results if x]
print("\n=== BEST robust configs (ranked by monthly-R) ===")
for tag, opf, mc, avgR, mR in sorted(good, key=lambda z: -z[4])[:6]:
    print(f"  {tag:34s} OOS={opf:.2f} MC5={mc:.2f} avgR={avgR:+.2f} monthlyR={mR:+.1f}")
if good:
    top = max(good, key=lambda z: z[4])
    print(f"\n  at $80/trade the best does ~${top[4]*80:+.0f}/month robustly (thin but real).")
else:
    print("  no config beats the ATR3 TP1R baseline robustly -> edge caps ~OOS 1.26, avgR +0.04.")
print("DONE")
