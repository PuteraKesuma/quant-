"""EDGE HUNT #2b — overnight/session on gold+indices, done RIGHT: real FBS spread (per-crossing =
spread/2mid) AND the overnight financing swap (gold long -6.3%/yr, NAS long -1.44%/yr — charged at the
21:00 UTC rollover the overnight hold crosses). Two variants:
  (A) long-overnight-only (flat session) — keeps some beta
  (B) long-overnight + short-session (beta-neutral) — isolates the session anomaly, could be UNCORRELATED
Full rigor: real cost+swap, per-fold WF, block-bootstrap 95% CI, vs buy&hold, + corr to the gold trend sleeve.

Run: python research/overnight_edge2.py
"""
import os, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, dukascopy_python
from walkforward_trend import COST, sharpe

np.random.seed(21)
# (dukascopy code, per-CROSSING cost = spread/2mid measured, long overnight swap %/yr)
ASSETS = {"XAUUSD": ("XAU/USD", 0.000048, -6.30), "NAS100": ("E_NQ-100", 0.000039, -1.44),
          "SP500": ("E_SandP-500", 0.000039, -1.44)}
SESS_START, SESS_END = 14, 21
START, END = dt.datetime(2016, 1, 1), dt.datetime(2026, 7, 14)
FOLDS = [("2018-01-01","2020-01-01"),("2020-01-01","2022-01-01"),("2022-01-01","2024-01-01"),("2024-01-01","2026-07-14")]


def hourly(code):
    df = dukascopy_python.fetch(code, dukascopy_python.INTERVAL_HOUR_1, dukascopy_python.OFFER_SIDE_BID, START, END)
    df = df.rename(columns=str.lower); s = df["close"].copy(); s.index = pd.to_datetime(s.index, utc=True)
    return s.dropna()


def shp(r):
    r = r.dropna(); return r.mean() / r.std() * np.sqrt(252) if (len(r) > 30 and r.std() > 0) else np.nan


def boot(r, n=3000, block=15):
    r = r.dropna().values; N = len(r); nb = max(1, N // block)
    out = []
    for _ in range(n):
        idx = (np.random.randint(0, N - block, nb)[:, None] + np.arange(block)).ravel()
        s = r[idx]; sd = s.std(); out.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    return np.percentile(out, [2.5, 97.5])


def wf(r):
    return [shp(r[(r.index >= pd.Timestamp(a, tz='UTC')) & (r.index < pd.Timestamp(b, tz='UTC'))]) for a, b in FOLDS]


# gold trend sleeve (daily, for correlation check)
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
def gold_trend():
    c = px["XAUUSD"].dropna(); ret = c.pct_change()
    up = c.rolling(100).max().shift(1); dn = c.rolling(100).min().shift(1)
    pos = pd.Series(np.nan, index=c.index); pos[c >= up] = 1; pos[c <= dn] = -1; pos = pos.ffill().shift(1)
    sc = (0.10 / (ret.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
    return pos * sc * ret
gt = gold_trend()

for name, (code, xcost, swap_yr) in ASSETS.items():
    s = hourly(code); lr = np.log(s).diff(); hr = s.index.hour
    is_sess = (hr >= SESS_START) & (hr < SESS_END); day = s.index.normalize()
    sess_d = lr[is_sess].groupby(day[is_sess]).sum()
    over_d = lr[~is_sess].groupby(day[~is_sess]).sum()
    d = pd.DataFrame({"sess": sess_d, "over": over_d}).dropna()
    sw = swap_yr / 100 / 252                                     # daily long-swap (negative)
    A = d["over"] + sw - 2 * xcost                              # long overnight only: 1 round trip + swap
    B = d["over"] - d["sess"] + sw - 4 * xcost                  # long ON + short session: 2 round trips + swap on long
    bh = d["over"] + d["sess"]
    print(f"=== {name} (n={len(d)}d)  gross session {d['sess'].mean()*252*100:+.1f}%/yr  overnight {d['over'].mean()*252*100:+.1f}%/yr ===")
    for tag, r in (("A long-overnight only", A), ("B long-ON + short-sess (neutral)", B)):
        lo, hi = boot(r); folds = wf(r); fp = sum(1 for x in folds if np.isfinite(x) and x > 0)
        keep = " <== SURVIVES" if lo > 0 else ""
        print(f"   {tag:34} Sharpe {shp(r):+.2f} 95%CI[{lo:+.2f},{hi:+.2f}] CAGR {np.expm1(r.mean()*252):+.1%} WF+ {fp}/4{keep}")
        if "neutral" in tag:
            j = pd.concat([r.rename('x'), gt.rename('g')], axis=1).dropna()
            print(f"   {'':34} corr to gold-trend sleeve = {j['x'].corr(j['g']):+.2f}")
    print(f"   buy&hold Sharpe {shp(bh):+.2f}\n")
print("keep ONLY if a variant's 95% CI > 0 (and if it beats buy&hold or is uncorrelated+positive). Else discard.")
print("DONE")
