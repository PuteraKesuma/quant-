"""ORB strategy logic: generates trade signals from 1m OHLCV + ORB levels."""
from dataclasses import dataclass, field
from typing import Literal
import pandas as pd


@dataclass
class ORBParams:
    range_minutes: int   = 30
    tp_multiplier: float = 1.0
    sl_multiplier: float = 1.0
    entry_buffer:  float = 0.0
    max_trades_per_session: int = 1


@dataclass
class Trade:
    symbol:     str
    session:    str
    date:       pd.Timestamp
    direction:  Literal["long", "short"]
    entry_ts:   pd.Timestamp
    entry_price: float
    tp_price:   float
    sl_price:   float
    exit_ts:    pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl_r:      float | None = None  # P&L in R-multiples


def generate_signals(
    df: pd.DataFrame,
    symbol: str,
    session_name: str,
    session_open: str,
    params: ORBParams,
) -> list[Trade]:
    """
    df: 1m OHLCV with UTC DatetimeIndex for a single symbol
    Returns list of Trade objects (not yet filled — exits computed in engine)
    """
    h, m = map(int, session_open.split(":"))
    trades: list[Trade] = []

    for date, day_df in df.groupby(df.index.date):
        start = pd.Timestamp(str(date), tz="UTC").replace(hour=h, minute=m)
        range_end = start + pd.Timedelta(minutes=params.range_minutes)

        window = day_df[(day_df.index >= start) & (day_df.index < range_end)]
        if len(window) < params.range_minutes // 2:
            continue

        orb_high = window["high"].max()
        orb_low  = window["low"].min()
        orb_size = orb_high - orb_low
        if orb_size <= 0:
            continue

        post_range = day_df[day_df.index >= range_end]
        trades_today = 0

        for ts, bar in post_range.iterrows():
            if trades_today >= params.max_trades_per_session:
                break

            if bar["high"] > orb_high + params.entry_buffer:
                entry = orb_high + params.entry_buffer
                trades.append(Trade(
                    symbol=symbol, session=session_name,
                    date=pd.Timestamp(str(date), tz="UTC"),
                    direction="long",
                    entry_ts=ts, entry_price=entry,
                    tp_price=entry + orb_size * params.tp_multiplier,
                    sl_price=entry - orb_size * params.sl_multiplier,
                ))
                trades_today += 1

            elif bar["low"] < orb_low - params.entry_buffer:
                entry = orb_low - params.entry_buffer
                trades.append(Trade(
                    symbol=symbol, session=session_name,
                    date=pd.Timestamp(str(date), tz="UTC"),
                    direction="short",
                    entry_ts=ts, entry_price=entry,
                    tp_price=entry - orb_size * params.tp_multiplier,
                    sl_price=entry + orb_size * params.sl_multiplier,
                ))
                trades_today += 1

    return trades
