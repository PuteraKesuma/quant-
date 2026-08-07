"""Partner research #1c — WALK-FORWARD trend sleeve (honest, no hindsight asset-picking).
Each fold: expanding in-sample window -> per-asset pick best lookback AND rank by IS Sharpe ->
trade the top-K OUT-OF-SAMPLE (vol-targeted, equal-risk). Compares SMA-sign vs Donchian(S&R, the Z
engine). Reports per-fold OOS Sharpe + which assets got selected (does it persist as gold+equities?),
plus an 'all-assets' walk-forward baseline. Uses the cached daily universe (no re-fetch).

Run: python research/walkforward_trend.py
"""
import os
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
from tsmom_universe import UNIVERSE, CACHE

COST = {n: c for n, _, _, c in UNIVERSE}
CLS = {n: cl for n, _, cl, _ in UNIVERSE}
LKB = [50, 100, 200]; TARGET_VOL = 0.10; TOPK = 5
# expanding-IS folds: (is_end / oos_start, oos_end)
FOLDS = [("2017-01-01", "2019-01-01"), ("2019-01-01", "2021-01-01"),
         ("2021-01-01", "2023-01-01"), ("2023-01-01", "2025-01-01"),
         ("2025-01-01", "2026-07-01")]


def sma_ret(close, cost, L):
    ret = close.pct_change()
    pos = np.sign(close - close.rolling(L).mean()).shift(1)
    vol = ret.rolling(50).std().shift(1) * np.sqrt(252)
    scale = (TARGET_VOL / vol).clip(upper=3).fillna(0)
    return pos * scale * ret - pos.diff().abs().fillna(0) * scale * cost


def donchian_ret(close, cost, N):
    ret = close.pct_change()
    up = close.rolling(N).max().shift(1); dn = close.rolling(N).min().shift(1)
    pos = pd.Series(np.nan, index=close.index)
    pos[close >= up] = 1.0; pos[close <= dn] = -1.0
    pos = pos.ffill().shift(1)
    vol = ret.rolling(50).std().shift(1) * np.sqrt(252)
    scale = (TARGET_VOL / vol).clip(upper=3).fillna(0)
    return pos * scale * ret - pos.diff().abs().fillna(0) * scale * cost


def sharpe(r):
    r = r.dropna()
    return (r.mean() / r.std() * np.sqrt(252)) if (len(r) > 30 and r.std() > 0) else np.nan


def walk(px, engine, select=True):
    fold_sh = []; fold_sel = []; oos_all = []
    for oos_start, oos_end in FOLDS:
        t0 = pd.Timestamp(oos_start, tz="UTC"); t1 = pd.Timestamp(oos_end, tz="UTC")
        picks = []
        for name in px.columns:
            c = px[name].dropna()
            is_c = c[c.index < t0]
            if len(is_c) < 400:
                continue
            bestL, bestS = None, -9
            for L in LKB:
                s = sharpe(engine(is_c, COST[name], L))
                if np.isfinite(s) and s > bestS:
                    bestS, bestL = s, L
            picks.append((name, bestL, bestS))
        if not picks:
            continue
        picks.sort(key=lambda x: -x[2])
        chosen = picks[:TOPK] if select else picks
        streams = []
        for name, L, _ in chosen:
            c = px[name].dropna()
            r = engine(c, COST[name], L)
            streams.append(r[(r.index >= t0) & (r.index < t1)].rename(name))
        if not streams:
            continue
        P = pd.concat(streams, axis=1).mean(axis=1)
        oos_all.append(P); fold_sh.append(sharpe(P))
        fold_sel.append([f"{n}({L})" for n, L, _ in chosen])
    port = pd.concat(oos_all).sort_index() if oos_all else pd.Series(dtype=float)
    return sharpe(port), fold_sh, fold_sel, port


def main():
    px = pd.read_parquet(CACHE)
    print(f"WALK-FORWARD trend sleeve  folds={len(FOLDS)}  top-K={TOPK}  vol-target {TARGET_VOL:.0%}")
    for ename, eng in [("SMA-sign", sma_ret), ("Donchian-S&R", donchian_ret)]:
        print(f"\n===== ENGINE: {ename} =====")
        for label, sel in [("TOP-K selected in-sample", True), ("ALL assets (baseline)", False)]:
            osh, fsh, fsel, port = walk(px, eng, select=sel)
            eq = (1 + port.dropna()).cumprod(); dd = (eq / eq.cummax() - 1).min() if len(eq) else np.nan
            cagr = eq.iloc[-1] ** (252 / len(port.dropna())) - 1 if len(port.dropna()) > 50 else np.nan
            fp = sum(1 for s in fsh if np.isfinite(s) and s > 0)
            print(f"  [{label}] OOS Sharpe={osh:.2f}  CAGR={cagr:.1%}  maxDD={dd:.1%}  folds+={fp}/{len(fsh)}")
            print(f"     per-fold Sharpe: " + " ".join(f"{s:+.2f}" for s in fsh))
            if sel:
                for (os_, _), s, picks in zip(FOLDS, fsh, fsel):
                    print(f"       {os_[:7]} (Sh {s:+.2f}): {', '.join(picks)}")
    print("\nread: robust = OOS Sharpe positive AND most folds positive AND selection makes sense (gold+equity),")
    print("with the walk-forward picking assets IN-SAMPLE (no hindsight). Compare selected vs all-assets.")
    print("DONE")


if __name__ == "__main__":
    main()
