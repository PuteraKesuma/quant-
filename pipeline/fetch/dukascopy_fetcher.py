"""Dukascopy-based fetcher: pulls historical 1m OHLCV via dukascopy-python.

Reliable deep history (FX/metals back to ~2003-2005, indices ~2012+),
fetched entirely over HTTP — no terminal or GUI required.

Fetches month-by-month and upserts each chunk immediately, so a long
historical pull is resumable and safe against mid-run failures.
"""
from datetime import datetime
import pandas as pd
import dukascopy_python
from loguru import logger
from .base_fetcher import BaseFetcher

_INTERVAL = dukascopy_python.INTERVAL_MIN_1
_OFFER    = dukascopy_python.OFFER_SIDE_BID   # bid prices (standard for backtest)


def _next_month(d: datetime) -> datetime:
    return d.replace(year=d.year + 1, month=1, day=1) if d.month == 12 \
        else d.replace(month=d.month + 1, day=1)


class DukascopyFetcher(BaseFetcher):
    def __init__(self, symbol: str, duka_instrument: str):
        super().__init__(symbol)
        self.instrument = duka_instrument

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.rename(columns=str.lower)
        df.index.name = "ts"
        df = df.reset_index()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        if "volume" not in df.columns:
            df["volume"] = 0.0
        return df[["ts", "open", "high", "low", "close", "volume"]]

    def fetch(self, start: str, end: str) -> pd.DataFrame:
        """Single-shot fetch (kept for interface parity; run() chunks monthly)."""
        df = dukascopy_python.fetch(
            self.instrument, _INTERVAL, _OFFER,
            datetime.fromisoformat(start), datetime.fromisoformat(end),
        )
        return self._normalize(df)

    def run(self, start: str, end: str):
        """Override: fetch month-by-month, upserting each chunk incrementally."""
        self.init_db()
        req_start = datetime.fromisoformat(start)
        req_end   = datetime.fromisoformat(end)

        total = 0
        cur = req_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur <= req_end:
            nxt = _next_month(cur)
            chunk_start = max(cur, req_start)
            chunk_end   = min(nxt, req_end)
            try:
                df = dukascopy_python.fetch(
                    self.instrument, _INTERVAL, _OFFER, chunk_start, chunk_end,
                )
                ndf = self._normalize(df)
                if not ndf.empty:
                    self.upsert(ndf)
                    total += len(ndf)
                    logger.info(f"[{self.symbol}] {cur:%Y-%m}: +{len(ndf):,} bars "
                                f"(running total {total:,})")
                else:
                    logger.info(f"[{self.symbol}] {cur:%Y-%m}: no data")
            except Exception as e:
                logger.error(f"[{self.symbol}] {cur:%Y-%m}: fetch failed — {e}")
            cur = nxt

        logger.info(f"[{self.symbol}] Done. {total:,} bars fetched into {self.db_path.name}")
