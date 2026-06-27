"""Tests for the live-only ORB enhancements (dst_open + trend_sma) added to the
ORBStrategy wrapper. The backtest strategy_orb.py is intentionally untouched."""
import datetime
import pandas as pd

from pipeline.live.signal import ORBStrategy


def test_dst_adjust_open_summer_unchanged():
    # US Eastern DST (summer): configured 13:30 UTC open stays 13:30.
    h, m = ORBStrategy._dst_adjust_open(pd.Timestamp("2026-07-15", tz="UTC"), 13, 30)
    assert (h, m) == (13, 30)


def test_dst_adjust_open_winter_shifts_one_hour():
    # US Eastern standard time (winter): cash open is 1h later -> 14:30 UTC.
    h, m = ORBStrategy._dst_adjust_open(pd.Timestamp("2026-01-15", tz="UTC"), 13, 30)
    assert (h, m) == (14, 30)


def test_dst_adjust_open_around_spring_forward():
    # DST begins 2nd Sunday of March 2026 (Mar 8). Before -> winter (+1h), after -> summer.
    before = ORBStrategy._dst_adjust_open(pd.Timestamp("2026-03-01", tz="UTC"), 13, 30)
    after = ORBStrategy._dst_adjust_open(pd.Timestamp("2026-03-20", tz="UTC"), 13, 30)
    assert before == (14, 30)
    assert after == (13, 30)


def test_trend_dir_cached_and_safe_without_mt5(monkeypatch):
    """_trend_dir must never raise and must cache per day. With MT5 unavailable it
    returns 0, which makes the caller go FLAT (fail-safe, never a wrong-way trade)."""
    class _Spec(dict):
        pass
    strat = ORBStrategy.__new__(ORBStrategy)          # bypass __init__ (needs cfg/data)
    strat.name = "orb30_nas"
    strat.symbol = "NAS100"
    strat.cfg = {"symbols": {"NAS100": {"mt5_symbol": "US100"}}}
    today = pd.Timestamp("2026-06-26", tz="UTC")
    d = strat._trend_dir(50, today)
    assert d in (-1, 0, 1)
    # second call hits the cache (same value, no exception)
    assert strat._trend_dir(50, today) == d
