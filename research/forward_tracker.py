"""Forward-test tracker: is the LIVE demo actually tracking the backtest expectation?

On first run it stamps a start marker (server-time + balance) into _MONITOR/forward_test.json.
Every run after that it pulls the closed demo trades since the start (our 3 magics), builds the
realized equity, and compares it to the Monte-Carlo forecast cone (from the same 0.01-lot book
used in ridge_forecast) anchored at the start balance over a 13-week (~3 month) horizon.

Purpose: the ONE validation we lack is FORWARD/live-execution proof. This turns 'test more'
into a number you can read weekly: on-track (inside the cone), lagging (below the 5th pct), or
lucky (above the 95th). Go-live with real money only after ~2-3 months of green criteria.
Run weekly:  python research/forward_tracker.py
"""
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from zrev_dual_trend import sim_dual, daily_map
from portfolio_audit import nas_dollars

MARKER = Path(r"C:\Quant\_MONITOR\forward_test.json")
MAGICS = {920617, 920622, 920624}          # orb30_nas, zrev_xau, zrev_xau_4h
HORIZON_W = 13                              # ~3 months
DD_LIMIT = 0.30                            # hard-stop drawdown criterion
OUT = r"C:\Users\ADMINI~1\AppData\Local\Temp\1\claude\C--Users-Administrator\91e0ccf1-c993-48f2-8268-f1678ad108cb\scratchpad\forward_tracker.png"


def get_marker(m):
    if MARKER.exists():
        return json.loads(MARKER.read_text(encoding="utf-8"))
    tick = m.symbol_info_tick("XAUUSD")
    bal = float(m.account_info().balance)
    mk = {"start_server_epoch": int(tick.time),
          "start_iso_utc": datetime.now(timezone.utc).isoformat(),
          "start_balance": bal, "horizon_weeks": HORIZON_W}
    MARKER.write_text(json.dumps(mk, indent=2), encoding="utf-8")
    print(f"[forward-test] *** STARTED *** balance=${bal:.2f}  ({mk['start_iso_utc']})")
    return mk


def forward_trades(m, start_epoch):
    frm = datetime.fromtimestamp(start_epoch, timezone.utc) - timedelta(days=1)
    deals = m.history_deals_get(frm, datetime.now(timezone.utc) + timedelta(days=2)) or []
    rows = [(d.time, d.profit + d.swap + d.commission, d.symbol)
            for d in deals if d.magic in MAGICS and d.entry == 1 and d.time > start_epoch]
    return sorted(rows)


def cone(start_balance):
    dmap = daily_map(50)
    z = sim_dual(dmap=dmap, use_daily=True)
    zser = pd.Series([t[3] for t in z], index=pd.DatetimeIndex([t[1] for t in z]))
    book = pd.concat([zser, nas_dollars()]).sort_index()
    wk = book.resample("W").sum()
    rng = np.random.default_rng(7)
    sims = np.array([start_balance + np.cumsum(rng.choice(wk.values, HORIZON_W, replace=True))
                     for _ in range(4000)])
    p5, p50, p95 = np.percentile(sims, [5, 50, 95], axis=0)
    return (np.concatenate([[start_balance], p5]),
            np.concatenate([[start_balance], p50]),
            np.concatenate([[start_balance], p95]))


def main():
    import MetaTrader5 as m
    if not m.initialize():
        print("MT5 init failed:", m.last_error()); return
    mk = get_marker(m)
    start_epoch = mk["start_server_epoch"]; start_bal = mk["start_balance"]
    trades = forward_trades(m, start_epoch)
    cur_bal = float(m.account_info().balance)
    open_n = len(m.positions_get() or [])
    m.shutdown()

    start_dt = datetime.fromisoformat(mk["start_iso_utc"])
    elapsed_d = (datetime.now(timezone.utc) - start_dt).total_seconds() / 86400
    elapsed_w = elapsed_d / 7
    pnl = np.array([p for _, p, _ in trades], dtype=float)
    realized = float(pnl.sum())
    eq_now = start_bal + realized
    if len(pnl):
        eqc = start_bal + np.cumsum(pnl)
        dd = float((eqc - np.maximum.accumulate(eqc)).min())
        dd_pct = dd / start_bal
    else:
        dd = dd_pct = 0.0
    p5, p50, p95 = cone(start_bal)
    wi = min(max(int(round(elapsed_w)), 0), HORIZON_W)

    print(f"\n=== FORWARD TEST (demo, {HORIZON_W}w target) ===")
    print(f"start {start_dt.date()}  bal ${start_bal:.2f}  |  elapsed {elapsed_d:.1f}d ({elapsed_w:.1f}w)")
    print(f"trades so far: {len(trades)}  ({open_n} open)  realized ${realized:+.2f}")
    print(f"equity now: ${eq_now:.2f}  ({100*(eq_now/start_bal-1):+.1f}%)  maxDD ${dd:+.2f} ({100*dd_pct:+.1f}%)")
    print(f"\nexpected by wk {wi}: median ${p50[wi]:.0f}  floor(5%) ${p5[wi]:.0f}  ceil(95%) ${p95[wi]:.0f}")
    if eq_now < p5[wi]:
        status = "LAGGING (di bawah cone 5% -- selidiki eksekusi/regime)"
    elif eq_now > p95[wi]:
        status = "di ATAS cone 95% (hoki -- jangan overconfident)"
    else:
        status = "ON-TRACK (dalam cone)"
    print(f"status: {status}")

    print("\n--- kriteria go/no-go ---")
    print(f"  [{'OK' if eq_now >= p5[wi] else '!!'}] P&L dalam cone MC")
    print(f"  [{'OK' if dd_pct > -DD_LIMIT else '!!'}] drawdown <= {int(DD_LIMIT*100)}% (now {100*dd_pct:+.0f}%)")
    print(f"  [{'OK' if (elapsed_w < 2 or len(trades) >= 2*elapsed_w) else '??'}] sistem aktif nge-trade")
    print("  [manual] eksekusi bersih (cek jurnal: ga ada sinyal kelewat / FLAT-close paksa)")
    print("  [manual] KAMU tahan lewat bulan merah tanpa panik")

    # plot
    wks = np.arange(HORIZON_W + 1)
    plt.figure(figsize=(11, 5.4))
    plt.fill_between(wks, p5, p95, color="#1f77b4", alpha=0.15, label="cone MC 5-95%")
    plt.plot(wks, p50, color="#1f77b4", lw=1.3, label=f"median (target ${p50[-1]:.0f})")
    plt.plot(wks, p5, color="#c0392b", lw=1.0, ls=":", label=f"floor 5% (${p5[-1]:.0f})")
    if len(trades):
        tw = [(t - start_epoch) / (7 * 86400) for t, _, _ in trades]
        te = start_bal + np.cumsum(pnl)
        plt.step([0] + tw, [start_bal] + list(te), where="post", color="#111", lw=1.6,
                 label=f"DEMO nyata (${eq_now:.0f})")
    else:
        plt.scatter([0], [start_bal], color="#111", zorder=5, label=f"DEMO nyata (${eq_now:.0f})")
    plt.axhline(start_bal, color="gray", ls=":", lw=0.7)
    plt.axhline(start_bal * (1 - DD_LIMIT), color="#c0392b", ls="--", lw=0.8,
                label=f"stop -{int(DD_LIMIT*100)}% (${start_bal*(1-DD_LIMIT):.0f})")
    plt.title(f"Forward test demo vs backtest cone  |  hari ke-{elapsed_d:.0f}, {len(trades)} trade  |  {status}")
    plt.xlabel("minggu sejak mulai"); plt.ylabel("Equity ($)")
    plt.legend(loc="upper left", fontsize=8); plt.grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(OUT, dpi=110)
    print("\nsaved:", OUT)


if __name__ == "__main__":
    main()
