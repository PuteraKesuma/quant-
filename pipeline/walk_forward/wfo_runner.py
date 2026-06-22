"""Walk-forward optimization runner."""
import argparse
from itertools import product
from pathlib import Path
import pandas as pd
import yaml
from loguru import logger

from pipeline.backtest.runner import run_backtest, load_config, load_ohlcv
from pipeline.backtest.strategy_orb import ORBParams
from pipeline.analysis.metrics import summary_stats
from pipeline.walk_forward.window_config import generate_windows

ROOT = Path(__file__).parent.parent.parent

PARAM_GRID = {
    "range_minutes": [15, 30, 60],
    "tp_multiplier": [0.5, 1.0, 1.5, 2.0],
    "sl_multiplier": [0.5, 1.0],
}


def best_params(results: list[dict]) -> dict:
    """Pick params with highest Sharpe on in-sample."""
    return max(results, key=lambda x: x.get("sharpe", -999))


def run_wfo(symbol: str, cfg: dict, start: str, end: str):
    windows = generate_windows(start, end, cfg)
    logger.info(f"[{symbol}] {len(windows)} WFO windows")

    all_oos = []
    for w in windows:
        logger.info(f"Window {w.index}: IS {w.train_start.date()}→{w.train_end.date()} | OOS {w.test_start.date()}→{w.test_end.date()}")
        is_results = []

        for rm, tp, sl in product(
            PARAM_GRID["range_minutes"],
            PARAM_GRID["tp_multiplier"],
            PARAM_GRID["sl_multiplier"],
        ):
            params = ORBParams(range_minutes=rm, tp_multiplier=tp, sl_multiplier=sl)
            trades_df = run_backtest(symbol, cfg, params,
                                     str(w.train_start.date()), str(w.train_end.date()))
            if len(trades_df) < cfg["walk_forward"]["min_trades"]:
                continue
            stats = summary_stats(trades_df)
            is_results.append({**stats, "range_minutes": rm, "tp_multiplier": tp, "sl_multiplier": sl})

        if not is_results:
            logger.warning(f"Window {w.index}: no valid IS params")
            continue

        best = best_params(is_results)
        logger.info(f"Window {w.index} best IS params: {best}")

        oos_params = ORBParams(
            range_minutes=best["range_minutes"],
            tp_multiplier=best["tp_multiplier"],
            sl_multiplier=best["sl_multiplier"],
        )
        oos_trades = run_backtest(symbol, cfg, oos_params,
                                  str(w.test_start.date()), str(w.test_end.date()))
        oos_stats = summary_stats(oos_trades)
        oos_stats["window"] = w.index
        oos_stats.update({k: best[k] for k in ["range_minutes", "tp_multiplier", "sl_multiplier"]})
        all_oos.append(oos_stats)

    out_df = pd.DataFrame(all_oos)
    out_dir = ROOT / cfg["data"]["datamart_dir"] / "walk_forward"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}_wfo.parquet"
    out_df.to_parquet(out_path, index=False)
    logger.info(f"[{symbol}] WFO results saved → {out_path}")
    return out_df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "NAS100", "GBPUSD"])
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end",   default="2024-12-31")
    args = parser.parse_args()
    cfg = load_config()
    for sym in args.symbols:
        run_wfo(sym, cfg, args.start, args.end)


if __name__ == "__main__":
    main()
