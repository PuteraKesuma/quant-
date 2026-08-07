"""EDGE HUNT #2 — the OVERNIGHT drift. Documented: equity-index gains cluster OUTSIDE the US cash
session (hold overnight, avoid the day). Test on hourly Dukascopy: split each day into US-session
(14:00-21:00 UTC) vs OVERNIGHT (21:00-14:00), compare gross drift, then trade 'long overnight only'
NET of cost (2 transitions/day = the killer for this anomaly) with walk-forward + bootstrap MOE.

Run: python research/overnight_edge.py
"""
import os, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, dukascopy_python

np.random.seed(13)
ASSETS = {"NAS100": ("E_NQ-100", 0.0002), "SP500": ("E_SandP-500", 0.0002), "XAUUSD": ("XAU/USD", 0.0002)}
SESS_START, SESS_END = 14, 21           # US cash session hours in UTC (approx)
START, END = dt.datetime(2016, 1, 1), dt.datetime(2026, 7, 14)


def hourly(code):
    df = dukascopy_python.fetch(code, dukascopy_python.INTERVAL_HOUR_1, dukascopy_python.OFFER_SIDE_BID, START, END)
    df = df.rename(columns=str.lower); s = df["close"].copy(); s.index = pd.to_datetime(s.index, utc=True)
    return s.dropna()


def sharpe_d(r):
    r = r.dropna()
    return r.mean() / r.std() * np.sqrt(252) if (len(r) > 30 and r.std() > 0) else np.nan


def boot_ci(r, n=3000, block=15):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    sh = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); sh.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(sh, [2.5, 97.5])


FOLDS = [("2018-01-01","2020-01-01"),("2020-01-01","2022-01-01"),("2022-01-01","2024-01-01"),
         ("2024-01-01","2026-07-14")]

print(f"OVERNIGHT vs SESSION drift  (session {SESS_START}:00-{SESS_END}:00 UTC)\n")
for name, (code, cost) in ASSETS.items():
    s = hourly(code); lr = np.log(s).diff()
    hr = s.index.hour
    is_sess = (hr >= SESS_START) & (hr < SESS_END)
    day = s.index.normalize()
    # daily aggregates of log-returns
    sess_d = lr[is_sess].groupby(day[is_sess]).sum()
    over_d = lr[~is_sess].groupby(day[~is_sess]).sum()
    allr = pd.DataFrame({"sess": sess_d, "over": over_d}).dropna()
    ann_sess = allr["sess"].mean() * 252 * 100
    ann_over = allr["over"].mean() * 252 * 100
    # strategy: long overnight only, net 2 transitions/day cost
    net_over = allr["over"] - 2 * cost
    net_sess = allr["sess"] - 2 * cost
    bh = allr["sess"] + allr["over"]
    lo, hi = boot_ci(net_over)
    fp = sum(1 for a, b in FOLDS if np.isfinite(x := sharpe_d(net_over[(net_over.index >= pd.Timestamp(a, tz='UTC')) & (net_over.index < pd.Timestamp(b, tz='UTC'))])) and x > 0)
    print(f"=== {name} (n={len(allr)} days) ===")
    print(f"  gross drift:  SESSION {ann_sess:+.1f}%/yr   OVERNIGHT {ann_over:+.1f}%/yr   buy&hold {ann_sess+ann_over:+.1f}%/yr")
    print(f"  long-OVERNIGHT net-cost: Sharpe {sharpe_d(net_over):+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  "
          f"CAGR {np.expm1(net_over.mean()*252):+.1%}  WF+ {fp}/{len(FOLDS)}")
    print(f"  (ref) long-SESSION net:  Sharpe {sharpe_d(net_sess):+.2f}   buy&hold Sharpe {sharpe_d(bh):+.2f}\n")
print("read: keep long-overnight ONLY if net-cost 95% CI > 0 AND it beats buy&hold. The 2 trips/day"
      " cost usually eats it. Else discard.")
print("DONE")
