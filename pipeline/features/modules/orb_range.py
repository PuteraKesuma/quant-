"""Compute Opening Range High/Low for each session."""
import pandas as pd


def compute_orb(df: pd.DataFrame, session_open: str, range_minutes: int = 30) -> pd.DataFrame:
    """
    Args:
        df: 1m OHLCV with UTC DatetimeIndex
        session_open: "HH:MM" UTC string for session open
        range_minutes: how many minutes define the opening range
    Returns:
        Daily DataFrame with: date, orb_high, orb_low, orb_size
    """
    h, m = map(int, session_open.split(":"))
    records = []

    for date, day_df in df.groupby(df.index.date):
        start = pd.Timestamp(date).replace(hour=h, minute=m, tzinfo=pd.Timestamp("now", tz="UTC").tzinfo)
        end   = start + pd.Timedelta(minutes=range_minutes)
        window = day_df[(day_df.index >= start) & (day_df.index < end)]
        if len(window) < range_minutes // 2:
            continue
        records.append({
            "date":     date,
            "orb_high": window["high"].max(),
            "orb_low":  window["low"].min(),
            "orb_size": window["high"].max() - window["low"].min(),
        })

    return pd.DataFrame(records).set_index("date")
