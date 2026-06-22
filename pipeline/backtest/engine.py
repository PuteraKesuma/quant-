"""Bar-by-bar backtest engine: fills trade exits against 1m OHLCV."""
import pandas as pd
from loguru import logger
from .strategy_orb import Trade


def fill_exits(trades: list[Trade], df: pd.DataFrame) -> list[Trade]:
    """
    For each trade, scan bars after entry to find first TP or SL hit.
    df: 1m OHLCV with UTC DatetimeIndex (full history, not just one day).
    """
    filled = []
    for t in trades:
        post = df[df.index > t.entry_ts]
        exit_ts = exit_price = exit_reason = None

        for ts, bar in post.iterrows():
            if t.direction == "long":
                if bar["low"] <= t.sl_price:
                    exit_ts, exit_price, exit_reason = ts, t.sl_price, "SL"
                    break
                if bar["high"] >= t.tp_price:
                    exit_ts, exit_price, exit_reason = ts, t.tp_price, "TP"
                    break
            else:
                if bar["high"] >= t.sl_price:
                    exit_ts, exit_price, exit_reason = ts, t.sl_price, "SL"
                    break
                if bar["low"] <= t.tp_price:
                    exit_ts, exit_price, exit_reason = ts, t.tp_price, "TP"
                    break

        if exit_ts is None:
            logger.debug(f"Trade {t.entry_ts} never closed — skipped")
            continue

        risk = abs(t.entry_price - t.sl_price)
        pnl_r = (exit_price - t.entry_price) / risk if t.direction == "long" \
               else (t.entry_price - exit_price) / risk

        t.exit_ts     = exit_ts
        t.exit_price  = exit_price
        t.exit_reason = exit_reason
        t.pnl_r       = round(pnl_r, 4)
        filled.append(t)

    return filled


def trades_to_df(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([t.__dict__ for t in trades])
