"""Tests for the signal-driven breakeven exit added to ORBStrategy._exit_hit.
risk = entry-sl = 1.0; tp = entry+1 (1R). breakeven_r=0.5 arms at +0.5R."""
import pandas as pd
from pipeline.backtest.strategy_orb import Trade
from pipeline.live.signal import ORBStrategy

STRAT = ORBStrategy.__new__(ORBStrategy)   # _exit_hit uses no instance state


def _trade(direction="long", entry=100.0, sl=99.0, tp=101.0):
    return Trade(symbol="NAS100", session="new_york",
                 date=pd.Timestamp("2026-06-26", tz="UTC"), direction=direction,
                 entry_ts=pd.Timestamp("2026-06-26 14:00", tz="UTC"),
                 entry_price=entry, tp_price=tp, sl_price=sl)


def _df(bars):  # bars: (minute, high, low)
    idx = [pd.Timestamp("2026-06-26 14:00", tz="UTC") + pd.Timedelta(minutes=m) for m, _, _ in bars]
    return pd.DataFrame({"high": [h for _, h, _ in bars], "low": [l for _, _, l in bars]}, index=idx)


def test_breakeven_exit_on_retrace_to_entry():
    # ran +0.6R (arms), then retraced to entry -> BE
    df = _df([(0, 100.0, 100.0), (1, 100.6, 100.2), (2, 100.4, 100.0)])
    assert STRAT._exit_hit(df, _trade(), True, 0.5) == "BE"


def test_no_breakeven_when_param_off():
    # same path but breakeven_r=None -> trade still open (no SL/TP touched)
    df = _df([(0, 100.0, 100.0), (1, 100.6, 100.2), (2, 100.4, 100.0)])
    assert STRAT._exit_hit(df, _trade(), True, None) is None


def test_sl_when_never_armed():
    # only +0.3R favourable (not armed), then drops to SL
    df = _df([(0, 100.0, 100.0), (1, 100.3, 100.1), (2, 100.2, 98.9)])
    assert STRAT._exit_hit(df, _trade(), True, 0.5) == "SL"


def test_tp_still_wins_with_breakeven_on():
    df = _df([(0, 100.0, 100.0), (1, 101.0, 100.5)])
    assert STRAT._exit_hit(df, _trade(), True, 0.5) == "TP"


def test_breakeven_short_side():
    # short entry 100, sl 101, tp 99; ran to 99.4 (+0.6R), retraced to 100 -> BE
    t = _trade(direction="short", entry=100.0, sl=101.0, tp=99.0)
    df = _df([(0, 100.0, 100.0), (1, 99.8, 99.4), (2, 100.0, 99.6)])
    assert STRAT._exit_hit(df, t, True, 0.5) == "BE"
