"""SEMI-MARTI PER-TRADE FORENSICS — study EACH trade, not the aggregate. Find WHERE it loses.

The EA fades BOTH sides blindly (sell overbought, buy oversold). Hypothesis: half the trades are
COUNTER to the higher-TF trend (shorting strength / buying weakness) and that's the bleed. This:
  1) For every raw entry (M5), measures MFE (max favorable) and MAE (max adverse) over the next
     W bars in $ — does price REVERT (fade works) or CONTINUE (falling knife)?
  2) Splits every trade by H1 trend at entry: WITH-trend fade (buy dip in uptrend / sell rip in
     downtrend) vs COUNTER-trend fade. Reports win-rate + $ expectancy per bucket.
  3) Tests the FIXED signal: fade only WITH the H1 trend, ATR stop + revert-to-mean TP, IS/OOS/MC5.
  4) Scans a small exit grid to see if ANY exit extracts positive expectancy.
Run: python research/semi_marti_forensics.py
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
W = 240   # forward window (~20h on M5) for MFE/MAE

M1 = load_m1("XAUUSD")
m5 = M1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
c = m5["close"]
macd_main = c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()
macd_sig = macd_main.rolling(9).mean()
mn, mx = macd_sig.rolling(NORM).min(), macd_sig.rolling(NORM).max()
macd_norm = ((macd_sig - mn) / (mx - mn).replace(0, np.nan) * 100)
pmn, pmx = c.rolling(NORM).min(), c.rolling(NORM).max()
price_norm = ((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100)
raw_sell = ((macd_norm >= HI) & (price_norm >= HI)).values
raw_buy = ((macd_norm <= LO) & (price_norm <= LO)).values

# H1 trend (EMA50 slope) mapped to each M5 bar
h1 = M1.resample("1h").agg({"close": "last"}).dropna()
ema = h1["close"].ewm(span=50, adjust=False).mean()
h1_trend = np.sign(ema.diff()).reindex(m5.index, method="ffill").fillna(0).values  # +1 up / -1 down

hi_arr = m5["high"].values; lo_arr = m5["low"].values; cl = c.values
n = len(m5)
fhigh = pd.Series(hi_arr).rolling(W).max().shift(-W).values   # max high of next W bars
flow = pd.Series(lo_arr).rolling(W).min().shift(-W).values

rows = []   # (side, with_trend, mfe, mae, ret60)
for i in range(n - W - 1):
    if raw_buy[i]:
        side = 1
    elif raw_sell[i]:
        side = -1
    else:
        continue
    entry = cl[i]
    if side == 1:
        mfe = fhigh[i] - entry; mae = entry - flow[i]
    else:
        mfe = entry - flow[i]; mae = fhigh[i] - entry
    with_trend = (side == 1 and h1_trend[i] > 0) or (side == -1 and h1_trend[i] < 0)
    ret60 = (cl[i + 60] - entry) * side
    rows.append((side, with_trend, mfe, mae, ret60))

df = pd.DataFrame(rows, columns=["side", "with_trend", "mfe", "mae", "ret60"])
print(f"### PER-TRADE FORENSICS (M5, {len(df)} raw entries, W={W} bars fwd) ###\n")
print("--- MFE vs MAE ($), does the fade REVERT or CONTINUE? ---")
for name, g in (("ALL", df), ("BUY (fade oversold)", df[df.side == 1]), ("SELL (fade overbought)", df[df.side == -1])):
    print(f"  {name:24s} n={len(g):5d}  MFE med=${g.mfe.median():5.1f}  MAE med=${g.mae.median():5.1f}  "
          f"MAE>MFE in {100*(g.mae > g.mfe).mean():4.0f}% of trades  ret@60bar med=${g.ret60.median():+.2f}")

print("\n--- WITH-trend vs COUNTER-trend fade (H1 EMA50) ---")
for name, g in (("WITH-trend fade", df[df.with_trend]), ("COUNTER-trend fade", df[~df.with_trend])):
    if len(g) == 0:
        continue
    print(f"  {name:20s} n={len(g):5d}  MFE med=${g.mfe.median():5.1f}  MAE med=${g.mae.median():5.1f}  "
          f"ret@60bar med=${g.ret60.median():+.2f}  mean=${g.ret60.mean():+.2f}")

# ---- FIXED strategy: fade only WITH H1 trend; ATR stop + revert-to-mean-ish TP ----
def atr_wilder(h, nn=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / nn, adjust=False).mean()
atr = atr_wilder(m5).shift(1).values
o = m5["open"].values


def sim_fixed(only_with_trend, am, tp_r):
    trades = []; pos = 0; entry = sl = tp = 0.0; e_ts = None; idx = m5.index
    for i in range(1, n):
        if pos == 0:
            if not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            sig = 0
            if raw_buy[i - 1]:
                sig = 1
            elif raw_sell[i - 1]:
                sig = -1
            if sig == 0:
                continue
            wt = (sig == 1 and h1_trend[i - 1] > 0) or (sig == -1 and h1_trend[i - 1] < 0)
            if only_with_trend and not wt:
                continue
            entry = o[i]; pos = sig; e_ts = idx[i]
            sl = entry - sig * am * atr[i]; tp = entry + sig * tp_r * am * atr[i]
        else:
            risk = abs(entry - sl)
            if pos == 1:
                ex = sl if lo_arr[i] <= sl else (tp if hi_arr[i] >= tp else None)
            else:
                ex = sl if hi_arr[i] >= sl else (tp if lo_arr[i] <= tp else None)
            if ex is not None:
                trades.append((e_ts, idx[i], (pos * (ex - entry) - COST) / risk)); pos = 0
    return trades


print("\n--- FIXED: fade only WITH H1 trend (buy dip in uptrend / sell rip in downtrend) ---")
best = None
for wt_only in (True, False):
    for am, tpr in ((1.5, 1.0), (2.0, 1.0), (2.0, 2.0), (3.0, 1.0)):
        tr = sim_fixed(wt_only, am, tpr)
        if len(tr) < 40:
            continue
        r = np.array([t[2] for t in tr]); _, oos = split([(t[1], t[2]) for t in tr])
        opf = stats(oos)["pf"]; mc = mc_pf_p5(list(r))
        tag = ("WITH-trend" if wt_only else "both-sides") + f" ATR{am} TP{tpr}R"
        rob = opf > 1.1 and mc >= 1.0 and r.mean() > 0
        print(f"  {tag:26s} n={len(tr):4d} WR={100*(r>0).mean():4.0f}% OOS={opf:4.2f} MC5={mc:4.2f} "
              f"avgR={r.mean():+.2f} {'[ROBUST]' if rob else ''}")
        if rob and (best is None or opf > best[1]):
            best = (tag, opf)
print("\nVERDICT:", f"FIXABLE via trend filter -> {best[0]} (OOS {best[1]:.2f})" if best
      else "even WITH-trend + tuned exits, no robust edge -> the entry is non-predictive, not just mis-exited.")
print("DONE")
