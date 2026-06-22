from abc import ABC, abstractmethod
from pathlib import Path
import duckdb
import pandas as pd
import yaml
from loguru import logger


def load_config() -> dict:
    root = Path(__file__).parent.parent.parent
    with open(root / "config.yaml") as f:
        return yaml.safe_load(f)


class BaseFetcher(ABC):
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.cfg = load_config()
        raw_dir = Path(self.cfg["data"]["raw_dir"])
        self.db_path = Path(__file__).parent.parent.parent / raw_dir / f"{symbol}_1m.duckdb"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init_db(self):
        con = duckdb.connect(str(self.db_path))
        con.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                ts        TIMESTAMPTZ PRIMARY KEY,
                open      DOUBLE,
                high      DOUBLE,
                low       DOUBLE,
                close     DOUBLE,
                volume    DOUBLE
            )
        """)
        con.close()
        logger.info(f"[{self.symbol}] DB initialized at {self.db_path}")

    def upsert(self, df: pd.DataFrame):
        """Insert or replace rows by ts."""
        con = duckdb.connect(str(self.db_path))
        con.execute("INSERT OR REPLACE INTO ohlcv (ts, open, high, low, close, volume) "
                    "SELECT ts, open, high, low, close, volume FROM df")
        con.close()
        logger.info(f"[{self.symbol}] Upserted {len(df)} rows")

    def latest_ts(self) -> pd.Timestamp | None:
        con = duckdb.connect(str(self.db_path))
        row = con.execute("SELECT MAX(ts) FROM ohlcv").fetchone()
        con.close()
        return pd.Timestamp(row[0]) if row and row[0] else None

    @abstractmethod
    def fetch(self, start: str, end: str) -> pd.DataFrame:
        """Return DataFrame with columns: ts, open, high, low, close, volume."""
        ...

    def run(self, start: str, end: str):
        self.init_db()
        df = self.fetch(start, end)
        if df.empty:
            logger.warning(f"[{self.symbol}] No data returned for {start} → {end}")
            return
        self.upsert(df)
