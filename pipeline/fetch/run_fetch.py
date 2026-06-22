"""Entry point: fetch raw 1m OHLCV for all symbols from the chosen source.

Examples:
  # Deep history from Dukascopy (default source in config.yaml)
  python -m pipeline.fetch.run_fetch --start 2020-01-01 --end 2026-06-09

  # Recent data from MT5 terminal
  python -m pipeline.fetch.run_fetch --source mt5 --start 2026-01-01 --end 2026-06-09
"""
import argparse
from loguru import logger
from pipeline.fetch.registry import build_fetcher, load_config


def main():
    cfg = load_config()
    default_source = cfg.get("data", {}).get("source", "dukascopy")
    all_symbols = list(cfg["symbols"].keys())

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=all_symbols)
    parser.add_argument("--source", choices=["mt5", "dukascopy"], default=default_source)
    parser.add_argument("--start", required=True, help="e.g. 2020-01-01")
    parser.add_argument("--end",   required=True, help="e.g. 2026-06-09")
    args = parser.parse_args()

    for sym in args.symbols:
        logger.info(f"Fetching {sym} via {args.source}: {args.start} -> {args.end}")
        fetcher = build_fetcher(sym, args.source, cfg)
        fetcher.run(args.start, args.end)


if __name__ == "__main__":
    main()
