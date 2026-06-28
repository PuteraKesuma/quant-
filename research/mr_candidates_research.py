"""Search for a FILLABLE mean-reversion diversifier to replace the dead mr_xau.

The mr_xau artifact came from placing the stop relative to the MEAN (stop_z),
so any |z| >= stop_z put the stop on the wrong side of entry (unfillable). The fix
audited here: place the stop relative to ENTRY (ATR-based), so it is ALWAYS on the
correct side -> 0% invalid by construction. Then test whether a real edge survives.

Same skeptic gauntlet as research/audit_live_strategies.py:
  - market entry at the NEXT H1 bar open (what the live slot would send)
  - executability check against the actual fill (BUY: SL<entry<TP ; SELL: SL>entry>TP)
  - H1 screen with SL-checked-before-TP; M1-confirm the best candidate
  - no-lookahead indicators (.shift(1)); cost + one level higher
  - IS<2025-01-01<=OOS, per-year PF (green years), Monte-Carlo 5th-pct PF
  - correlation of monthly PnL vs zrev_xau (diversification value)

RESEARCH ONLY — does not touch config.yaml or the live brain.
Run: python research/mr_candidates_research.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))
from audit_live_strategies import (  # reuse audited helpers + data loaders
    load_m1, to_h1, stats, fmt, split, per_year, mc_pf_p5, CUT, zrev_audit,
)


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"],
                    (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close, n=2):
    d = np.diff(close, prepend=close[0])
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).values


# ---------------------------------------------------------------- candidate engines
def zfade_h1(h, N=20, ez=2.5, stop_atr=1.5, atr_n=14, max_hold=48, cost=0.30):
    """z-score fade, TP=mean, STOP = entry -/+ stop_atr*ATR (always beyond entry).
    Entry at next bar open. H1 walk, SL-before-TP. Returns (trades, invalid, n_signals)."""
    c = h["close"].values; H = h["high"].values; L = h["low"].values; O = h["open"].values
    ma = pd.Series(c).rolling(N).mean().shift(1).values
    sd = pd.Series(c).rolling(N).std().shift(1).values
    a = atr(h, atr_n).shift(1).values
    idx = h.index; n = len(h)
    trades = []; invalid = 0; sig = 0; busy = -1
    for i in range(n - 1):
        if i <= busy:
            continue
        if np.isnan(ma[i]) or np.isnan(sd[i]) or np.isnan(a[i]) or sd[i] <= 0 or a[i] <= 0:
            continue
        z = (c[i] - ma[i]) / sd[i]
        d = 1 if z <= -ez else (-1 if z >= ez else 0)
        if d == 0:
            continue
        sig += 1
        entry = O[i + 1]                       # market fill, next bar open
        tgt = ma[i]
        stop = entry - d * stop_atr * a[i]     # beyond entry by construction
        ok = (stop < entry < tgt) if d == 1 else (stop > entry > tgt)
        if not ok:
            invalid += 1
            continue
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


def rsi2_h1(h, low=5, high=95, trend_n=200, stop_atr=2.0, atr_n=14,
            exit_rsi=50, max_hold=48, cost=0.30, allow_short=True):
    """Connors-style RSI(2) reversion WITH trend filter: long only above SMA(trend_n),
    short only below. Exit when RSI crosses exit_rsi, ATR stop beyond entry, time-exit."""
    c = h["close"].values; H = h["high"].values; L = h["low"].values; O = h["open"].values
    r = rsi(c, 2)
    sma = pd.Series(c).rolling(trend_n).mean().shift(1).values
    a = atr(h, atr_n).shift(1).values
    rprev = pd.Series(r).shift(1).values        # RSI of completed bar (no lookahead)
    idx = h.index; n = len(h)
    trades = []; invalid = 0; sig = 0; busy = -1
    for i in range(n - 1):
        if i <= busy:
            continue
        if np.isnan(sma[i]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        d = 0
        if rprev[i] <= low and c[i] > sma[i]:        d = 1
        elif allow_short and rprev[i] >= high and c[i] < sma[i]:  d = -1
        if d == 0:
            continue
        sig += 1
        entry = O[i + 1]
        stop = entry - d * stop_atr * a[i]
        if not ((stop < entry) if d == 1 else (stop > entry)):
            invalid += 1
            continue
        pnl = None; xi = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            if d == 1:
                if L[j] <= stop:        pnl = stop - entry - cost; xi = j; break
                if r[j] >= exit_rsi:    pnl = c[j] - entry - cost; xi = j; break    # exit at close
            else:
                if H[j] >= stop:        pnl = entry - stop - cost; xi = j; break
                if r[j] <= exit_rsi:    pnl = entry - c[j] - cost; xi = j; break
        if pnl is None:
            xi = min(i + max_hold, n - 1); pnl = d * (c[xi] - entry) - cost
        trades.append((idx[xi], pnl)); busy = xi
    return trades, invalid, sig


def m1_confirm(m1, h, N=20, ez=2.5, stop_atr=1.5, atr_n=14, max_hold=48, cost=0.30):
    """Re-fill the z-fade candidate on M1 (honest intrabar, SL-before-TP)."""
    c = h["close"].values
    ma = pd.Series(c).rolling(N).mean().shift(1).values
    sd = pd.Series(c).rolling(N).std().shift(1).values
    a = atr(h, atr_n).shift(1).values
    sigs = []
    for i in range(len(h)):
        if np.isnan(ma[i]) or np.isnan(sd[i]) or np.isnan(a[i]) or sd[i] <= 0 or a[i] <= 0:
            continue
        z = (c[i] - ma[i]) / sd[i]
        d = 1 if z <= -ez else (-1 if z >= ez else 0)
        if d:
            sigs.append((h.index[i], d, ma[i], a[i]))
    trades = []; invalid = 0; busy = pd.Timestamp.min.tz_localize("UTC")
    for label, d, tgt, av in sigs:
        start = label + pd.Timedelta(hours=1)
        if start < busy:
            continue
        seg = m1.loc[start:start + pd.Timedelta(hours=max_hold)]
        if len(seg) == 0:
            continue
        entry = float(seg["open"].iloc[0])
        stop = entry - d * stop_atr * av
        ok = (stop < entry < tgt) if d == 1 else (stop > entry > tgt)
        if not ok:
            invalid += 1; continue
        H = seg["high"].values; L = seg["low"].values; C = seg["close"].values; ix = seg.index
        pnl = None; xts = ix[-1]
        for j in range(len(seg)):
            if d == 1:
                if L[j] <= stop: pnl = stop - entry - cost; xts = ix[j]; break
                if H[j] >= tgt:  pnl = tgt - entry - cost; xts = ix[j]; break
            else:
                if H[j] >= stop: pnl = entry - stop - cost; xts = ix[j]; break
                if L[j] <= tgt:  pnl = entry - tgt - cost; xts = ix[j]; break
        if pnl is None:
            pnl = d * (C[-1] - entry) - cost; xts = ix[-1]
        trades.append((xts, pnl)); busy = xts
    return trades, invalid, len(sigs)


def line(label, items, invalid, sig):
    is_, oos = split(items)
    py = per_year(items)
    green = sum(1 for pf, _, _ in py.values() if pf == float("inf") or pf >= 1.0)
    si, so = stats(is_), stats(oos)
    inv = f"{100*invalid/sig:.0f}%" if sig else "n/a"
    pfo = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    pfi = "inf" if si["pf"] == float("inf") else f"{si['pf']:.2f}"
    return (f"{label:38s} sig={sig:4d} inv={inv:>4} | IS PF={pfi:>4} n={si['n']:4d} | "
            f"OOS PF={pfo:>4} n={so['n']:3d} net={so['net']:+7.0f} wr={so['wr']:3.0f}% "
            f"grn={green}/{len(py)} MCp5={mc_pf_p5(oos):.2f}")


def main():
    xau = load_m1("XAUUSD")
    h = to_h1(xau)
    print(f"XAUUSD h1={len(h):,}  (PnL = $ per 0.01 lot)  IS<{CUT.date()}<=OOS\n")

    print("=== Candidate A: z-fade, TP=mean, ATR stop BEYOND entry (H1 screen) ===")
    best = None
    for N in (20, 50):
        for ez in (2.0, 2.5):
            for satr in (1.0, 1.5, 2.5):
                tr, inv, sig = zfade_h1(h, N=N, ez=ez, stop_atr=satr)
                _, oos = split(tr)
                so = stats(oos)
                lbl = f"N{N} ez{ez} stopATR{satr}"
                print("  " + line(lbl, tr, inv, sig))
                key = (so["pf"] if so["pf"] != float("inf") else 9, so["n"])
                if so["n"] >= 30 and (best is None or key > best[0]):
                    best = (key, dict(N=N, ez=ez, stop_atr=satr), tr, inv, sig)

    print("\n=== Candidate B: RSI(2) + SMA200 trend filter, ATR stop (H1 screen) ===")
    for satr in (1.5, 2.5):
        for short in (True, False):
            tr, inv, sig = rsi2_h1(h, stop_atr=satr, allow_short=short)
            print("  " + line(f"RSI2 stopATR{satr} short={short}", tr, inv, sig))

    if best is not None:
        p = best[1]
        print(f"\n=== M1-confirm best A: {p} ===")
        for cost in (0.30, 0.60):
            tr, inv, sig = m1_confirm(xau, h, N=p["N"], ez=p["ez"], stop_atr=p["stop_atr"], cost=cost)
            print(f"  cost {cost}: " + line(f"M1 {p['N']}/{p['ez']}/{p['stop_atr']}", tr, inv, sig))
        # per-year + correlation vs zrev (monthly)
        tr, inv, sig = m1_confirm(xau, h, N=p["N"], ez=p["ez"], stop_atr=p["stop_atr"])
        print("  per-year:", {yr: v[0] for yr, v in per_year(tr).items()})
        cand_m = pd.Series([x[1] for x in tr], index=pd.DatetimeIndex([x[0] for x in tr])).resample("MS").sum()
        zit, _, _ = zrev_audit(xau)
        z_m = pd.Series([x[1] for x in zit], index=pd.DatetimeIndex([x[0] for x in zit])).resample("MS").sum()
        j = pd.concat([cand_m.rename("cand"), z_m.rename("zrev")], axis=1).dropna()
        corr = j["cand"].corr(j["zrev"]) if len(j) > 6 else float("nan")
        print(f"  monthly corr vs zrev_xau: {corr:+.2f}  (lower/negative = better diversifier)")


if __name__ == "__main__":
    main()
