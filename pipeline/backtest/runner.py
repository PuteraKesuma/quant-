"""Backtest runner: loads data, generates signals, fills exits, saves results."""
import argparse
from pathlib import Path
import duckdb
import pandas as pd
import yaml
from loguru import logger

from pipeline.backtest.strategy_orb import ORBParams, generate_signals
from pipeline.backtest.engine import fill_exits, trades_to_df

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


def run_backtest(symbol: str, cfg: dict, params: ORBParams, start: str, end: str) -> pd.DataFrame:
    df = load_ohlcv(symbol, cfg)
    df = df[start:end]

    all_trades = []
    sessions = cfg["symbols"][symbol]["sessions"]
    for session_name, session_cfg in sessions.items():
        logger.info(f"[{symbol}] Generating signals for {session_name} session...")
        trades = generate_signals(df, symbol, session_name, session_cfg["open"], params)
        logger.info(f"[{symbol}:{session_name}] {len(trades)} signals generated")
        filled = fill_exits(trades, df)
        logger.info(f"[{symbol}:{session_name}] {len(filled)} trades filled")
        all_trades.extend(filled)

    result = trades_to_df(all_trades)
    out_dir = ROOT / cfg["data"]["datamart_dir"] / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}_backtest.parquet"
    result.to_parquet(out_path, index=False)
    logger.info(f"[{symbol}] Backtest saved → {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "NAS100", "GBPUSD"])
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end",   default="2023-12-31")
    args = parser.parse_args()

    cfg = load_config()
    orb_cfg = cfg["orb"]
    params = ORBParams(
        range_minutes=orb_cfg["range_minutes"],
        tp_multiplier=orb_cfg["tp_multiplier"],
        sl_multiplier=orb_cfg["sl_multiplier"],
        entry_buffer=orb_cfg["entry_buffer_pips"],
        max_trades_per_session=orb_cfg["max_trades_per_session"],
    )

    for sym in args.symbols:
        run_backtest(sym, cfg, params, args.start, args.end)


if __name__ == "__main__":
    main()
