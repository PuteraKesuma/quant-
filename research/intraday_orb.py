"""EDGE HUNT #7 (INTRADAY / frequency lever) — London-session Opening-Range Breakout across gold + 7 FX
majors. Each asset that works = a semi-independent intraday stream; stacking many raises combined Sharpe
via sqrt-breadth (the real path past the daily correlation ceiling). Rule: opening range = 07:00-07:30
UTC; first breakout in the trend direction (daily SMA50) -> enter at boundary, SL opposite, TP 1R,
time-exit 16:00 UTC. Cost = spread per trade. Reports per-asset Sharpe + CI + inter-asset correlation.

Run: python research/intraday_orb.py
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from walkforward_trend import sharpe

np.random.seed(61)
ASSETS = {"XAUUSD": 0.00010, "EURUSD": 0.00007, "GBPUSD": 0.00009, "USDJPY": 0.00007,
          "USDCHF": 0.00009, "AUDUSD": 0.00009, "USDCAD": 0.00010, "NZDUSD": 0.00013}
OPEN, RNG_END, SESS_END, SMA = "07:00", "07:30", "16:00", 50


def orb_daily_ret(sym, cost):
    m1 = load_m1(sym)
    d1 = to_d1(m1); sma = d1["close"].rolling(SMA).mean()
    trend = (d1["close"].shift(1) > sma.shift(1))            # yesterday's close vs SMA (no lookahead)
    out = {}
    for date, day in m1.groupby(m1.index.normalize()):
        rng = day.between_time(OPEN, RNG_END)
        if len(rng) < 5: continue
        hi, lo = rng["high"].max(), rng["low"].min(); rs = hi - lo
        if rs <= 0: continue
        key = pd.Timestamp(date)
        up = trend.get(key, np.nan)
        if pd.isna(up): continue
        after = day.between_time(RNG_END, SESS_END)
        entry = d = sl = tp = None
        for t, b in after.iterrows():
            if up and b["high"] >= hi: entry, d, sl, tp = hi, 1, lo, hi + rs; break
            if (not up) and b["low"] <= lo: entry, d, sl, tp = lo, -1, hi, lo - rs; break
        if entry is None: continue
        # walk to exit
        seg = after.loc[t:]; exitp = seg["close"].iloc[-1]
        for _, b in seg.iterrows():
            if d == 1:
                if b["low"] <= sl: exitp = sl; break
                if b["high"] >= tp: exitp = tp; break
            else:
                if b["high"] >= sl: exitp = sl; break
                if b["low"] <= tp: exitp = tp; break
        pnl = d * (exitp - entry) / entry - cost
        out[key] = out.get(key, 0.0) + pnl
    return pd.Series(out).sort_index()


def boot(r, n=2500, block=10):
    r = r.dropna().values; N = len(r); nb = max(1, N // block); o = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); o.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(o, [2.5, 97.5])


print("LONDON-SESSION ORB (07:00-07:30 UTC range, trend-filtered breakout, 1R TP)\n")
print(f"{'asset':8} {'days':>5} {'Sharpe':>7} {'95% CI':>16} {'win%':>5}")
S = {}
for sym, cost in ASSETS.items():
    try:
        r = orb_daily_ret(sym, cost)
    except Exception as e:
        print(f"{sym:8} ERROR {e}"); continue
    if len(r) < 100: print(f"{sym:8} too few"); continue
    S[sym] = r; lo, hi = boot(r); wr = (r > 0).mean()
    print(f"{sym:8} {len(r):>5} {sharpe(r):>7.2f}  [{lo:+.2f},{hi:+.2f}]  {wr:>4.0%}{'  <-CI>0' if lo>0 else ''}")

if len(S) >= 2:
    M = pd.DataFrame({k: v for k, v in S.items()}).fillna(0)
    print("\ninter-asset correlation (ORB streams):")
    print(M.corr().round(2).to_string())
    basket = M.mean(axis=1)
    lo, hi = boot(basket)
    ac = M.corr().values[np.triu_indices(len(M.columns), 1)].mean()
    print(f"\nORB BASKET ({len(S)} assets): Sharpe {sharpe(basket):+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  avg pair-corr {ac:+.2f}")
    print("(low pair-corr + positive = the frequency/breadth lever working)")
print("DONE")
