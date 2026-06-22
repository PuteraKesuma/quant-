"""Monthly performance + market-regime recap for the LIVE strategies.

For each live slot, replays its exact rule over history and aggregates by month:
trades, win%, PF, R, and $ at 0.01 lot. Alongside, a market-regime read per month
(directional trend % and realised daily vol %) so you can SEE which regimes the
strategy likes — the basis for monitoring, not for auto-re-optimising.

This is the rolling out-of-sample monitor: re-run it any time; later it can ingest
the live trade log to compare expected vs actual.

    python -m pipeline.analysis.monthly_recap
"""
import numpy as np
import pandas as pd

from .strategy_lab import load_m1, simulate, resample, ROOT
from .orb_refine import orb_entries

# live slots: (symbol, range_filter?, rr, usd_per_point@0.01)
LIVE = {
    "orb30_nas (NAS100)": dict(sym="NAS100", rf=True,  rr=1.0, upp=0.10),
    "orb30_xau (XAU)":    dict(sym="XAUUSD", rf=False, rr=3.0, upp=1.0),
}


def regime(sym):
    """Monthly directional trend % and realised daily-vol % for the instrument."""
    d = resample(load_m1(sym), "1D")["close"]
    ret = d.pct_change()
    g = d.groupby([d.index.year, d.index.month])
    trend = g.apply(lambda x: 100 * (x.iloc[-1] / x.iloc[0] - 1))
    vol = ret.groupby([ret.index.year, ret.index.month]).std() * 100
    out = pd.DataFrame({"trend%": trend.round(1), "dvol%": vol.round(2)})
    out.index = [pd.Timestamp(year=y, month=m, day=1) for y, m in out.index]
    return out


def strat_monthly(cfg):
    m1 = load_m1(cfg["sym"])
    ent = orb_entries(m1, "orb", use_trend=False, use_range=cfg["rf"])
    slb = {p: sl for p, d, sl in ent}
    r = simulate(m1, ent, 1440, cfg["rr"])
    r["ts"] = m1.index[r["pos"].values]
    r["R"] = r["pos"].map(slb)
    r["usd"] = r["pnl_R"] * r["R"] * cfg["upp"]
    r["m"] = r["ts"].dt.to_period("M").dt.to_timestamp()
    g = r.groupby("m")
    rep = pd.DataFrame({
        "trades": g.size(),
        "win%": (g["pnl_R"].apply(lambda x: 100 * (x > 0).mean())).round(0),
        "R": g["pnl_R"].sum().round(1),
        "$@0.01": g["usd"].sum().round(1),
    })
    return rep


def main():
    for label, cfg in LIVE.items():
        rep = strat_monthly(cfg)
        reg = regime(cfg["sym"])
        tab = rep.join(reg, how="left")
        out = ROOT / "_DOC" / f"monthly_recap_{cfg['sym']}.csv"
        tab.to_csv(out)
        print(f"\n================ {label} — monthly recap ================")
        print("(trend% = arah bulan itu, dvol% = volatilitas harian; regime monitor)")
        print(tab.tail(15).to_string())
        pos = (rep["$@0.01"] > 0).mean() * 100
        print(f"  -> bulan profit: {pos:.0f}%  | total $@0.01: {rep['$@0.01'].sum():.0f}  | full CSV: {out.name}")
        # regime split: trending months vs choppy months
        j = rep.join(reg, how="left").dropna()
        strong = j[j["trend%"].abs() >= j["trend%"].abs().median()]
        weak = j[j["trend%"].abs() < j["trend%"].abs().median()]
        print(f"  regime: bulan TREN-kuat avg ${strong['$@0.01'].mean():.1f} | bulan CHOPPY avg ${weak['$@0.01'].mean():.1f}")


if __name__ == "__main__":
    main()
