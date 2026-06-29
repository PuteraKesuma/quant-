"""Portfolio audit of the live book: zrev (EMA100 always-in, XAU) + orb30_nas (US100).

Combines the two DEPLOYED configs in $ terms at 0.01 lot, to show that diversifying
two uncorrelated edges raises return AND cuts drawdown (the only free lunch in
trading). Also sizes the capital needed for a sane real-money drawdown.

Units: XAU 0.01 lot = $1.00/point; NAS (US100) 0.01 lot = $0.10/point.
Run: python research/portfolio_audit.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from audit_live_strategies import load_m1, to_d1, _nas_open_min
from pipeline.backtest.strategy_zrev import simulate, ZRevParams, trades_to_df, resample_1h


def maxdd(s: pd.Series) -> float:
    eq = s.sort_index().cumsum()
    return float((eq - eq.cummax()).min())


def zrev_dollars():
    h1 = resample_1h(load_m1("XAUUSD").assign(volume=0))
    t = trades_to_df(simulate(h1, ZRevParams(20, 0, 0.30, trend_filter=True, trend_ema=100)))
    return pd.Series(t["pnl_points"].values, index=pd.to_datetime(t["entry_ts"]))  # $1/point


def nas_dollars():
    nas = load_m1("NAS100")
    H, L, C = nas["high"].values, nas["low"].values, nas["close"].values
    mod = nas.index.hour.values * 60 + nas.index.minute.values
    dord = nas.index.normalize().asi8
    uniq, starts = np.unique(dord, return_index=True); starts = list(starts) + [len(nas)]
    d1 = to_d1(nas); dc = d1["close"]; pc = dc.shift(1); sma = dc.rolling(50).mean().shift(1)
    tmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    rows = []
    for di in range(len(uniq)):
        a, b = starts[di], starts[di + 1]; day = nas.index[a].date()
        om = _nas_open_min(day); md = mod[a:b]; idx = np.arange(a, b)
        rm = (md >= om) & (md < om + 30)
        if rm.sum() < 15:
            continue
        ri = idx[rm]; oh = H[ri].max(); ol = L[ri].min(); size = oh - ol
        if size <= 0:
            continue
        pidx = idx[md >= om + 30]; ei = d = ent = None
        for i in pidx:
            if H[i] > oh: ei, d, ent = i, 1, oh; break
            if L[i] < ol: ei, d, ent = i, -1, ol; break
        if ei is None:
            continue
        td = tmap.get(day, 0)
        if td == 0 or (td > 0) != (d == 1):      # trend50 filter
            continue
        cr = 2.0 / size; armed = False; pnl = None
        for j in range(ei, b):
            if mod[j] >= 20 * 60: pnl = d * (C[j] - ent) / size - cr; break
            if d == 1:
                if not armed and (H[j] - ent) >= 0.5 * size: armed = True
                if armed and L[j] <= ent: pnl = -cr; break
                if L[j] <= ent - size: pnl = -1 - cr; break
                if H[j] >= ent + size: pnl = 1 - cr; break
            else:
                if not armed and (ent - L[j]) >= 0.5 * size: armed = True
                if armed and H[j] >= ent: pnl = -cr; break
                if H[j] >= ent + size: pnl = -1 - cr; break
                if L[j] <= ent - size: pnl = 1 - cr; break
        if pnl is None:
            pnl = d * (C[b - 1] - ent) / size - cr
        rows.append((nas.index[ei], pnl * size * 0.10))     # R -> $: R * size * $0.10/pt
    return pd.Series([p for _, p in rows], index=pd.DatetimeIndex([t for t, _ in rows]))


def main():
    z, n = zrev_dollars(), nas_dollars()
    both = pd.concat([z, n]).sort_index()
    print(f"zrev EMA100 : n={len(z):4d} net=${z.sum():+.0f} maxDD=${maxdd(z):.0f}")
    print(f"orb30_nas   : n={len(n):4d} net=${n.sum():+.0f} maxDD=${maxdd(n):.0f}")
    print(f"PORTFOLIO   : n={len(both):4d} net=${both.sum():+.0f} maxDD=${maxdd(both):.0f}")
    print(f"  sum of individual DDs ${maxdd(z)+maxdd(n):.0f} vs combined ${maxdd(both):.0f} (diversification)")
    zm = z.resample("MS").sum(); nm = n.resample("MS").sum()
    j = pd.concat([zm.rename("z"), nm.rename("n")], axis=1).fillna(0)
    print(f"monthly return correlation zrev vs nas: {j['z'].corr(j['n']):+.2f}")
    dd = abs(maxdd(both))
    print(f"\nat $367: portfolio maxDD ${-dd:.0f} = {dd/367*100:.0f}% of balance")
    print(f"for DD < 25% of balance, capital ~ ${dd/0.25:.0f}")


if __name__ == "__main__":
    main()
