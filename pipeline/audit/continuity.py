"""Continuity checks: detect and classify time-series gaps (UTC).

Gap classes:
  OK                   1-minute step (not reported)
  EXPECTED_WEEKEND     gap that fully contains a Saturday
  EXPECTED_DAILY_BREAK recurring ~daily maintenance break (auto-detected)
  EXPECTED_HOLIDAY     long weekday gap (holiday)
  ANOMALY_INTRADAY     missing minutes inside an active session  <-- repair target
"""
import pandas as pd
from . import read_ohlcv


def _detect_daily_break(df: pd.DataFrame):
    """Find the recurring daily break as (utc_hour, duration_min), or None."""
    m = (df["dmin"] >= 50) & (df["dmin"] <= 80)
    if m.sum() == 0:
        return None
    hmode = df.loc[m, "prev"].dt.hour.mode()
    dmode = df.loc[m, "dmin"].round().mode()
    if len(hmode) == 0 or len(dmode) == 0:
        return None
    return (int(hmode.iloc[0]), float(dmode.iloc[0]))


def _spans_weekend(start, end) -> bool:
    cur = start.normalize() + pd.Timedelta(days=1)
    while cur < end:
        if cur.weekday() == 5:  # a full Saturday sits inside the gap
            return True
        cur += pd.Timedelta(days=1)
    return False


def _classify(start, end, dmin, brk) -> str:
    if _spans_weekend(start, end):
        return "EXPECTED_WEEKEND"
    if dmin > 180:
        return "EXPECTED_HOLIDAY"
    # daily maintenance break — allow +/-1h for DST drift and a wide duration band
    if brk is not None and abs(int(start.hour) - brk[0]) <= 1 and dmin <= brk[1] + 30:
        return "EXPECTED_DAILY_BREAK"
    # medium session break / partial-day holiday — expected, not a repair target
    if dmin >= 30:
        return "EXPECTED_BREAK"
    # small gap inside an active session = genuine missing minutes
    return "ANOMALY_INTRADAY"


def classify_gaps(symbol: str, cfg: dict) -> dict:
    df = read_ohlcv(symbol, cfg, columns="ts").sort_values("ts").reset_index(drop=True)
    df["prev"] = df["ts"].shift(1)
    df["dmin"] = (df["ts"] - df["prev"]).dt.total_seconds() / 60.0
    brk = _detect_daily_break(df)

    gaps = []
    for prev, ts, dmin in zip(*[df.loc[df["dmin"] > 1.0, c] for c in ("prev", "ts", "dmin")]):
        gaps.append({
            "start": prev, "end": ts,
            "minutes": round(float(dmin), 1),
            "missing": int(round(dmin)) - 1,
            "class": _classify(prev, ts, dmin, brk),
        })

    anomalies = [g for g in gaps if g["class"] == "ANOMALY_INTRADAY"]
    by_class = {}
    for g in gaps:
        by_class[g["class"]] = by_class.get(g["class"], 0) + 1

    # within-session completeness per year
    df["year"] = df["ts"].dt.year
    bars_per_year = df.groupby("year").size().to_dict()
    miss_per_year = {}
    for g in anomalies:
        y = pd.Timestamp(g["start"]).year
        miss_per_year[y] = miss_per_year.get(y, 0) + g["missing"]
    by_year = {}
    for y, bars in bars_per_year.items():
        miss = miss_per_year.get(y, 0)
        by_year[int(y)] = {
            "bars": int(bars), "missing": int(miss),
            "completeness": round(bars / (bars + miss) * 100, 4) if bars + miss else 100.0,
        }

    return {
        "daily_break": brk,
        "n_gaps": len(gaps),
        "by_class": by_class,
        "anomaly_count": len(anomalies),
        "missing_bars_anomaly": int(sum(g["missing"] for g in anomalies)),
        "by_year": by_year,
        "gaps": gaps,
    }
