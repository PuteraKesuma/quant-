"""ETERNA fase-24: SHADOW CHECK — data live MT5 vs data riset (Dukascopy).

Logika class sudah terbukti identik dengan riset (eterna_parity3.py, 100%). Yang BELUM
diuji: apakah bar H1 dari terminal MT5 (FBS) sama dengan bar H1 Dukascopy yang dipakai
riset. Kalau bedanya besar, sinyal live bisa menyimpang walau kodenya benar.

Ini celah yang dulu menangkap bug ORB (commit d4f6028: window salah -> mismatch palsu),
dan sumber beda yang wajar: waktu server broker (FBS UTC+3), presisi 2 digit vs 3 digit,
jam tutup pekan, dan feed yang memang berbeda penyedia.

TIDAK ADA ORDER YANG DIKIRIM. Skrip ini murni membaca.

Jalankan: python research/eterna_shadow_check.py
"""
import warnings
warnings.filterwarnings("ignore")

import datetime as dt
import duckdb
import numpy as np
import pandas as pd
import yaml

import MetaTrader5 as mt5
from pipeline.live.data import DataProvider
from pipeline.live.signal import EternaStrategy

P, MULT_E, MULT_T, TP_R, MIN_SL = 16, 1.8, 3.8, 4.0, 0.30
LOT, CONTRACT = 0.01, 100.0


def atr_s(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, mult):
    a = atr_s(df, P)
    hl2 = (df["high"] + df["low"]) / 2.0
    up = (hl2 + mult * a).to_numpy(); lo = (hl2 - mult * a).to_numpy()
    c = df["close"].to_numpy(); n = len(df)
    fu = np.full(n, np.nan); fl = np.full(n, np.nan); d = np.ones(n, dtype=int)
    for i in range(1, n):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            continue
        fu[i] = up[i] if (np.isnan(fu[i-1]) or up[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lo[i] if (np.isnan(fl[i-1]) or lo[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
        if not np.isnan(fu[i-1]) and c[i] > fu[i]:
            d[i] = 1
        elif not np.isnan(fl[i-1]) and c[i] < fl[i]:
            d[i] = -1
        else:
            d[i] = d[i-1]
    return pd.Series(d, index=df.index)


def main():
    cfg = yaml.safe_load(open(r"C:\Quant\config.yaml", encoding="utf-8"))
    spec = [x for x in cfg["live"]["strategies"] if x["name"] == "eterna_xau"][0]

    # ---------- 1. bar dari MT5 lewat DataProvider (menangani offset server->UTC) ----------
    dp = DataProvider(cfg)
    m1 = dp.recent_bars("XAUUSD", 30000)
    print("=" * 96)
    print("1. BAR M1 DARI MT5 (via DataProvider, sudah dikoreksi ke UTC)")
    print("=" * 96)
    print(f"  {len(m1):,} bar   {m1.index[0]}  ..  {m1.index[-1]}")
    h_mt5 = m1.resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    print(f"  -> {len(h_mt5):,} bar H1")
    print(h_mt5.tail(3).to_string())

    # ---------- 2. bar Dukascopy untuk periode yang sama ----------
    import dukascopy_python
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD
    duka = dukascopy_python.fetch(
        INSTRUMENT_FX_METALS_XAU_USD, dukascopy_python.INTERVAL_MIN_1,
        dukascopy_python.OFFER_SIDE_BID,
        m1.index[0].to_pydatetime().replace(tzinfo=None),
        dt.datetime.utcnow())
    duka.index = pd.to_datetime(duka.index, utc=True)
    h_dk = duka[["open", "high", "low", "close"]].resample(
        "1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

    print("\n" + "=" * 96)
    print("2. PERBANDINGAN BAR H1 — MT5(FBS) vs Dukascopy")
    print("=" * 96)
    common = h_mt5.index.intersection(h_dk.index)
    print(f"  bar H1 beririsan: {len(common)}")
    if len(common) < 50:
        print("  *** terlalu sedikit irisan — periksa offset waktu server MT5 ***")
        mt5.shutdown(); return
    a, b = h_mt5.loc[common], h_dk.loc[common]
    for col in ("open", "high", "low", "close"):
        d = (a[col] - b[col]).abs()
        print(f"  {col:6}: beda rata-rata ${d.mean():6.3f}  median ${d.median():6.3f}  "
              f"maks ${d.max():7.3f}")
    corr = a["close"].corr(b["close"])
    print(f"  korelasi close: {corr:.6f}")

    # ---------- 3. Supertrend & keputusan pada kedua feed ----------
    print("\n" + "=" * 96)
    print("3. APAKAH SINYALNYA SAMA DI KEDUA FEED?")
    print("=" * 96)
    n = min(len(a), len(b))
    ste_a, stt_a = supertrend(a, MULT_E), supertrend(a, MULT_T)
    ste_b, stt_b = supertrend(b, MULT_E), supertrend(b, MULT_T)
    tail = 300
    same_e = (ste_a.iloc[-tail:].values == ste_b.iloc[-tail:].values).mean()
    same_t = (stt_a.iloc[-tail:].values == stt_b.iloc[-tail:].values).mean()
    print(f"  arah Supertrend ENTRY sama : {100*same_e:.1f}%  ({tail} bar terakhir)")
    print(f"  arah Supertrend TREN  sama : {100*same_t:.1f}%")
    print(f"  Supertrend entry MT5 sekarang : {'UP' if ste_a.iloc[-1]==1 else 'DOWN'}")
    print(f"  Supertrend tren  MT5 sekarang : {'UP' if stt_a.iloc[-1]==1 else 'DOWN'}")

    # ---------- 4. jalankan class pada data MT5 SUNGGUHAN ----------
    print("\n" + "=" * 96)
    print("4. KEPUTUSAN SLOT ETERNA PADA DATA MT5 LIVE (tanpa kirim order)")
    print("=" * 96)
    strat = EternaStrategy(spec, cfg, dp)
    r = strat.evaluate()
    print(f"  action    : {r.action}")
    print(f"  signal_id : {r.signal_id}")
    print(f"  sl / tp   : {r.sl} / {r.tp}")
    print(f"  lot/magic : {r.lot} / {r.magic}")
    ai = mt5.account_info()
    if r.action != "FLAT" and ai:
        risk = abs(float(h_mt5['close'].iloc[-1]) - r.sl) * LOT * CONTRACT
        print(f"  risiko    : ${risk:.2f} = {100*risk/ai.balance:.1f}% dari saldo ${ai.balance:.2f}")
    print("\n  (slot masih enabled:false di config -> brain tidak akan mengirim sinyal ini)")
    mt5.shutdown()


if __name__ == "__main__":
    main()
