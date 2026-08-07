"""Partner research #3 — correlation to XAUUSD, to build a LOW-DD book around the proven gold edge.
Uses the cached daily universe. Reports: (1) each asset's daily-return corr to gold (full + recent),
(2) the corr MATRIX among the trend-edge assets (is the 'trend sleeve' actually diversified or just
equity beta?), (3) the low-corr candidates = best diversifiers to a gold-trend book.

Run: python research/corr_to_gold.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
px = pd.read_parquet(CACHE)
ret = px.pct_change()
recent = ret[ret.index >= pd.Timestamp("2023-01-01", tz="UTC")]

print("=== daily-return correlation to XAUUSD (asset level) ===")
print(f"{'asset':9} {'corr(full)':>10} {'corr(23-26)':>12}")
cf = ret.corrwith(ret["XAUUSD"]); cr = recent.corrwith(recent["XAUUSD"])
for a in cf.reindex(cf.abs().sort_values().index).index:
    if a == "XAUUSD": continue
    print(f"{a:9} {cf[a]:>10.2f} {cr[a]:>12.2f}")

print("\n=== LOWEST |corr| to gold (best diversifier candidates, full period) ===")
low = cf.drop("XAUUSD").abs().sort_values().head(8)
for a in low.index:
    print(f"  {a:9} corr {cf[a]:+.2f}")

print("\n=== corr matrix among trend-edge assets (do they diversify each other?) ===")
edge = [a for a in ["XAUUSD","NAS100","SP500","NIKKEI","DOW","USDJPY","WTI"] if a in ret.columns]
cm = ret[edge].corr()
print(cm.round(2).to_string())

print("\nread: an asset with LOW/NEGATIVE corr to gold + its own validated edge = a real DD-cutting")
print("diversifier (like Z<->NAS -0.04). High mutual corr among equities = they DON'T diversify each other.")
print("DONE")
