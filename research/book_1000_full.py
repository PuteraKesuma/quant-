"""THE MISSING TEST: Z and Golden running TOGETHER in one account, position-state aware.

Golden was validated STANDALONE (PF 2.14, OOS 1.99, 11/11 WF, corr-to-Z +0.15). Z was validated
standalone. Nobody ever simulated them in the SAME account -- where they can hold OPPOSITE XAUUSD
positions at the same time (2026-07-17 live: Z short 0.01 +$18 while Golden long 0.02 hit SL -$45).
The live guard `_book_conflict` only blocks SAME-direction stacking; opposite-direction is allowed.

Answers, at $1000 path-dependent equity:
  1. Does Golden ADD to the book, or does it just fight Z?
  2. Does an opposite-direction guard help, hurt, or do nothing?
Variants: Golden OFF | ON (live, no guard) | ON + opposite-guard | ON + any-guard (never overlap Z).

Golden params read from live config.yaml. No-lookahead: H1 gates shifted +1h (only after the bar
closes); M5 signal from the completed bar, fill at the NEXT bar's open.
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1
from pipeline.backtest.strategy_zrev import resample_1h
from regime_fix import adx_daily, orb_usd, reversal_usd, daily

EQ0 = 1000.0
ADX_GATE = 28                      # Z's fixed trend-strength gate (research/regime_fix.py)
COST = 0.30                        # XAU spread, price units, once per trade
# --- Golden, from config.yaml golden_xau ---
G_NORM, G_LO, G_HI = 100, 15.0, 80.0
G_MACD = (5, 13, 9)
G_EMA_TREND, G_ADX_P, G_ADX_MAX = 15, 14, 40.0
G_ATR_P, G_ATR_MULT, G_TP_R = 14, 3.0, 3.0
G_LOT, G_LOT_FAV, G_SIZE_ADX = 0.01, 0.02, 20.0
# --- Z, from config.yaml / regime_fix ---
ENTRY_N = EXIT_N = 20; EMA = 100; DAILY_SMA = 50; ATR_MULT = 3.0
LOT_MIN, LOT_MAX, Z_LO, Z_HI = 0.01, 0.02, 0.5, 1.0


def z_trades_full(m1, adx_min=ADX_GATE):
    """Z with entry/exit timestamps + direction (regime_fix.accurate_z_usd, instrumented)."""
    h = resample_1h(m1.assign(volume=0)); c = h["close"]
    ema = c.ewm(span=EMA, adjust=False).mean()
    up = h["high"].rolling(ENTRY_N).max().shift(1); dn = h["low"].rolling(ENTRY_N).min().shift(1)
    xup = h["high"].rolling(EXIT_N).max().shift(1); xdn = h["low"].rolling(EXIT_N).min().shift(1)
    tr = pd.concat([h["high"]-h["low"], (h["high"]-c.shift()).abs(), (h["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    d1 = to_d1(m1); dsma = d1["close"].rolling(DAILY_SMA).mean()
    dtr = (d1["close"].shift(1) > dsma.shift(1)).map({True: 1, False: -1})
    dtd = {ts.date(): (0 if pd.isna(dtr.loc[ts]) else int(dtr.loc[ts])) for ts in d1.index}
    adx = adx_daily(d1).shift(1)
    adxd = {ts.date(): (0.0 if pd.isna(adx.loc[ts]) else float(adx.loc[ts])) for ts in d1.index}
    ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
    idx = h.index; C, H, L, O = c.values, h["high"].values, h["low"].values, h["open"].values
    upv, dnv, xuv, xdv, emv, atv = up.values, dn.values, xup.values, xdn.values, ema.values, atr.values
    m20, s20 = ma20.values, sd20.values
    dtl = [dtd.get(idx[i].date(), 0) for i in range(len(idx))]
    axl = [adxd.get(idx[i].date(), 0.0) for i in range(len(idx))]

    def dl(i, d):
        if s20[i] <= 0 or np.isnan(s20[i]): return LOT_MIN
        z = (C[i]-m20[i])/s20[i]; zd = z if d == 1 else -z
        f = max(0., min(1., (zd-Z_LO)/max(Z_HI-Z_LO, 1e-9)))
        return round(max(LOT_MIN, min(LOT_MAX, round((LOT_MIN+f*(LOT_MAX-LOT_MIN))/0.01)*0.01)), 2)

    pos = 0; ep = sl = elot = None; ets = None; out = []
    for i in range(1, len(idx)):
        if np.isnan(upv[i]) or np.isnan(emv[i]) or np.isnan(atv[i]): continue
        ut = C[i-1] > emv[i]; dd = dtl[i]; strong = axl[i] >= adx_min
        cl = ut and dd == 1 and strong; cs = (not ut) and dd == -1 and strong
        if pos == 0:
            if H[i] >= upv[i] and cl:
                pos, ep, sl, elot, ets = 1, max(O[i], upv[i]), max(O[i], upv[i])-ATR_MULT*atv[i], dl(i, 1), idx[i]
            elif L[i] <= dnv[i] and cs:
                pos, ep, sl, elot, ets = -1, min(O[i], dnv[i]), min(O[i], dnv[i])+ATR_MULT*atv[i], dl(i, -1), idx[i]
            continue
        if pos == 1:
            sl = max(sl, C[i-1]-ATR_MULT*atv[i])
            if L[i] <= sl:
                out.append((ets, idx[i], 1, ((min(O[i], sl)-ep)-COST)*elot*100)); pos = 0
            elif L[i] <= xdv[i]:
                out.append((ets, idx[i], 1, ((min(O[i], xdv[i])-ep)-COST)*elot*100))
                if L[i] <= dnv[i] and cs:
                    pos, ep, sl, elot, ets = -1, min(O[i], dnv[i]), min(O[i], dnv[i])+ATR_MULT*atv[i], dl(i, -1), idx[i]
                else: pos = 0
        else:
            sl = min(sl, C[i-1]+ATR_MULT*atv[i])
            if H[i] >= sl:
                out.append((ets, idx[i], -1, ((ep-max(O[i], sl))-COST)*elot*100)); pos = 0
            elif H[i] >= xuv[i]:
                out.append((ets, idx[i], -1, ((ep-max(O[i], xuv[i]))-COST)*elot*100))
                if H[i] >= upv[i] and cl:
                    pos, ep, sl, elot, ets = 1, max(O[i], upv[i]), max(O[i], upv[i])-ATR_MULT*atv[i], dl(i, 1), idx[i]
                else: pos = 0
    return out


def _adx_series(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100*pd.Series(plus, index=df.index).ewm(alpha=1/n, adjust=False).mean()/atr
    mdi = 100*pd.Series(minus, index=df.index).ewm(alpha=1/n, adjust=False).mean()/atr
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()


def golden_trades(m1, z_dir=None, guard=None):
    """guard: None (live today) | 'opposite' (skip if Z holds the other side) | 'any' (never overlap Z)."""
    m5 = m1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
    h1 = m1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])
    c = m5["close"]
    f, s, sig = G_MACD
    macd_sig = (c.ewm(span=f, adjust=False).mean() - c.ewm(span=s, adjust=False).mean()).rolling(sig).mean()
    mmn, mmx = macd_sig.rolling(G_NORM).min(), macd_sig.rolling(G_NORM).max()
    mnorm = (macd_sig - mmn) / (mmx - mmn).replace(0, np.nan) * 100
    pmn, pmx = c.rolling(G_NORM).min(), c.rolling(G_NORM).max()
    pnorm = (c - pmn) / (pmx - pmn).replace(0, np.nan) * 100
    tr5 = pd.concat([m5["high"]-m5["low"], (m5["high"]-c.shift()).abs(), (m5["low"]-c.shift()).abs()], axis=1).max(axis=1)
    atr5 = tr5.ewm(alpha=1/G_ATR_P, adjust=False).mean()
    # H1 gates: only available AFTER the bar closes -> stamp forward 1h, then ffill onto M5
    trend_h = np.sign(h1["close"].ewm(span=G_EMA_TREND, adjust=False).mean().diff())
    adx_h = _adx_series(h1, G_ADX_P)
    trend_h.index = trend_h.index + pd.Timedelta(hours=1); adx_h.index = adx_h.index + pd.Timedelta(hours=1)
    trend = trend_h.reindex(m5.index, method="ffill").values
    adxv = adx_h.reindex(m5.index, method="ffill").values

    idx = m5.index
    O, H, L, C = m5["open"].values, m5["high"].values, m5["low"].values, c.values
    mn, pn, at = mnorm.values, pnorm.values, atr5.values
    zd = z_dir.values if z_dir is not None else np.zeros(len(idx))
    out = []; i = 1
    while i < len(idx):
        j = i - 1                                          # decide on the COMPLETED bar
        if not (np.isfinite(mn[j]) and np.isfinite(pn[j]) and np.isfinite(adxv[i]) and np.isfinite(at[j])) \
           or at[j] <= 0 or not np.isfinite(trend[i]):
            i += 1; continue
        if adxv[i] > G_ADX_MAX:                            # over-extended -> stand aside
            i += 1; continue
        d = 0
        if mn[j] <= G_LO and pn[j] <= G_LO and trend[i] > 0: d = 1
        elif mn[j] >= G_HI and pn[j] >= G_HI and trend[i] < 0: d = -1
        if d == 0:
            i += 1; continue
        if guard == "any" and zd[i] != 0:
            i += 1; continue
        if guard == "opposite" and zd[i] != 0 and zd[i] != d:
            i += 1; continue
        price = C[j]                                       # live computes SL/TP off the completed close
        risk = G_ATR_MULT * at[j]
        sl = price - d*risk; tp = price + d*G_TP_R*risk
        lot = G_LOT_FAV if adxv[i] < G_SIZE_ADX else G_LOT
        en = O[i]                                          # fill at the next bar's open
        k = i; ex = None
        while k < len(idx):
            if d == 1:
                if L[k] <= sl: ex = sl; break              # SL first when both touch (conservative)
                if H[k] >= tp: ex = tp; break
            else:
                if H[k] >= sl: ex = sl; break
                if L[k] <= tp: ex = tp; break
            k += 1
        if ex is None: ex = C[-1]; k = len(idx)-1
        out.append((idx[i], idx[k], d, (d*(ex-en)-COST)*lot*100))
        i = k + 1
    return out


def dir_series(trades, index):
    """+1/-1 while a trade is open, else 0, on `index`."""
    s = np.zeros(len(index))
    for ets, xts, d, _ in trades:
        a, b = index.searchsorted(ets), index.searchsorted(xts)
        s[a:b+1] = d
    return pd.Series(s, index=index)


def simulate(base, cal, eq0=EQ0, step=1500.0):
    b = base.values.astype(float)
    eq = eq0; curve = np.empty(len(cal))
    for i in range(len(cal)):
        k = 1 if step is None else max(1, int(eq // step))
        eq += b[i] * k; curve[i] = eq
    return pd.Series(curve, index=cal)


def stats(curve, cal):
    dd = (curve - curve.cummax()) / curve.cummax()
    ret = curve.pct_change().fillna(0)
    greens = tot = 0
    for y in sorted(set(cal.year)):
        c = curve[cal.year == y]
        if len(c) < 5: continue
        tot += 1; greens += int(c.iloc[-1] > c.iloc[0])
    yrs = (cal[-1]-cal[0]).days/365.25
    return dict(term=curve.iloc[-1], cagr=(curve.iloc[-1]/EQ0)**(1/yrs)-1, mdd=float(dd.min()),
                greens=greens, tot=tot, sh=ret.mean()/ret.std()*np.sqrt(252) if ret.std() > 0 else np.nan)


xau = load_m1("XAUUSD"); nas = load_m1("NAS100")
ztr = z_trades_full(xau)
Z = daily([(x[1], x[3]) for x in ztr])
O = daily(orb_usd(nas)); R = daily(reversal_usd("NAS100"))
m5idx = xau.resample("5min").agg({"close": "last"}).dropna().index
zdir = dir_series(ztr, m5idx)

variants = {}
for label, guard, on in [("Golden OFF", None, False), ("Golden ON  (live, no guard)", None, True),
                         ("Golden ON  + opposite-guard", "opposite", True),
                         ("Golden ON  + any-guard", "any", True)]:
    if on:
        gtr = golden_trades(xau, z_dir=zdir, guard=guard)
        G = daily([(x[1], x[3]) for x in gtr])
        variants[label] = (G, gtr)
    else:
        variants[label] = (pd.Series(dtype=float), [])

lo = max(Z.index.min(), O.index.min()); hi = min(Z.index.max(), O.index.max(), R.index.max())
cal = pd.date_range(lo, hi, freq="B", tz="UTC")

print("=" * 100)
print(f"Z + GOLDEN IN ONE ACCOUNT  |  ${EQ0:,.0f} start, compound 1 lot-step per $1500  |  "
      f"{cal[0].date()} -> {cal[-1].date()}")
print("=" * 100)
print(f"{'variant':<30}{'final$':>9}{'CAGR':>7}{'maxDD':>7}  green{'Sharpe':>7}{'Gtr':>5}{'Gold$':>8}{'hedged':>8}")
print("-" * 100)
for label, (G, gtr) in variants.items():
    Gc = G.reindex(cal).fillna(0) if len(G) else pd.Series(0.0, index=cal)
    base = Z.reindex(cal).fillna(0) + O.reindex(cal).fillna(0) + R.reindex(cal).fillna(0) + Gc
    cur = simulate(base, cal); s = stats(cur, cal)
    nhedge = sum(1 for ets, _, d, _ in gtr if zdir.reindex([ets]).iloc[0] not in (0, d))
    print(f"{label:<30}{s['term']:>9,.0f}{s['cagr']:>7.0%}{s['mdd']:>7.0%}  {s['greens']}/{s['tot']}"
          f"{s['sh']:>7.2f}{len(gtr):>5}{Gc.sum():>+8.0f}{nhedge:>8}")

print("-" * 100)
print("Gtr = Golden trades taken | Gold$ = Golden's own P/L at base size | hedged = Golden entries that")
print("opened while Z held the OPPOSITE side (what happened live on 2026-07-17).")
print("DONE")
