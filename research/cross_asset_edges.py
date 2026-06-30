"""Hunt UNCORRELATED trend edges on other instruments by transferring the validated
zrev framework (always-in Donchian S&R entry20/exit20 + H1 EMA100 + Daily SMA50 gate).
A new edge only helps if it (a) has its own edge AND (b) is low-correlated to the gold
zrev book -> lowers portfolio DD / raises PF (the only honest way to grow).

FX cost modeled in pips. PnL in price points (PF is scale-invariant; correlation uses
monthly point-sums). IS<2025-01-01<=OOS. Run: python research/cross_asset_edges.py
"""
import sys
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import stats, split, fmt, per_year, mc_pf_p5

ROOT = Path(r"C:\Quant")


def load(sym):
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / f"{sym}_1m.duckdb"), read_only=True)
    rows = con.execute("SELECT epoch(ts),open,high,low,close FROM ohlcv ORDER BY ts").fetchall()
    con.close()
    a = np.asarray(rows, float)
    return pd.DataFrame({"open": a[:, 1], "high": a[:, 2], "low": a[:, 3], "close": a[:, 4]},
                        index=pd.to_datetime(a[:, 0], unit="s", utc=True))


def dual_sr(m1, cost, N=20, ema_n=100, dsma=50):
    H1 = (m1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna(subset=["open"]))
    c = H1["close"].values; H = H1["high"].values; L = H1["low"].values; O = H1["open"].values
    up = pd.Series(H1["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(H1["low"]).rolling(N).min().shift(1).values
    ema = H1["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H1["close"] > ema).shift(1).values
    d1 = (m1["close"].resample("1D").last().dropna())
    pc = d1.shift(1); sma = d1.rolling(dsma).mean().shift(1)
    dmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    dates = H1.index.date; idx = H1.index
    trades = []; pos = 0; ep = ets = None
    for i in range(len(H1)):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            continue
        dt = dmap.get(dates[i], 0)
        can_long = bool(h1_up[i]) and dt == 1
        can_short = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            if H[i] >= up[i] and can_long: pos, ep, ets = 1, max(O[i], up[i]), idx[i]
            elif L[i] <= lo[i] and can_short: pos, ep, ets = -1, min(O[i], lo[i]), idx[i]
            continue
        if pos == 1 and L[i] <= lo[i]:
            f = min(O[i], lo[i]); trades.append((idx[i], (f - ep) - cost))
            pos, ep, ets = (-1, f, idx[i]) if can_short else (0, None, None)
        elif pos == -1 and H[i] >= up[i]:
            f = max(O[i], up[i]); trades.append((idx[i], (ep - f) - cost))
            pos, ep, ets = (1, f, idx[i]) if can_long else (0, None, None)
    return pd.Series([p for _, p in trades], index=pd.DatetimeIndex([t for t, _ in trades]))


def report(sym, s):
    items = list(zip(s.index, s.values)); i_, o = split(items)
    eq = s.sort_index().cumsum(); mdd = float((eq - eq.cummax()).min())
    py = per_year(items); g = sum(1 for v in py.values() if v[0] >= 1.0)
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    si = stats(i_); pfi = "inf" if si["pf"] == float("inf") else f"{si['pf']:.2f}"
    print(f"  {sym:8s} n={len(s):4d} ISpf={pfi:>4} OOSpf={pf:>4} maxDD={mdd:+8.1f} "
          f"MCp5={mc_pf_p5(o):.2f} green{g}/{len(py)}")
    return s.resample("MS").sum()


def main():
    print("zrev framework (dual-TF S&R) transferred to other instruments:")
    print("  (PnL in price points; cost: XAU 0.30, FX 0.00015 = ~1.5 pip)\n")
    xau_m = report("XAUUSD", dual_sr(load("XAUUSD"), 0.30))
    eur_m = report("EURUSD", dual_sr(load("EURUSD"), 0.00015))
    gbp_m = report("GBPUSD", dual_sr(load("GBPUSD"), 0.00015))
    nas_m = report("NAS100", dual_sr(load("NAS100"), 2.0))
    print("\nKorelasi return BULANAN vs XAU zrev (rendah/negatif = diversifier bagus):")
    for tag, m in [("EURUSD", eur_m), ("GBPUSD", gbp_m), ("NAS100", nas_m)]:
        j = pd.concat([xau_m.rename("x"), m.rename("y")], axis=1).fillna(0)
        print(f"  {tag}: {j['x'].corr(j['y']):+.2f}")
    print("\nKandidat tambah ke portofolio = OOS PF>1.3 robust DAN korelasi rendah ke gold.")


if __name__ == "__main__":
    main()
