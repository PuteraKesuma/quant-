"""Data audit package: integrity + continuity checks, repair, and a worker loop.

Shared helpers live here so the check/repair modules read the raw DuckDB
consistently (always in UTC) and survive transient file locks from a
concurrent fetch process.
"""
import time
from pathlib import Path
import duckdb
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def raw_db_path(symbol: str, cfg: dict) -> Path:
    return ROOT / cfg["data"]["raw_dir"] / f"{symbol}_1m.duckdb"


def connect_utc(db_path, read_only: bool = True, retries: int = 5, wait: float = 2.0):
    """Connect with the session timezone pinned to UTC, retrying on file locks
    (a background fetch may briefly hold the DuckDB file)."""
    last = None
    for _ in range(retries):
        try:
            con = duckdb.connect(str(db_path), read_only=read_only)
            con.execute("SET TimeZone='UTC'")
            return con
        except Exception as e:  # locked by the fetcher process, etc.
            last = e
            time.sleep(wait)
    raise last


def read_ohlcv(symbol: str, cfg: dict, columns: str = "ts, open, high, low, close, volume") -> pd.DataFrame:
    con = connect_utc(raw_db_path(symbol, cfg), read_only=True)
    df = con.execute(f"SELECT {columns} FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df
