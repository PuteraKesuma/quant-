"""Performance metrics computed from a trades DataFrame."""
import numpy as np
import pandas as pd


def summary_stats(df: pd.DataFrame) -> dict:
    if df.empty or "pnl_r" not in df.columns:
        return {"trades": 0, "win_rate": 0, "expectancy": 0, "sharpe": 0, "max_dd_r": 0}

    r = df["pnl_r"].dropna()
    wins = (r > 0).sum()
    equity = r.cumsum()
    rolling_max = equity.cummax()
    drawdown = equity - rolling_max

    return {
        "trades":      len(r),
        "win_rate":    round(wins / len(r), 4),
        "expectancy":  round(r.mean(), 4),
        "sharpe":      round(r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0, 4),
        "profit_factor": round(r[r > 0].sum() / abs(r[r < 0].sum()), 4) if (r < 0).any() else float("inf"),
        "max_dd_r":    round(drawdown.min(), 4),
        "total_r":     round(r.sum(), 4),
    }


def equity_curve(df: pd.DataFrame) -> pd.Series:
    return df.set_index("exit_ts")["pnl_r"].sort_index().cumsum()
