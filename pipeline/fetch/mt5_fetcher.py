"""MT5-based fetcher: uses copy_rates_from_pos (copy_rates_range broken on FBS)."""
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5
from loguru import logger
from .base_fetcher import BaseFetcher

_MAX_BARS = 99_999  # MT5 safe limit per request


class MT5Fetcher(BaseFetcher):
    """
    Base fetcher for any MT5 symbol.
    Uses copy_rates_from_pos (more reliable across brokers than copy_rates_range).
    MT5 terminal must be open and logged in before running.
    """
    def __init__(self, symbol: str, mt5_symbol: str):
        super().__init__(symbol)
        self.mt5_symbol = mt5_symbol

    def fetch(self, start: str, end: str) -> pd.DataFrame:
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

        try:
            info = mt5.symbol_info(self.mt5_symbol)
            if info is None:
                raise ValueError(f"Symbol '{self.mt5_symbol}' not found in MT5.")
            if not info.visible:
                mt5.symbol_select(self.mt5_symbol, True)

            logger.info(f"[{self.symbol}] Pulling all available 1m bars from MT5...")
            rates = mt5.copy_rates_from_pos(self.mt5_symbol, mt5.TIMEFRAME_M1, 0, _MAX_BARS)

            if rates is None or len(rates) == 0:
                logger.error(f"[{self.symbol}] No data from MT5: {mt5.last_error()}")
                return pd.DataFrame()

            df = pd.DataFrame(rates)
            df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df = df.rename(columns={"tick_volume": "volume"})[
                ["ts", "open", "high", "low", "close", "volume"]
            ]

            # Filter to requested date range
            req_start = pd.Timestamp(start, tz="UTC")
            req_end   = pd.Timestamp(end,   tz="UTC")
            df = df[(df["ts"] >= req_start) & (df["ts"] <= req_end)]

            if df.empty:
                logger.warning(
                    f"[{self.symbol}] No bars in requested range {start} to {end}. "
                    f"MT5 has data from {pd.Timestamp(rates[0]['time'], unit='s', tz='UTC').date()} "
                    f"to {pd.Timestamp(rates[-1]['time'], unit='s', tz='UTC').date()}."
                )
                return pd.DataFrame()

            logger.info(
                f"[{self.symbol}] Fetched {len(df):,} bars "
                f"({df['ts'].min().date()} to {df['ts'].max().date()})"
            )
            return df

        finally:
            mt5.shutdown()
