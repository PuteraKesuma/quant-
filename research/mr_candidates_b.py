"""Addendum to mr_candidates_research.py — the two most sensible fixes for a fade:
  (1) regime filter: only fade when ADX is LOW (ranging); fades die in trends.
  (2) move asset: z-fade / RSI(2) on EURUSD (FX mean-reverts more, and a non-gold
      MR also diversifies the gold Z strategy).
Same skeptic gauntlet. RESEARCH ONLY.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from audit_live_strategies import load_m1, to_h1, stats, split, per_year, mc_pf_p5, CUT
from pipeline.backtest.strategy_zrev import _adx
from mr_candidates_research import zfade_h1, rsi2_h1, atr, line


def zfade_adx(h, N=20, ez=2.5, stop_atr=2.5, atr_n=14, adx_max=25.0, adx_n=14,
              max_hold=48, cost=0.30):
    """z-fade but only ENTER when prior-bar ADX < adx_max (ranging regime)."""
    c = h["close"].values; H = h["high"].values; L = h["low"].values; O = h["open"].values
    ma = pd.Series(c).rolling(N).mean().shift(1).values
    sd = pd.Series(c).rolling(N).std().shift(1).values
    a = atr(h, atr_n).shift(1).values
    adx = _adx(h, adx_n).shift(1).values
    idx = h.index; n = len(h)
    trades = []; invalid = 0; sig = 0; busy = -1
    for i in range(n - 1):
        if i <= busy:
            continue
        if (np.isnan(ma[i]) or np.isnan(sd[i]) or np.isnan(a[i]) or np.isnan(adx[i])
                or sd[i] <= 0 or a[i] <= 0):
            continue
        if adx[i] >= adx_max:                       # skip trending regime
            continue
        z = (c[i] - ma[i]) / sd[i]
        d = 1 if z <= -ez else (-1 if z >= ez else 0)
        if d == 0:
            continue
        sig += 1
        entry = O[i + 1]; tgt = ma[i]; stop = entry - d * stop_atr * a[i]
        if not ((stop < entry < tgt) if d == 1 else (stop > entry > tgt)):
            invalid += 1; continue
        pnl = None; xi = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            if d == 1:
                if L[j] <= stop:  pnl = stop - entry - cost; xi = j; break
                if H[j] >= tgt:   pnl = tgt - entry - cost; xi = j; break
            else:
                if H[j] >= stop:  pnl = entry - stop - cost; xi = j; break
                if L[j] <= tgt:   pnl = entry - tgt - cost; xi = j; break
        if pnl is None:
            xi = min(i + max_hold, n - 1); pnl = d * (c[xi] - entry) - cost
        trades.append((idx[xi], pnl)); busy = xi
    return trades, invalid, sig


def main():
    xau = to_h1(load_m1("XAUUSD"))
    print(f"XAUUSD h1={len(xau):,}\n=== (1) XAU z-fade + ADX-low regime filter ===")
    for amax in (20.0, 25.0, 30.0):
        for satr in (1.5, 2.5):
            tr, inv, sig = zfade_adx(xau, adx_max=amax, stop_atr=satr)
            print("  " + line(f"ADX<{amax} stopATR{satr}", tr, inv, sig))

    print("\n=== (2) EURUSD (FX mean-reversion, non-gold diversifier) ===")
    eur = to_h1(load_m1("EURUSD"))
    print(f"EURUSD h1={len(eur):,}  (PnL in price points; 0.01 lot = $0.10/pip-ish, scale only)")
    for N in (20, 50):
        for ez in (2.0, 2.5):
            tr, inv, sig = zfade_h1(eur, N=N, ez=ez, stop_atr=2.5)
            print("  " + line(f"EUR z-fade N{N} ez{ez} stop2.5", tr, inv, sig))
    for satr in (1.5, 2.5):
        tr, inv, sig = rsi2_h1(eur, stop_atr=satr, allow_short=True)
        print("  " + line(f"EUR RSI2 stop{satr}", tr, inv, sig))
    tr, inv, sig = zfade_adx(eur, adx_max=25.0, stop_atr=2.5)
    print("  " + line("EUR z-fade ADX<25 stop2.5", tr, inv, sig))


if __name__ == "__main__":
    main()
