"""Z Strategy — stop-and-reverse Donchian breakout (XAUUSD 1H).

Always-in-market: after the first fill the net position is ALWAYS +1 (long) or
-1 (short). Entry is via pending STOP orders parked at the Donchian channel:

  - while SHORT, a BUY_STOP rests at the N-bar HIGH  -> break up   = reverse to long
  - while LONG,  a SELL_STOP rests at the N-bar LOW  -> break down = reverse to short

Pure stop-and-reverse: the opposite breakout is the ONLY exit (no fixed TP/SL).
Because the Donchian level trails with price, the resting reverse level behaves as
a flexible / trailing stop — matching the "flexible TP/SL" behaviour seen on chart.

Signals + simulation run on 1H bars. The channel only updates at each hourly close,
and a resting stop fills at its level regardless of the intra-hour path, so 1H bars
are *exact* for this strategy (no need to walk 1m). Gaps that open beyond the level
fill at the bar open (realistic slippage).
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class ZRevParams:
    donchian_n: int = 20        # ENTRY channel lookback in 1H bars
    exit_n: int = 0             # EXIT channel lookback; 0 => same as entry (pure always-in S&R).
                                # >0 (and < donchian_n) => tighter exit, system can go flat (Turtle-style)
    cost_points: float = 0.30   # cost deducted per reversal (spread+commission, price points)
    # --- optional trend filter (default off => pure always-in S&R, unchanged) ---
    trend_filter: bool = False  # gate entries by EMA trend; against-trend break = exit FLAT, not reverse
    trend_ema: int = 200        # EMA period on 1H close
    adx_filter: bool = False    # additionally require ADX >= adx_min to take a NEW position
    adx_period: int = 14
    adx_min: float = 20.0


@dataclass
class ZTrade:
    direction:   str               # "long" | "short"
    entry_ts:    pd.Timestamp
    entry_price: float
    exit_ts:     pd.Timestamp | None = None
    exit_price:  float | None = None
    pnl_points:  float | None = None
    bars_held:   int | None = None


def resample_1h(df_1m: pd.DataFrame) -> pd.DataFrame:
    """1m OHLCV (UTC index) -> 1H OHLCV, dropping empty (weekend) hours."""
    h = (df_1m.resample("1h")
              .agg({"open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"})
              .dropna(subset=["open"]))
    return h


def _adx(h: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ADX on the given OHLC frame."""
    up = h["high"].diff()
    dn = -h["low"].diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([
        h["high"] - h["low"],
        (h["high"] - h["close"].shift()).abs(),
        (h["low"] - h["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.fillna(0.0).ewm(alpha=1 / period, adjust=False).mean()


def donchian_levels(h: pd.DataFrame, params: "ZRevParams") -> pd.DataFrame:
    """Add `upper`/`lower` (= extreme of prior n bars, shift 1 => no lookahead) plus
    optional `trend` (+1/-1 from EMA) and `adx_ok` gate columns. All indicators are
    shifted 1 bar so a decision on bar t uses only completed bars t-n .. t-1."""
    out = h.copy()
    out["upper"] = h["high"].rolling(params.donchian_n).max().shift(1)
    out["lower"] = h["low"].rolling(params.donchian_n).min().shift(1)

    # exit channel: 0 => identical to entry (pure S&R); else a separate (tighter) channel
    ex_n = params.exit_n if params.exit_n and params.exit_n > 0 else params.donchian_n
    out["exit_up"] = h["high"].rolling(ex_n).max().shift(1)
    out["exit_dn"] = h["low"].rolling(ex_n).min().shift(1)

    if params.trend_filter:
        ema = h["close"].ewm(span=params.trend_ema, adjust=False).mean()
        out["trend"] = (h["close"] > ema).map({True: 1, False: -1}).shift(1)
    else:
        out["trend"] = 0  # 0 = no constraint

    if params.adx_filter:
        out["adx_ok"] = (_adx(h, params.adx_period) >= params.adx_min).shift(1).fillna(False).astype(bool)
    else:
        out["adx_ok"] = True

    return out.dropna(subset=["upper", "lower", "exit_up", "exit_dn"])


def simulate(h: pd.DataFrame, params: ZRevParams) -> list[ZTrade]:
    """Run the stop-and-reverse simulation over 1H bars.

    No filter -> pure always-in S&R. With trend/ADX filter -> trend-following with
    flat: an against-trend breakout EXITS the position instead of reversing, and new
    entries are only taken in the trend direction (and when ADX confirms).
    """
    lv = donchian_levels(h, params)
    trades: list[ZTrade] = []
    cost = params.cost_points
    gated = params.trend_filter or params.adx_filter

    pos = 0                        # +1 long, -1 short, 0 flat
    entry_price = entry_ts = None
    entry_idx = None

    for i, r in enumerate(lv.itertuples(index=True)):
        ts, o, hi, lo = r.Index, r.open, r.high, r.low
        e_up, e_dn = r.upper, r.lower          # ENTRY channel (donchian_n)
        x_up, x_dn = r.exit_up, r.exit_dn      # EXIT channel (exit_n, tighter or equal)
        can_long  = (r.trend != -1) and r.adx_ok
        can_short = (r.trend != 1)  and r.adx_ok

        if pos == 0:
            if hi >= e_up and can_long:
                pos, entry_price, entry_ts, entry_idx = 1, max(o, e_up), ts, i
            elif lo <= e_dn and can_short:
                pos, entry_price, entry_ts, entry_idx = -1, min(o, e_dn), ts, i
            continue

        if pos == 1:
            if lo <= x_dn:                                 # exit long on EXIT channel break
                fill = min(o, x_dn)
                pnl = (fill - entry_price) - cost
                trades.append(ZTrade("long", entry_ts, entry_price, ts, fill, pnl, i - entry_idx))
                # reverse to short only if the ENTRY channel also broke (same bar) AND trend allows
                if lo <= e_dn and can_short:
                    f2 = min(o, e_dn)
                    pos, entry_price, entry_ts, entry_idx = -1, f2, ts, i
                else:
                    pos, entry_price, entry_ts, entry_idx = 0, None, None, None
        else:                                              # pos == -1
            if hi >= x_up:                                 # exit short on EXIT channel break
                fill = max(o, x_up)
                pnl = (entry_price - fill) - cost
                trades.append(ZTrade("short", entry_ts, entry_price, ts, fill, pnl, i - entry_idx))
                if hi >= e_up and can_long:
                    f2 = max(o, e_up)
                    pos, entry_price, entry_ts, entry_idx = 1, f2, ts, i
                else:
                    pos, entry_price, entry_ts, entry_idx = 0, None, None, None

    return trades


def metrics(trades: list[ZTrade]) -> dict:
    """Summary stats. PnL is in PRICE POINTS (USD per oz); 0.01 XAU lot = 1 oz => $1/point."""
    if not trades:
        return {"trades": 0}
    pnl = np.array([t.pnl_points for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    equity = np.cumsum(pnl)
    dd = equity - np.maximum.accumulate(equity)
    gross_win, gross_loss = wins.sum(), -losses.sum()
    return {
        "trades":        len(pnl),
        "net_points":    round(float(pnl.sum()), 2),
        "win_rate":      round(len(wins) / len(pnl), 4),
        "profit_factor": round(float(gross_win / gross_loss), 3) if gross_loss > 0 else float("inf"),
        "avg_points":    round(float(pnl.mean()), 3),
        "avg_win":       round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss":      round(float(losses.mean()), 3) if len(losses) else 0.0,
        "max_dd_points": round(float(dd.min()), 2),
        "net_usd_001":   round(float(pnl.sum()), 2),   # $ at 0.01 lot (1 oz, $1/point)
    }


def trades_to_df(trades: list[ZTrade]) -> pd.DataFrame:
    return pd.DataFrame([t.__dict__ for t in trades])
