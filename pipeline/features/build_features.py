"""Build feature parquets for all symbols from Level_0_Raw DuckDB files."""
import argparse
from pathlib import Path
import duckdb
import pandas as pd
import yaml
from loguru import logger

from pipeline.features.modules.orb_range import compute_orb
from pipeline.features.modules.session_labels import label_sessions
from pipeline.features.modules.volatility import atr, rolling_std
from pipeline.features.modules.time_features import time_features

ROOT = Path(__file__).parent.parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def load_ohlcv(symbol: str, cfg: dict) -> pd.DataFrame:
    db_path = ROOT / cfg["data"]["raw_dir"] / f"{symbol}_1m.duckdb"
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute("SELECT * FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def build(symbol: str, cfg: dict):
    logger.info(f"[{symbol}] Loading OHLCV...")
    df = load_ohlcv(symbol, cfg)

    feat = pd.DataFrame(index=df.index)
    feat = feat.join(time_features(df))
    feat["session"] = label_sessions(df)
    feat = feat.join(atr(df))
    feat = feat.join(rolling_std(df))
    feat["close"] = df["close"]

    out_dir = ROOT / cfg["data"]["features_dir"] / "modules"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}_features.parquet"
    feat.to_parquet(out_path)
    logger.info(f"[{symbol}] Features saved → {out_path}  shape={feat.shape}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "NAS100", "GBPUSD"])
    args = parser.parse_args()
    cfg = load_config()
    for sym in args.symbols:
        build(sym, cfg)


if __name__ == "__main__":
    main()
