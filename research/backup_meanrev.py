"""Regime-conditional MEAN-REVERSION backup for XAU: only fade extremes when the
market is RANGING (low ADX) -- i.e. exactly when the trend-following zrev struggles.

Goal is NOT a standalone money-maker; it is to LOWER the combined book's drawdown and
RAISE its PF by earning during zrev's chop drawdowns. We therefore judge each backup
by the COMBINED (zrev EMA100 + backup) metrics, not by itself. (A losing backup still
can't help -- it must be >= breakeven; the mr_xau lesson.)

Fillable by construction: SL = entry -/+ sl_atr*ATR (always the correct side; 0%
invalid). No-lookahead (shift 1). $ at 0.01 lot ($1/point XAU). IS<2025-01-01<=OOS.
Run: python research/backup_meanrev.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5
from pipeline.backtest.strategy_zrev import simulate, ZRevParams, trades_to_df, resample_1h, _adx

XAU = load_m1("XAUUSD"); H1 = resample_1h(XAU.assign(volume=0))
ZDD0, ZDD1 = pd.Timestamp("2026-03-27", tz="UTC"), pd.Timestamp("2026-05-14", tz="UTC")  # zrev DD window


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def zrev_series():
    t = trades_to_df(simulate(H1, ZRevParams(20, 0, 0.30, trend_filter=True, trend_ema=100)))
    return pd.Series(t["pnl_points"].values, index=pd.to_datetime(t["exit_ts"]))


def backup_mr(N=20, ez=2.0, sl_atr=2.0, adx_max=25, atr_n=14, max_hold=48, cost=0.30):
    c = H1["close"].values; Hi = H1["high"].values; Lo = H1["low"].values; O = H1["open"].values
    ma = pd.Series(c).rolling(N).mean().shift(1).values
    sd = pd.Series(c).rolling(N).std().shift(1).values
    a = atr(H1, atr_n).shift(1).values
    adx = _adx(H1, 14).shift(1).values
    idx = H1.index; trades = []; busy = -1
    for i in range(len(H1) - 1):
        if i <= busy:
            continue
        if (np.isnan(ma[i]) or np.isnan(sd[i]) or np.isnan(a[i]) or np.isnan(adx[i])
                or sd[i] <= 0 or a[i] <= 0):
            continue
        if adx[i] >= adx_max:                       # ONLY ranging regime
            continue
        z = (c[i] - ma[i]) / sd[i]
        d = 1 if z <= -ez else (-1 if z >= ez else 0)
        if d == 0:
            continue
        entry = O[i + 1]; tgt = ma[i]; stop = entry - d * sl_atr * a[i]
        if not ((stop < entry < tgt) if d == 1 else (stop > entry > tgt)):
            continue
        pnl = None; xi = None
        for j in range(i + 1, min(i + 1 + max_hold, len(H1))):
            if d == 1:
                if Lo[j] <= stop: pnl = stop - entry - cost; xi = j; break
                if Hi[j] >= tgt: pnl = tgt - entry - cost; xi = j; break
            else:
                if Hi[j] >= stop: pnl = entry - stop - cost; xi = j; break
                if Lo[j] <= tgt: pnl = entry - tgt - cost; xi = j; break
        if pnl is None:
            xi = min(i + max_hold, len(H1) - 1); pnl = d * (c[xi] - entry) - cost
        trades.append((idx[xi], pnl)); busy = xi
    return pd.Series([p for _, p in trades], index=pd.DatetimeIndex([t for t, _ in trades]))


def mdd(s):
    e = s.sort_index().cumsum(); return float((e - e.cummax()).min())


def pf(s):
    w = s[s > 0].sum(); l = -s[s < 0].sum(); return (w / l) if l > 0 else float("inf")


def main():
    z = zrev_series()
    z_oos = z[z.index >= pd.Timestamp("2025-01-01", tz="UTC")]
    print(f"zrev EMA100 ALONE: net=${z.sum():+.0f} maxDD=${mdd(z):.0f} OOSpf={pf(z_oos):.2f} "
          f"net@zrevDD=${z[(z.index>=ZDD0)&(z.index<=ZDD1)].sum():+.0f}\n")
    print("backup (ADX-gated MR)        | STANDALONE        | COMBINED book (zrev+backup)")
    print("N  ez  slA adxMax  n   net$   OOSpf | comboDD$ comboPF dDD  corr  net@zrevDD")
    best = None
    for N in (15, 20):
        for ez in (2.0, 2.5):
            for sla in (1.5, 2.5):
                for ax in (20, 25, 30):
                    b = backup_mr(N=N, ez=ez, sl_atr=sla, adx_max=ax)
                    if len(b) < 30:
                        continue
                    b_oos = b[b.index >= pd.Timestamp("2025-01-01", tz="UTC")]
                    combo = pd.concat([z, b]).sort_index()
                    cdd = mdd(combo); ddd = cdd - mdd(z)        # improvement (positive = DD reduced)
                    zm = z.resample("MS").sum(); bm = b.resample("MS").sum()
                    corr = pd.concat([zm, bm], axis=1).fillna(0).corr().iloc[0, 1]
                    atzdd = b[(b.index >= ZDD0) & (b.index <= ZDD1)].sum()
                    print(f"{N:<2} {ez:<3} {sla:<3} {ax:<6} {len(b):<3} {b.sum():+6.0f} "
                          f"{pf(b_oos):<5.2f} | {cdd:+7.0f} {pf(combo):<6.2f} {ddd:+5.0f} {corr:+.2f}  {atzdd:+.0f}")
                    score = (cdd, pf(combo))   # higher (less negative) DD then higher PF
                    if b.sum() > 0 and (best is None or cdd > best[0][0]):
                        best = (score, dict(N=N, ez=ez, sl_atr=sla, adx_max=ax), b)
    if best:
        p = best[1]; b = best[2]; combo = pd.concat([z, b]).sort_index()
        print(f"\nBEST by combined DD: {p}")
        print(f"  zrev alone : net=${z.sum():+.0f} DD=${mdd(z):.0f} PF={pf(z):.2f}")
        print(f"  +backup    : net=${combo.sum():+.0f} DD=${mdd(combo):.0f} PF={pf(combo):.2f}")
        # walk-forward: 6-month combined windows
        cm = combo.resample("6MS").sum()
        print(f"  walk-forward (6-mo combined): {int((cm>0).sum())}/{len(cm)} hijau")


if __name__ == "__main__":
    main()
