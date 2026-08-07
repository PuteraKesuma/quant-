"""Full per-trade table for the combined book, Jan-Jul 2026, $1000 equity, 0.01 lots (real live sizing).
Sleeves: Reversal US100 (LIVE), JPY carry (LIVE), Gold-trend + NAS-trend (shadow). Each trade: entry,
dir, entry px, SL, exit, exit px, exit reason, PnL$, running equity. $/pt at 0.01 lot: US100 $0.10,
XAU $1.00, USDJPY ~1000*(dP/P)+swap. Reversal has no fixed TP (exits close>SMA5); trend = stop&reverse
(SL = opposite Donchian channel); carry SL 156.
"""
import os, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, dukascopy_python

Y0 = pd.Timestamp("2026-01-01", tz="UTC")
EQ0 = 1000.0
SWAP_JPY_NIGHT = 0.025  # $/night at 0.01 lot (long, real FBS +0.91%/yr)


def daily(code):
    df = dukascopy_python.fetch(code, dukascopy_python.INTERVAL_DAY_1, dukascopy_python.OFFER_SIDE_BID,
                                dt.datetime(2015, 1, 1), dt.datetime.utcnow())
    df = df.rename(columns=str.lower)
    for k in ("open", "high", "low", "close"):
        df[k] = pd.to_numeric(df[k])
    df.index = pd.to_datetime(df.index, utc=True)
    return df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def reversal_trades(df, ppt):
    c = df["close"]; r2 = rsi(c, 2); s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    T = []; ip = False; ei = None
    idx = c.index
    for i in range(1, len(c)):
        if not ip and r2.iloc[i] < 10 and c.iloc[i] > s200.iloc[i]:
            ip = True; ei = i; sl = c.iloc[i] * 0.95
        elif ip:
            hit_sl = df["low"].iloc[i] <= sl
            exit_sig = c.iloc[i] > s5.iloc[i]
            if hit_sl or exit_sig:
                xpx = sl if hit_sl else c.iloc[i]
                T.append(("Reversal US100", idx[ei], "LONG", c.iloc[ei], sl, idx[i], xpx,
                          "SL" if hit_sl else "exit>SMA5", (xpx - c.iloc[ei]) * ppt))
                ip = False
    if ip:
        T.append(("Reversal US100", idx[ei], "LONG", c.iloc[ei], sl, idx[-1], c.iloc[-1], "OPEN",
                  (c.iloc[-1] - c.iloc[ei]) * ppt))
    return T


def jpy_trades(df):
    c = df["close"]; sma = c.rolling(100).mean(); net = 0.91  # net carry % (real) > 0 in 2026
    gate = (c > sma) & (net > 0)
    T = []; ip = False; ei = None
    idx = c.index
    for i in range(1, len(c)):
        if not ip and gate.iloc[i]:
            ip = True; ei = i
        elif ip and not gate.iloc[i]:
            nights = (idx[i] - idx[ei]).days
            pnl = 1000 * (c.iloc[i] - c.iloc[ei]) / c.iloc[i] + SWAP_JPY_NIGHT * nights
            T.append(("JPY carry", idx[ei], "LONG", c.iloc[ei], 156.0, idx[i], c.iloc[i], "gate off", pnl)); ip = False
    if ip:
        nights = (idx[-1] - idx[ei]).days
        pnl = 1000 * (c.iloc[-1] - c.iloc[ei]) / c.iloc[-1] + SWAP_JPY_NIGHT * nights
        T.append(("JPY carry", idx[ei], "LONG", c.iloc[ei], 156.0, idx[-1], c.iloc[-1], "OPEN", pnl))
    return T


def trend_trades(df, ppt, name, N=100):
    c = df["close"]; up = c.rolling(N).max().shift(1); dn = c.rolling(N).min().shift(1)
    pos = pd.Series(np.nan, index=c.index); pos[c >= up] = 1; pos[c <= dn] = -1; pos = pos.ffill()
    T = []; idx = c.index; st = None; d0 = None
    for i in range(1, len(pos)):
        if pos.iloc[i] != pos.iloc[i - 1] and not np.isnan(pos.iloc[i]):
            if st is not None and not np.isnan(d0):
                sl = dn.iloc[st] if d0 == 1 else up.iloc[st]           # opposite channel = reverse level
                T.append((f"{name} trend", idx[st], "LONG" if d0 == 1 else "SHORT", c.iloc[st], sl,
                          idx[i], c.iloc[i], "reverse", d0 * (c.iloc[i] - c.iloc[st]) * ppt))
            st = i; d0 = pos.iloc[i]
    if st is not None and not np.isnan(d0):
        sl = dn.iloc[st] if d0 == 1 else up.iloc[st]
        T.append((f"{name} trend", idx[st], "LONG" if d0 == 1 else "SHORT", c.iloc[st], sl,
                  idx[-1], c.iloc[-1], "OPEN", d0 * (c.iloc[-1] - c.iloc[st]) * ppt))
    return T


NAS = daily("E_NQ-100"); GOLD = daily("XAU/USD"); JPY = daily("USD/JPY")
allT = (reversal_trades(NAS, 0.10) + jpy_trades(JPY) + trend_trades(GOLD, 1.0, "GOLD") + trend_trades(NAS, 0.10, "NAS"))
allT = [t for t in allT if t[1] >= Y0]                    # ENTERED in 2026 only
allT.sort(key=lambda t: t[1])
LIVE = {"Reversal US100", "JPY carry"}
hdr = f"{'Sleeve':15} {'Entry':10} {'Dir':5} {'EntryPx':>9} {'SL':>9} {'Exit':10} {'ExitPx':>9} {'Reason':10} {'PnL$':>8} {'Equity':>9}"


def show(title, rows, eq0):
    print(f"\n{title}"); print(hdr); print("-" * len(hdr))
    eq = eq0; tot = 0.0
    for s, en, d, ep, sl, xd, xp, why, pnl in rows:
        eq += pnl; tot += pnl
        print(f"{s:15} {en:%Y-%m-%d} {d:5} {ep:>9.2f} {sl:>9.2f} {xd:%Y-%m-%d} {xp:>9.2f} {why:10} {pnl:>+8.2f} {eq:>9.2f}")
    print("-" * len(hdr))
    print(f"{'SUBTOTAL':15} {len(rows)} trd -> PnL {tot:>+.2f}   equity ${eq:.2f}  ({tot/eq0:+.1%})")
    return tot


print(f"BOOK TRADES entered Jan-Jul 2026  (equity ${EQ0:.0f}, 0.01 lots)   data thru {NAS.index.max():%Y-%m-%d}")
live = [t for t in allT if t[0] in LIVE]
shadow = [t for t in allT if t[0] not in LIVE]
lt = show("=== LIVE daily book (Reversal US100 3x-lev + JPY carry 1x-lev) ===", live, EQ0)
st = show("=== SHADOW trend (GOLD/NAS Donchian) — 0.01 lot = 4x/3x OVER-leveraged at $1000, why it's not live ===", shadow, EQ0)
print(f"\nLIVE book: ${EQ0:.0f} -> ${EQ0+lt:.2f} ({lt/EQ0:+.1%})   |   +trend(shadow): -> ${EQ0+lt+st:.2f}")
print("DONE")
