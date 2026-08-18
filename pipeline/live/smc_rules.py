"""Rule-based SMC candidate gate (no LLM, fully backtestable).

Implements the 3-part A+ pattern already specified in
`pipeline/vision/prompt_smc_mtf_v2.md` mechanically, using the `smartmoneyconcepts`
package (BOS/CHoCH, order blocks, liquidity, FVG):

    1. Liquidity sweep  -- a swing high/low pool gets swept (wicked through and closed back).
    2. Displacement/CHoCH -- shortly after, a change of character in the OPPOSITE
       direction of the sweep (sweeping highs -> bearish CHOCH, and vice versa).
    3. Entry origin -- an unmitigated Order Block or FVG left behind by that
       displacement, in the CHOCH's direction, for price to retrace into.

All three must line up within the lookback windows or there is no candidate. This
mirrors the prompt's own "no sweep, no trade; no displacement, no trade" rule, so
the LLM is only ever asked to confirm a setup a human SMC trader would already call
A-grade -- not to find one from scratch on a timer.

`smartmoneyconcepts` prints a banner (with an emoji) at import time that crashes
under Windows' default cp1252 console encoding -- force UTF-8 stdio before the
import so this module is safe to import from the live brain process.
"""
import os
import sys

if sys.platform == "win32" and sys.stdout is not None:
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pandas as pd
from smartmoneyconcepts import smc


def htf_bias(df_htf: pd.DataFrame, swing_length: int = 10) -> str:
    """Directional bias from the last two CONFIRMED swing highs and lows on a
    higher timeframe: 'up' (higher-high AND higher-low), 'down' (lower-high AND
    lower-low), else 'range'. Mechanical version of prompt_smc_mtf_v2.md's own
    macro-bias guard ("clear UPTREND -> BUY only" etc.), used as the "market
    confidence" cross-check when the entry-trigger timeframe is fast (M1/M5):
    a fast CHoCH is only actionable if the slower structure agrees."""
    n = len(df_htf)
    if n < swing_length * 2 + 10:
        return "range"
    sh = smc.swing_highs_lows(df_htf, swing_length=swing_length)
    highs = sh[sh["HighLow"] == 1]
    lows = sh[sh["HighLow"] == -1]
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    h1, h0 = float(highs["Level"].iloc[-1]), float(highs["Level"].iloc[-2])
    l1, l0 = float(lows["Level"].iloc[-1]), float(lows["Level"].iloc[-2])
    if h1 > h0 and l1 > l0:
        return "up"
    if h1 < h0 and l1 < l0:
        return "down"
    return "range"


def smc_candidate(
    df: pd.DataFrame,
    swing_length: int = 10,
    choch_lookback: int | None = None,
    sweep_lookback: int = 60,
    ob_fvg_lookback: int = 30,
    bias: str | None = None,
) -> dict | None:
    """Return a candidate dict if the full sweep->CHoCH->OB/FVG pattern just
    completed within the recent window of `df`, else None. Pure function of the
    OHLC history given -- no I/O, no state -- so it is trivially backtestable by
    replaying it bar-by-bar over historical data.

    `df` must have columns open/high/low/close (volume optional), sorted
    ascending, one row per bar, index = bar timestamp.

    `choch_lookback`: swing_highs_lows() confirms a swing point only once
    `swing_length` bars AFTER it (it needs that many future candles to know the
    point was never exceeded) -- so a CHoCH broken at bar B is not visible in the
    data AT ALL until bar B+swing_length. Defaults to `swing_length + 5` so a
    freshly-confirmed CHoCH is still caught with a few bars of margin, not missed
    entirely by a window shorter than the library's own confirmation lag.

    `sweep_lookback`/`ob_fvg_lookback` (2026-08-18, corrected): checked empirically
    against 3 weeks of XAUUSD M5 -- the gap between a CHoCH's BrokenIndex and its
    causally-related liquidity sweep is very often 20-90 bars (confirmation lag
    stacks with however long the real displacement took to build), NOT the ~10
    originally guessed. That guess silently threw out most genuine setups (found
    only 3 candidates where a proper scan found 10+, same win rate). Swept 10/20/
    30/50/80/100/150/200-bar windows: win rate and avgR hold up (66-80%, R+1.0..+1.5)
    through ~100 bars, then visibly decay (150 -> 56% WR, 200 -> 45% WR) as the
    matched "sweep" stops being causally related to the CHoCH. 60 sits inside the
    good range without hugging the single best-observed point on one sample.
    """
    if choch_lookback is None:
        choch_lookback = swing_length + 5
    n = len(df)
    min_bars = swing_length * 2 + max(choch_lookback, sweep_lookback, ob_fvg_lookback) + 5
    if n < min_bars:
        return None

    sh = smc.swing_highs_lows(df, swing_length=swing_length)
    bc = smc.bos_choch(df, sh, close_break=True)
    obr = smc.ob(df, sh, close_mitigation=False)
    liq = smc.liquidity(df, sh, range_percent=0.01)
    fv = smc.fvg(df, join_consecutive=False)

    last_pos = n - 1

    # ---- 2. displacement: a CHOCH CONFIRMED (BrokenIndex, not the row's own
    #    position -- the row index is the swing LEVEL being broken, which can be
    #    far in the past) within the last `choch_lookback` closed bars. Causality:
    #    only consider a CHOCH once its BrokenIndex has actually happened.
    choch_rows = bc[bc["CHOCH"].notna()].copy()
    choch_rows = choch_rows[choch_rows["BrokenIndex"].notna()]
    choch_rows["BrokenIndex"] = choch_rows["BrokenIndex"].astype(int)
    choch_rows = choch_rows[(choch_rows["BrokenIndex"] <= last_pos) &
                             (choch_rows["BrokenIndex"] >= last_pos - choch_lookback)]
    if choch_rows.empty:
        return None
    choch_rows = choch_rows.sort_values("BrokenIndex")
    choch = choch_rows.iloc[-1]
    broken_at = int(choch["BrokenIndex"])                   # bar the CHoCH was confirmed on
    direction = int(choch["CHOCH"])                          # 1 bullish, -1 bearish
    choch_level = float(choch["Level"])

    # ---- 0. higher-timeframe bias agreement ("market confidence") -- a fast LTF
    #    CHoCH is only actionable if the slower structure isn't fighting it ----
    if bias and bias != "range":
        want_bias = "up" if direction == 1 else "down"
        if bias != want_bias:
            return None

    # ---- 1. liquidity sweep shortly before the CHOCH confirmation, OPPOSITE side ----
    # bullish CHOCH (displacement UP) must be preceded by a SELL-side sweep (lows swept, Liquidity==-1)
    # bearish CHOCH (displacement DOWN) must be preceded by a BUY-side sweep (highs swept, Liquidity==1)
    want_liq_dir = -1 if direction == 1 else 1
    liq_rows = liq[liq["Liquidity"] == want_liq_dir].copy()
    liq_rows = liq_rows[liq_rows["Swept"].fillna(0) > 0]     # 0 = not (yet) swept
    liq_rows["Swept"] = liq_rows["Swept"].astype(int)
    swept = liq_rows[(liq_rows["Swept"] <= broken_at) &
                      (liq_rows["Swept"] >= broken_at - sweep_lookback)]
    if swept.empty:
        return None
    sweep_level = float(swept.sort_values("Swept").iloc[-1]["Level"])

    # ---- 3. an unmitigated OB or FVG, in the CHOCH direction, formed around the
    #    displacement (sweep -> CHoCH confirmation window) for price to retrace into ----
    win_start = max(0, broken_at - ob_fvg_lookback)
    win_end = min(last_pos, broken_at + ob_fvg_lookback)
    ob_hits = obr.iloc[win_start:win_end + 1]
    ob_hits = ob_hits[(ob_hits["OB"] == direction) & (ob_hits["MitigatedIndex"].fillna(0) == 0)]
    fvg_hits = fv.iloc[win_start:win_end + 1]
    fvg_hits = fvg_hits[(fvg_hits["FVG"] == direction) & (fvg_hits["MitigatedIndex"].fillna(0) == 0)]
    if ob_hits.empty and fvg_hits.empty:
        return None

    origin_kind, origin_top, origin_bottom = None, None, None
    if not ob_hits.empty:
        o = ob_hits.iloc[-1]
        origin_kind, origin_top, origin_bottom = "OB", float(o["Top"]), float(o["Bottom"])
    elif not fvg_hits.empty:
        f = fvg_hits.iloc[-1]
        origin_kind, origin_top, origin_bottom = "FVG", float(f["Top"]), float(f["Bottom"])

    side = "BUY" if direction == 1 else "SELL"
    return {
        "side": side,
        "choch_ts": df.index[broken_at],
        "choch_level": choch_level,
        "sweep_level": sweep_level,
        "origin_kind": origin_kind,
        "origin_top": origin_top,
        "origin_bottom": origin_bottom,
        "reason": (f"{side}: liquidity swept @ {sweep_level:.2f}, CHoCH confirmed @ {choch_level:.2f} "
                   f"({df.index[broken_at]}), unmitigated {origin_kind} "
                   f"[{origin_bottom:.2f}-{origin_top:.2f}] to retrace into"),
    }
