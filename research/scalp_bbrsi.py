"""Bollinger-Band + RSI mean-reversion SCALP on M1 (small fixed TP, dynamic SL).

User idea: enter on every BB/RSI signal, tiny TP (~$2-3 = 2-3 pts on XAU @0.01 lot),
variable SL. Tested honestly because tiny-TP scalping lives or dies by SPREAD.

Backtest data: FBS M1 is only weeks deep (can't validate across regimes), so we run
the LONG Dukascopy M1 history for a real IS/OOS read AND model the FBS spread/slippage
as cost; we also run whatever FBS M1 exists as a broker-real sanity check.

No-lookahead: BB/RSI on completed closes (shift 1); entry at next-bar open. SL checked
before TP (pessimistic). IS<2025-01-01<=OOS.

Run: python research/scalp_bbrsi.py
"""
import sys
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from audit_live_strategies import stats, split, fmt, per_year, mc_pf_p5, CUT


def load_duka(sym):
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / f"{sym}_1m.duckdb"), read_only=True)
    rows = con.execute("SELECT epoch(ts),open,high,low,close FROM ohlcv ORDER BY ts").fetchall()
    con.close()
    a = np.asarray(rows, float)
    return pd.DataFrame({"open": a[:, 1], "high": a[:, 2], "low": a[:, 3], "close": a[:, 4]},
                        index=pd.to_datetime(a[:, 0], unit="s", utc=True))


def load_fbs(sym, n=300000):
    import MetaTrader5 as mt5
    mt5.initialize()
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, n)
    mt5.shutdown()
    if r is None or len(r) == 0:
        return None
    df = pd.DataFrame(r)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("ts")[["open", "high", "low", "close"]].sort_index()


def rsi(close, n=14):
    d = np.diff(close, prepend=close[0])
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).values


def atr(df, n=14):
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().values


def scalp(df, bb_n=20, bb_k=2.0, rsi_n=14, ob=70, os=30,
          tp_pts=2.0, sl_pts=None, sl_atr=None, cost_pts=0.5, max_hold=120):
    c = df["close"].values; H = df["high"].values; L = df["low"].values; O = df["open"].values
    ma = pd.Series(c).rolling(bb_n).mean()
    sd = pd.Series(c).rolling(bb_n).std()
    upper = (ma + bb_k * sd).shift(1).values
    lower = (ma - bb_k * sd).shift(1).values
    r = pd.Series(rsi(c, rsi_n)).shift(1).values
    a = pd.Series(atr(df, 14)).shift(1).values
    idx = df.index; n = len(df)
    trades = []; i = 1; busy = -1
    for i in range(n - 1):
        if i <= busy:
            continue
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        d = 0
        if c[i] <= lower[i] and r[i] <= os: d = 1          # oversold at lower band -> long
        elif c[i] >= upper[i] and r[i] >= ob: d = -1       # overbought at upper band -> short
        if d == 0:
            continue
        entry = O[i + 1]
        sl_dist = sl_atr * a[i] if (sl_atr and not np.isnan(a[i])) else (sl_pts or tp_pts)
        tp = entry + d * tp_pts
        sl = entry - d * sl_dist
        pnl = None; xi = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            if d == 1:
                if L[j] <= sl: pnl = -sl_dist - cost_pts; xi = j; break    # SL first
                if H[j] >= tp: pnl = tp_pts - cost_pts; xi = j; break
            else:
                if H[j] >= sl: pnl = -sl_dist - cost_pts; xi = j; break
                if L[j] <= tp: pnl = tp_pts - cost_pts; xi = j; break
        if pnl is None:
            xi = min(i + max_hold, n - 1); pnl = d * (c[xi] - entry) - cost_pts
        trades.append((idx[xi], pnl)); busy = xi
    return trades


def rep(tag, tr):
    if not tr:
        print(f"  {tag:34s} n=0"); return
    i_, o = split(tr); pnl = np.array([p for _, p in tr])
    wr = 100 * (pnl > 0).mean()
    eq = np.cumsum(pnl); mdd = (eq - np.maximum.accumulate(eq)).min()
    so = stats(o); si = stats(i_)
    pfo = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    pfi = "inf" if si["pf"] == float("inf") else f"{si['pf']:.2f}"
    print(f"  {tag:34s} n={len(tr):5d} WR={wr:3.0f}% ISpf={pfi:>4} OOSpf={pfo:>4} "
          f"net=${pnl.sum():+6.0f} maxDD=${mdd:6.0f} exp=${pnl.mean():+.3f}/trade")


def run(df, label, cost):
    print(f"\n=== {label} (cost={cost}pts/trade) ===")
    rep("TP2 SL2 (1:1)",            scalp(df, tp_pts=2, sl_pts=2, cost_pts=cost))
    rep("TP3 SL3 (1:1)",            scalp(df, tp_pts=3, sl_pts=3, cost_pts=cost))
    rep("TP2 SL6 (tinyTP wideSL)",  scalp(df, tp_pts=2, sl_pts=6, cost_pts=cost))
    rep("TP2 SL=1.5xATR (dynamic)", scalp(df, tp_pts=2, sl_atr=1.5, cost_pts=cost))
    rep("TP3 SL=2xATR (dynamic)",   scalp(df, tp_pts=3, sl_atr=2.0, cost_pts=cost))


def main():
    duka = load_duka("XAUUSD")
    print(f"Dukascopy XAU M1: {len(duka):,}  {duka.index.min().date()} -> {duka.index.max().date()}")
    run(duka, "DUKASCOPY 5.5y, FBS-spread modeled", cost=0.5)
    run(duka, "DUKASCOPY 5.5y, higher cost", cost=0.7)
    fbs = load_fbs("XAUUSD")
    if fbs is not None:
        wk = (fbs.index.max() - fbs.index.min()).days / 7
        print(f"\nFBS XAU M1: {len(fbs):,}  {fbs.index.min().date()} -> {fbs.index.max().date()} (~{wk:.0f} minggu)")
        print(f"=== FBS DATA (broker-real but only ~{wk:.0f} weeks -> NOT a validation) cost=0.5 ===")
        rep("TP2 SL2", scalp(fbs, tp_pts=2, sl_pts=2, cost_pts=0.5))
        rep("TP3 SL3", scalp(fbs, tp_pts=3, sl_pts=3, cost_pts=0.5))
        rep("TP2 SL=1.5xATR", scalp(fbs, tp_pts=2, sl_atr=1.5, cost_pts=0.5))


if __name__ == "__main__":
    main()
