"""Test a regime-aware structure TP for zrev: in CHOPPY/ranging conditions, bank profit
at a support/resistance distance instead of waiting for the channel reverse (which gives
back in chop). In TRENDING conditions, let it run (channel exit). The user's idea: 'TP at
support, but ga baku (brain-driven)'.

Operationalised:
  - TP for a long = entry + tp_atr*ATR (toward the next resistance); short = entry - tp_atr*ATR.
  - 'always' = TP every trade; 'ranging' = TP only when ADX(at entry) < adx_th (chop), else
    let the channel exit run (so trends are untouched).
Exit = first of {channel reverse, TP}. After a TP exit -> flat, re-enter on next breakout.

Measure OVERALL (OOS PF/net/DD/MC) AND the choppy DD window (27 Mar - 14 May 2026) net.
Honest bar: a win only if the choppy period improves WITHOUT wrecking overall PF.
Run: python research/structure_tp.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5
from pipeline.backtest.strategy_zrev import _adx

XAU = load_m1("XAUUSD")
H = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
       .dropna(subset=["open"]))
CHOP0, CHOP1 = pd.Timestamp("2026-03-27", tz="UTC"), pd.Timestamp("2026-05-14", tz="UTC")


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def sim(mode="none", tp_atr=4.0, adx_th=20.0, N=20, ema_n=100, dsma=50, cost=0.30):
    c = H["close"].values; Hi = H["high"].values; Lo = H["low"].values; O = H["open"].values
    up = pd.Series(H["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(H["low"]).rolling(N).min().shift(1).values
    ema = H["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H["close"] > ema).shift(1).values
    av = atr(H, 14).shift(1).values
    adx = _adx(H, 14).shift(1).values
    d1 = XAU["close"].resample("1D").last().dropna(); pc = d1.shift(1); sma = d1.rolling(dsma).mean().shift(1)
    dmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    dates = H.index.date; idx = H.index
    tr = []; pos = 0; ep = ets = None; tp = None
    for i in range(len(H)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(av[i]):
            continue
        dt = dmap.get(dates[i], 0)
        cl = bool(h1_up[i]) and dt == 1; cs = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            use_tp = mode != "none" and (mode == "always" or (not np.isnan(adx[i]) and adx[i] < adx_th))
            if Hi[i] >= up[i] and cl:
                pos, ep, ets = 1, max(O[i], up[i]), idx[i]; tp = (ep + tp_atr * av[i]) if use_tp else None
            elif Lo[i] <= lo[i] and cs:
                pos, ep, ets = -1, min(O[i], lo[i]), idx[i]; tp = (ep - tp_atr * av[i]) if use_tp else None
            continue
        x = None
        if pos == 1:
            if tp is not None and Hi[i] >= tp: x = tp                       # structure TP (bank profit)
            elif Lo[i] <= lo[i]: x = min(O[i], lo[i])                       # channel reverse
            if x is not None: tr.append((idx[i], (x - ep) - cost)); pos = 0
        else:
            if tp is not None and Lo[i] <= tp: x = tp
            elif Hi[i] >= up[i]: x = max(O[i], up[i])
            if x is not None: tr.append((idx[i], (ep - x) - cost)); pos = 0
    return pd.Series([p for _, p in tr], index=pd.DatetimeIndex([t for t, _ in tr]))


def rep(tag, s):
    items = list(zip(s.index, s.values)); _, o = split(items)
    eq = s.sort_index().cumsum(); dd = float((eq - eq.cummax()).min())
    chop = s[(s.index >= CHOP0) & (s.index <= CHOP1)].sum()
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    print(f"  {tag:26s} n={len(s):4d} OOSpf={pf:>4} net=${s.sum():+6.0f} maxDD=${dd:+7.0f} "
          f"MC={mc_pf_p5(o):.2f} | net@CHOP=${chop:+5.0f}")


def main():
    print("Structure/distance TP for zrev (entry identical). CHOP window = 27Mar-14May 2026:\n")
    rep("baseline (channel only)", sim("none"))
    print("ALWAYS TP (cap every trade):")
    for k in (3.0, 5.0, 8.0):
        rep(f"TP {k}xATR always", sim("always", tp_atr=k))
    print("RANGING-only TP (ADX<th -> TP; trend lari):")
    for th in (18, 22, 26):
        rep(f"TP 4xATR if ADX<{th}", sim("ranging", tp_atr=4.0, adx_th=th))
    for th in (18, 22):
        rep(f"TP 6xATR if ADX<{th}", sim("ranging", tp_atr=6.0, adx_th=th))
    print("\n  Menang HANYA jika net@CHOP NAIK & OOSpf/net keseluruhan tidak jatuh.")


if __name__ == "__main__":
    main()
