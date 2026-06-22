"""Forward test runner: runs ORB with fixed params on unseen data."""
import argparse
from pathlib import Path
import yaml
from loguru import logger

from pipeline.backtest.runner import run_backtest, load_config
from pipeline.backtest.strategy_orb import ORBParams

ROOT = Path(__file__).parent.parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "NAS100", "GBPUSD"])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end",   required=True)
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
        logger.info(f"[{sym}] Forward test {args.start} → {args.end}")
        df = run_backtest(sym, cfg, params, args.start, args.end)
        out_dir = ROOT / cfg["data"]["datamart_dir"] / "forward_test"
        out_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_dir / f"{sym}_forward_test.parquet", index=False)
        logger.info(f"[{sym}] Forward test saved  trades={len(df)}")


if __name__ == "__main__":
    main()
