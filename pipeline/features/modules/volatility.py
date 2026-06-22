"""Volatility features: ATR, rolling std, daily range."""
import pandas as pd
import numpy as np


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean().rename(f"atr_{period}")


def rolling_std(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df["close"].pct_change().rolling(period).std().rename(f"rstd_{period}")


def daily_range(df: pd.DataFrame) -> pd.Series:
    """High-Low range resampled to daily, then forward-filled to 1m."""
    daily = (df["high"] - df["low"]).resample("1D").sum()
    return daily.reindex(df.index, method="ffill").rename("daily_range")
