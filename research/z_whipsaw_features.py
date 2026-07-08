"""Can Z whipsaws be PREDICTED at entry? Compare entry features (H1 ADX, H1 MACD-momentum, price
position) between Z WINNERS and Z LOSERS. If losers are separable -> a filter can cut whipsaws.
If winners/losers look the same at entry -> whipsaws are structural/unpredictable (honest answer;
the practical mitigation is diversification, not prediction). Run: python research/z_whipsaw_features.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from zrev_dual_trend import sim_dual, daily_map

M1 = load_m1("XAUUSD")
h1 = M1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
c = h1["close"]

def adx(h, n=14):
    up = h["high"].diff(); dn = -h["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()
ADX = adx(h1)
# MACD momentum (same as Golden's macd line, H1), normalized 0-100 over 100 bars
macd = (c.ewm(span=5, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()).rolling(9).mean()
mnn = ((macd - macd.rolling(100).min()) / (macd.rolling(100).max() - macd.rolling(100).min()).replace(0, np.nan) * 100)
# price position in its 100-bar range
ppos = ((c - c.rolling(100).min()) / (c.rolling(100).max() - c.rolling(100).min()).replace(0, np.nan) * 100)

rows = []
for e, x, d, p in sim_dual(dmap=daily_map(50), use_daily=True):
    rows.append((e, d, p, ADX.asof(e), mnn.asof(e), ppos.asof(e)))
df = pd.DataFrame(rows, columns=["e", "dir", "pnl", "adx", "macdN", "ppos"]).dropna()
W = df[df.pnl > 0]; L = df[df.pnl < 0]
print(f"Z trades: {len(df)}  winners {len(W)}  losers {len(L)}\n")
print(f"{'feature':10} {'WINNERS med':>12} {'LOSERS med':>12}  (separable if very different)")
for col, lbl in (("adx", "H1 ADX"), ("macdN", "MACD-norm"), ("ppos", "price-pos")):
    print(f"  {lbl:10} {W[col].median():>12.1f} {L[col].median():>12.1f}")

# does a momentum-confirmation filter separate them? long wants macdN high; short wants macdN low.
df["confirm"] = np.where(df.dir == "long", df.macdN, 100 - df.macdN)
print(f"\n  momentum-confirm (long:macdN, short:100-macdN):  winners med {W.assign(cf=np.where(W.dir=='long',W.macdN,100-W.macdN)).cf.median():.1f}  "
      f"losers med {L.assign(cf=np.where(L.dir=='long',L.macdN,100-L.macdN)).cf.median():.1f}")

print("\n--- IF we skip Z entries with confirm < thr (post-hoc, approximate) ---")
print("  (caveat: Z is always-in S&R; dropping entries isn't perfectly faithful -> indicative only)")
for thr in (30, 40, 50, 60):
    keep = df[df.confirm >= thr]
    drop = df[df.confirm < thr]
    kept_pf = keep[keep.pnl > 0].pnl.sum() / max(1e-9, -keep[keep.pnl < 0].pnl.sum())
    print(f"    confirm>={thr}: keeps {len(keep):3d} trades net ${keep.pnl.sum():+6.0f} PF={kept_pf:4.2f} | "
          f"skips {len(drop):3d} whose net was ${drop.pnl.sum():+6.0f} (loss-avoided if negative)")
print("\nread: if 'skips' net is strongly NEGATIVE and 'keeps' PF rises, the filter cuts whipsaws.")
print("      if skips net is ~flat/positive, the filter also cuts winners = whipsaws NOT separable.")
print("DONE")
