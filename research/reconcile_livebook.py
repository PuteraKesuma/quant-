"""STEP 3 — reconcile the daily 3-sleeve book with the LIVE intraday book, so we don't 3x gold/NAS.
Reconstructs the intraday edges' daily PnL (gold Z via strategy_zrev; NAS ORB via the live-logic port)
and correlates them to the daily sleeves. JPY has NO live counterpart = fully additive. Output = a
clean integration rule.

Run: python research/reconcile_livebook.py
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from walkforward_trend import donchian_ret, COST
from audit_live_strategies import load_m1, zrev_audit, nas_orb

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
px = pd.read_parquet(CACHE)


def daily_pnl(items):
    """(ts,pnl) list -> daily summed PnL series (UTC dates)."""
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC"))
    return s.groupby(s.index.normalize()).sum()


print("STEP 3 — daily sleeve vs LIVE intraday book (correlation of daily PnL)\n")
# daily sleeves
gold_sleeve = donchian_ret(px["XAUUSD"].dropna(), COST["XAUUSD"], 100)
nas_sleeve  = donchian_ret(px["NAS100"].dropna(), COST["NAS100"], 100)

# intraday live edges (reconstructed from committed engines)
zg = daily_pnl(zrev_audit(load_m1("XAUUSD"))[0])            # gold Z (intraday Donchian S&R)
no = daily_pnl(nas_orb(load_m1("NAS100"))[0])              # NAS ORB (intraday breakout)

def corr(a, b, label):
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    c = j["a"].corr(j["b"]) if len(j) > 60 else float("nan")
    print(f"  {label:38} corr(daily PnL) = {c:+.2f}   (n={len(j)} shared days)")
    return c

print("=== overlap check (daily PnL correlation) ===")
cg = corr(gold_sleeve, zg, "daily GOLD-trend  vs  live gold Z")
cn = corr(nas_sleeve, no, "daily NAS-trend   vs  live NAS ORB")
print("  daily JPY-carry   vs  live book        = NONE (live book does not trade JPY) -> fully additive")

print("\n=== INTEGRATION RULE ===")
print(f"  - JPY carry: brand-new, uncorrelated (-0.36 to gold), NOT traded live -> ADD as a clean new sleeve.")
gl = "OVERLAPS" if (np.isfinite(cg) and cg > 0.2) else "low overlap"
nl = "OVERLAPS" if (np.isfinite(cn) and cn > 0.2) else "low overlap"
print(f"  - GOLD daily-trend {gl} live gold Z (corr {cg:+.2f}) -> do NOT stack a 2nd gold position;")
print(f"    use daily-trend as a SIZING/REGIME overlay on the existing Z (size up when daily agrees).")
print(f"  - NAS  daily-trend {nl} live NAS ORB (corr {cn:+.2f}); ORB is intraday/flat-overnight so")
print(f"    the persistent daily-trend NAS is {'mostly redundant' if (np.isfinite(cn) and cn>0.2) else 'largely additive'}.")
print("DONE")
