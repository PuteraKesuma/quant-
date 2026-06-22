"""Generate HTML/markdown performance reports."""
from pathlib import Path
import pandas as pd
from loguru import logger
from .metrics import summary_stats, equity_curve

ROOT = Path(__file__).parent.parent.parent


def generate_report(symbol: str, mode: str = "backtest"):
    """mode: backtest | forward_test | walk_forward"""
    parquet = ROOT / "data" / "Level_2_Datamart" / mode / f"{symbol}_{mode}.parquet"
    if not parquet.exists():
        logger.error(f"No results found at {parquet}")
        return

    df = pd.read_parquet(parquet)
    stats = summary_stats(df)

    lines = [
        f"# {symbol} — {mode.replace('_',' ').title()} Report",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
    ]
    for k, v in stats.items():
        lines.append(f"| {k} | {v} |")

    out = ROOT / "_DOC" / "_PRD" / f"{symbol}_{mode}_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    logger.info(f"Report saved → {out}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "NAS100", "GBPUSD"])
    parser.add_argument("--mode", default="backtest",
                        choices=["backtest", "forward_test", "walk_forward"])
    args = parser.parse_args()
    for sym in args.symbols:
        generate_report(sym, args.mode)


if __name__ == "__main__":
    main()
