"""Entry point for the data audit worker.

  python -m pipeline.audit.run_audit --once             # single pass, then exit
  python -m pipeline.audit.run_audit --watch            # continuous worker (default)
  python -m pipeline.audit.run_audit --once --symbols XAUUSD
"""
import argparse
from . import load_config
from .worker import run_cycle, watch


def main():
    cfg = load_config()
    all_symbols = list(cfg["symbols"].keys())

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=all_symbols)
    parser.add_argument("--once",  action="store_true", help="run a single cycle then exit")
    parser.add_argument("--watch", action="store_true", help="run continuously (default)")
    args = parser.parse_args()

    if args.once:
        run_cycle(args.symbols, cfg)
    else:
        watch(args.symbols, cfg)


if __name__ == "__main__":
    main()
