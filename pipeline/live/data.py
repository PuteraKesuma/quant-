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
        self._offset_hours: int | None = None   # cached broker server->UTC offset

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

        # MT5 bar times are in BROKER SERVER time, not UTC. Shift to true UTC so the
        # UTC session windows (e.g. 13:30 NY) line up with the research. Without this
        # the live ORB evaluates a window offset by the broker's UTC offset.
        offset = self._server_offset_hours(mt5, mt5_symbol)
        if offset:
            df.index = df.index - pd.Timedelta(hours=offset)
        return df

    def _server_offset_hours(self, mt5, mt5_symbol) -> int:
        """Broker server time minus UTC, in whole hours (e.g. FBS = +3 summer).

        Uses the configured value if set; otherwise auto-detects from a fresh tick
        (server tick time vs real UTC, rounded to the nearest hour). Auto-detection
        is only trusted when the tick is fresh (a whole-hour offset within a sane
        range), so a stale weekend tick can't poison it. Warns if a configured
        offset disagrees with a freshly detected one (likely a DST change)."""
        configured = self.cfg.get("live", {}).get("mt5_server_utc_offset_hours")

        detected = None
        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick and tick.time:
            server_now = pd.Timestamp(tick.time, unit="s", tz="UTC")
            diff = (server_now - pd.Timestamp.utcnow()).total_seconds() / 3600.0
            nearest = round(diff)
            if abs(diff - nearest) <= 0.5 and -12 <= nearest <= 14:   # fresh, sane
                detected = nearest

        if configured is not None:
            if detected is not None and detected != configured:
                logger.warning(
                    f"Configured mt5_server_utc_offset_hours={configured} but live "
                    f"offset looks like {detected} (DST change?). Update config.yaml."
                )
            offset = int(configured)
        elif detected is not None:
            offset = detected
        else:
            offset = self._offset_hours or 0   # keep last good; 0 until first detect

        if offset != self._offset_hours:
            logger.info(f"MT5 server->UTC offset = {offset:+d}h")
            self._offset_hours = offset
        return offset
