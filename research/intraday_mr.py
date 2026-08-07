"""EDGE HUNT #8 (INTRADAY, different mechanism) — intraday MEAN-REVERSION on FX majors + gold.
FX is known to mean-revert intraday (opposite of breakout, which just died). 15-min bars inside the
London/NY window: z = (close - SMA20)/std20; fade z<-2 (long) / z>2 (short), exit |z|<0.5 or session
end, flat overnight. Cost per trade. Multiple trades/day = the frequency lever. Per-asset Sharpe + CI
+ inter-asset correlation + a combined basket.

Run: python research/intraday_mr.py
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from walkforward_trend import sharpe

np.random.seed(67)
ASSETS = {"XAUUSD": 0.00010, "EURUSD": 0.00007, "GBPUSD": 0.00009, "USDJPY": 0.00007,
          "USDCHF": 0.00009, "AUDUSD": 0.00009, "USDCAD": 0.00010, "NZDUSD": 0.00013}
SESS_A, SESS_B, ENTRY_Z, EXIT_Z, LB = "07:00", "20:00", 2.0, 0.5, 20


def mr_daily_ret(sym, cost):
    m1 = load_m1(sym)
    b = (m1.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
           .dropna(subset=["close"]))
    b = b.between_time(SESS_A, SESS_B)
    c = b["close"]; ma = c.rolling(LB).mean(); sd = c.rolling(LB).std()
    z = ((c - ma) / sd).values; ret = c.pct_change().values
    day = b.index.normalize()
    pos = np.zeros(len(c)); cur = 0.0
    for i in range(1, len(c)):
        if day[i] != day[i - 1]: cur = 0.0                     # flat across day boundary
        if cur == 0:
            if z[i] < -ENTRY_Z: cur = 1.0
            elif z[i] > ENTRY_Z: cur = -1.0
        elif cur == 1 and z[i] >= -EXIT_Z: cur = 0.0
        elif cur == -1 and z[i] <= EXIT_Z: cur = 0.0
        pos[i] = cur
    pos = pd.Series(pos, index=b.index)
    r = pos.shift(1).fillna(0).values * ret
    turn = np.abs(np.diff(np.concatenate([[0], pos.values])))
    pnl = pd.Series(r - turn * cost, index=b.index).fillna(0)
    return pnl.groupby(pnl.index.normalize()).sum()


def boot(r, n=2500, block=10):
    r = r.dropna().values; N = len(r); nb = max(1, N // block); o = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); o.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(o, [2.5, 97.5])


print("INTRADAY MEAN-REVERSION (15min z-score fade, session-only, flat overnight)\n")
print(f"{'asset':8} {'days':>5} {'Sharpe':>7} {'95% CI':>16} {'win%':>5}")
S = {}
for sym, cost in ASSETS.items():
    try:
        r = mr_daily_ret(sym, cost)
    except Exception as e:
        print(f"{sym:8} ERROR {e}"); continue
    if len(r) < 100: continue
    S[sym] = r; lo, hi = boot(r); wr = (r > 0).mean()
    print(f"{sym:8} {len(r):>5} {sharpe(r):>7.2f}  [{lo:+.2f},{hi:+.2f}]  {wr:>4.0%}{'  <-CI>0' if lo>0 else ''}")

if len(S) >= 2:
    M = pd.DataFrame(S).fillna(0)
    print("\ninter-asset correlation:")
    print(M.corr().round(2).to_string())
    basket = M.mean(axis=1); lo, hi = boot(basket)
    ac = M.corr().values[np.triu_indices(len(M.columns), 1)].mean()
    print(f"\nMR BASKET ({len(S)} assets): Sharpe {sharpe(basket):+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  avg pair-corr {ac:+.2f}")
print("DONE")
