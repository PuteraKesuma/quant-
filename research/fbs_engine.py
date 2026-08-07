"""Shared engine for backtesting on FBS/MT5 broker data. SINGLE SOURCE OF TRUTH — import these,
never copy them (two divergent copies of a live strategy's engine is how a money system rots).

Contents:
  server_offset()      broker server time - UTC, whole hours (FBS=+3 summer)
  fbs_bars()           MT5 bars -> DataFrame indexed by TRUE UTC (the offset pipeline/fetch/mt5_fetcher.py forgets)
  z_usd_from_bars()    the LIVE Z strategy taking H1 + D1 bars directly (no M1 needed) -> $ per trade at base lot

Z params mirror config.yaml live.strategies[zrev_xau]: Donchian-20 S&R, H1 EMA100 gate, daily SMA50
gate, daily Wilder-ADX(14) >= 28 strength gate (shift(1) = previous completed day), trailing 3xATR
stop, z-score dynamic lot 0.01-0.02. Logic identical to research/regime_fix.accurate_z_usd.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from regime_fix import adx_daily

ENTRY_N = EXIT_N = 20
EMA = 100
DAILY_SMA = 50
ATR_MULT = 3.0
COST = 0.30                       # XAU spread in price units, once per trade (FBS median measured $0.23)
ADX_GATE = 28
LOT_MIN, LOT_MAX, Z_LO, Z_HI = 0.01, 0.02, 0.5, 1.0


def server_offset():
    """Broker server time minus UTC in whole hours. Mirrors pipeline/live/data.py::_server_offset_hours.
    MT5 bar timestamps are SERVER time; without this every UTC session window is wrong."""
    tick = mt5.symbol_info_tick("XAUUSD")
    return round((pd.Timestamp(tick.time, unit="s", tz="UTC") - pd.Timestamp.utcnow()).total_seconds() / 3600.0)


def fbs_bars(sym, tf, off, n=99_999):
    """MT5 bars -> DataFrame indexed by TRUE UTC. Caller must mt5.initialize() first.
    n is capped by the terminal's maxbars setting (100,000 here) -- asking for more errors out."""
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) == 0:
        raise RuntimeError(f"no bars for {sym} tf={tf}: {mt5.last_error()}")
    df = pd.DataFrame(r)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=off)
    keep = [c for c in ("open", "high", "low", "close", "tick_volume", "spread") if c in df.columns]
    return df.set_index("ts")[keep].sort_index()


def z_usd_from_bars(h, d1, adx_min=ADX_GATE, cost=COST):
    """The live Z strategy, driven by H1 + D1 bars directly (so it can run on FBS H1/D1, which are deep,
    without FBS M1, which is only ~100 days). Returns [(exit_ts, usd_at_base_lot), ...].
    Identical logic to research/regime_fix.accurate_z_usd -- keep them in sync."""
    c = h["close"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - c.shift()).abs(), (h["low"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtr = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dtd = {ts.date(): (0 if pd.isna(dtr.loc[ts]) else int(dtr.loc[ts])) for ts in d1.index}
    adx = adx_daily(d1).shift(1)                       # previous completed day -> no lookahead
    adxd = {ts.date(): (0.0 if pd.isna(adx.loc[ts]) else float(adx.loc[ts])) for ts in d1.index}
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
    idx = h.index
    C, H, L, O = c.values, h["high"].values, h["low"].values, h["open"].values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    m20, s20 = ma20.values, sd20.values
    dtl = [dtd.get(idx[i].date(), 0) for i in range(len(idx))]
    axl = [adxd.get(idx[i].date(), 0.0) for i in range(len(idx))]

    def dl(i, d):
        if s20[i] <= 0 or np.isnan(s20[i]): return LOT_MIN
        z = (C[i] - m20[i]) / s20[i]; zd = z if d == 1 else -z
        f = max(0., min(1., (zd - Z_LO) / max(Z_HI - Z_LO, 1e-9)))
        return round(max(LOT_MIN, min(LOT_MAX, round((LOT_MIN + f * (LOT_MAX - LOT_MIN)) / 0.01) * 0.01)), 2)

    pos = 0; ep = sl = elot = None; out = []
    for i in range(1, len(idx)):
        if np.isnan(upv[i]) or np.isnan(emv[i]) or np.isnan(atv[i]): continue
        ut = C[i - 1] > emv[i]; dd = dtl[i]; strong = axl[i] >= adx_min
        cl = ut and dd == 1 and strong; cs = (not ut) and dd == -1 and strong
        if pos == 0:
            if H[i] >= upv[i] and cl:
                pos, ep, sl, elot = 1, max(O[i], upv[i]), max(O[i], upv[i]) - ATR_MULT * atv[i], dl(i, 1)
            elif L[i] <= dnv[i] and cs:
                pos, ep, sl, elot = -1, min(O[i], dnv[i]), min(O[i], dnv[i]) + ATR_MULT * atv[i], dl(i, -1)
            continue
        if pos == 1:
            sl = max(sl, C[i - 1] - ATR_MULT * atv[i])
            if L[i] <= sl:
                out.append((idx[i], ((min(O[i], sl) - ep) - cost) * elot * 100)); pos = 0
            elif L[i] <= xdv[i]:
                out.append((idx[i], ((min(O[i], xdv[i]) - ep) - cost) * elot * 100))
                if L[i] <= dnv[i] and cs:
                    pos, ep, sl, elot = -1, min(O[i], dnv[i]), min(O[i], dnv[i]) + ATR_MULT * atv[i], dl(i, -1)
                else: pos = 0
        else:
            sl = min(sl, C[i - 1] + ATR_MULT * atv[i])
            if H[i] >= sl:
                out.append((idx[i], ((ep - max(O[i], sl)) - cost) * elot * 100)); pos = 0
            elif H[i] >= xuv[i]:
                out.append((idx[i], ((ep - max(O[i], xuv[i])) - cost) * elot * 100))
                if H[i] >= upv[i] and cl:
                    pos, ep, sl, elot = 1, max(O[i], upv[i]), max(O[i], upv[i]) - ATR_MULT * atv[i], dl(i, 1)
                else: pos = 0
    return out
