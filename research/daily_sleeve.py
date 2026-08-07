"""STEP 1 — deployable daily signal generator for the 3-sleeve diversified book
(GOLD trend + NAS trend + USDJPY carry). Fetches FRESH daily bars from Dukascopy each run,
computes each sleeve's current target (direction + vol-scaled risk weight), prints them, and
writes _MONITOR/daily_sleeve.json for a daily executor to act on. NOT wired to live orders yet
(waits for robustness [step 2] + live-book reconciliation [step 3]).

Run: python research/daily_sleeve.py
"""
import os, json, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, dukascopy_python

OUT = r"C:\Quant\_MONITOR\daily_sleeve.json"
START = dt.datetime(2015, 1, 1); END = dt.datetime.utcnow()
RATEDIFF_NOW = 3.23                     # US-JP rate diff (%) implied by FBS broker swap quotes (2026-07-15)
BROKER_MARKUP = 2.32                    # measured FBS swap markup (%/yr) -> NET carry a long actually banks
ASSETS = {"GOLD": "XAU/USD", "NAS": "E_NQ-100", "JPY": "USD/JPY"}


def daily(code):
    df = dukascopy_python.fetch(code, dukascopy_python.INTERVAL_DAY_1, dukascopy_python.OFFER_SIDE_BID, START, END)
    df = df.rename(columns=str.lower); s = df["close"].copy(); s.index = pd.to_datetime(s.index, utc=True)
    return s.resample("1D").last().dropna()


def vol_weight(c, cap=3.0, target=0.10):
    v = c.pct_change().rolling(50).std().iloc[-1] * np.sqrt(252)
    return float(min(cap, target / v)) if v and v > 0 else 0.0


def donchian_signal(c, N=100):
    up = c.rolling(N).max().shift(1).iloc[-1]; dn = c.rolling(N).min().shift(1).iloc[-1]
    px = c.iloc[-1]
    return 1 if px >= up else (-1 if px <= dn else None)   # None = hold prior (needs state)


def main():
    sig = {}; print(f"DAILY SLEEVE signals  {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC")
    c = {k: daily(v) for k, v in ASSETS.items()}
    # GOLD / NAS: Donchian-100 S&R (breakout); hold prior if inside channel (report last breakout dir)
    for name in ("GOLD", "NAS"):
        s = c[name]; up = s.rolling(100).max().shift(1); dn = s.rolling(100).min().shift(1)
        pos = pd.Series(np.nan, index=s.index); pos[s >= up] = 1; pos[s <= dn] = -1
        d = pos.ffill().iloc[-1]; w = vol_weight(s)
        sig[name] = {"dir": ("LONG" if d == 1 else "SHORT" if d == -1 else "FLAT"),
                     "risk_weight": round(w, 2), "px": round(float(s.iloc[-1]), 2),
                     "chan_hi": round(float(up.iloc[-1]), 2), "chan_lo": round(float(dn.iloc[-1]), 2)}
    # JPY carry: long only when NET carry (rate diff - broker markup) > 0 AND close>SMA100; else flat.
    # NET gate matters: at real FBS swap the sleeve is only Sharpe ~0.36 (was 0.70 on the idealized +3%),
    # and eating negative net-carry (trend-only gate) blew maxDD out to -23% -> must sit out those regimes.
    s = c["JPY"]; sma = s.rolling(100).mean().iloc[-1]
    net_carry = RATEDIFF_NOW - BROKER_MARKUP
    long_ok = (net_carry > 0) and (s.iloc[-1] > sma)
    sig["JPY"] = {"dir": "LONG" if long_ok else "FLAT", "risk_weight": round(vol_weight(s), 2),
                  "px": round(float(s.iloc[-1]), 3), "sma100": round(float(sma), 3),
                  "carry_pct": RATEDIFF_NOW, "net_carry_pct": round(net_carry, 2),
                  "note": "carry sleeve: long only when NET carry (after ~2.3%/yr FBS swap markup) > 0"}
    WEIGHTS = {"GOLD": 1.0, "NAS": 1.0, "JPY": 0.5}     # LOCKED 2026-07-13: JPY at 0.5x (balanced, Sharpe 0.77/DD -17%)
    for k in sig: sig[k]["book_weight"] = WEIGHTS[k]
    out = {"ts": dt.datetime.utcnow().isoformat() + "Z", "book": "gold+nas+jpy_daily_sleeve",
           "weights": WEIGHTS, "equal_risk_within_weight": True, "signals": sig,
           "status": "SHADOW (not live until go-live wiring approved)"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f: json.dump(out, f, indent=2)
    for k, v in sig.items():
        print(f"  {k:5} {v['dir']:6} risk_wt={v['risk_weight']}  px={v['px']}  " +
              (f"chan[{v['chan_lo']}..{v['chan_hi']}]" if "chan_hi" in v else f"sma100={v.get('sma100')} carry={v.get('carry_pct')}%"))
    print(f"\nwrote {OUT}  (status: {out['status']})")
    print("DONE")


if __name__ == "__main__":
    main()
