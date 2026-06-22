"""Live bar provider for signal evaluation.

Pulls recent M1 OHLCV straight from the MT5 terminal (already open to run the
EA), mirroring the normalisation in `pipeline/fetch/mt5_fetcher.py`: UTC index,
columns open/high/low/close/volume. The `orb` signal needs only today's bars up
to and including the opening range, so a few hundred bars is plenty.
"""
import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config


class DataProvider:
    """Returns recent M1 bars for a config symbol from the live MT5 terminal."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self._initialized = False

    def _ensure_mt5(self):
        import MetaTrader5 as mt5
        if not self._initialized:
            if not mt5.initialize():
                raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
            self._initialized = True
        return mt5

    def recent_bars(self, symbol: str, n: int) -> pd.DataFrame:
        """DataFrame indexed by UTC ts with open/high/low/close/volume (newest last)."""
        mt5 = self._ensure_mt5()
        mt5_symbol = self.cfg["symbols"][symbol]["mt5_symbol"]

        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            raise ValueError(f"Symbol '{mt5_symbol}' not found in MT5.")
        if not info.visible:
            mt5.symbol_select(mt5_symbol, True)

        rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M1, 0, n)
        if rates is None or len(rates) == 0:
            logger.warning(f"[{symbol}] No live bars from MT5: {mt5.last_error()}")
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rates)
        df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"})
        df = df.set_index("ts")[["open", "high", "low", "close", "volume"]].sort_index()
        return df
