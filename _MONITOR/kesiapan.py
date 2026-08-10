"""Diagnostik KESIAPAN tiap sleeve: apa yang sedang ditunggu, dan seberapa dekat.

Bukan alat untuk memaksa trade - ini menunjukkan kondisi entry mana yang belum terpenuhi,
supaya "belum ada trade" bisa dibedakan antara STRATEGI SEDANG MENUNGGU (normal) dan
SISTEM RUSAK (harus diperbaiki).

Jalankan: python C:\\Quant\\_MONITOR\\kesiapan.py
"""
import datetime as dt
import sys

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, r"C:\Quant")
import MetaTrader5 as mt5
from pipeline.fetch.base_fetcher import load_config


def h1_from_mt5(sym, n=6000):
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, n)
    df = pd.DataFrame(r)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("ts")[["open", "high", "low", "close"]]
    # MT5 memberi waktu SERVER (FBS = UTC+3). Geser ke UTC supaya jendela sesi benar.
    tick = mt5.symbol_info_tick(sym)
    off = round((pd.Timestamp(tick.time, unit="s", tz="UTC") - pd.Timestamp.now("UTC")).total_seconds() / 3600)
    if off:
        df.index = df.index - pd.Timedelta(hours=off)
    return df, off


def atr_s(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr_s(df, period)
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
    return pd.Series(d, index=df.index), pd.Series(fu, index=df.index), pd.Series(fl, index=df.index)


def main():
    if not mt5.initialize():
        print("MT5 init gagal:", mt5.last_error()); return
    now = dt.datetime.utcnow()
    print("=" * 84)
    print(f"KESIAPAN SLEEVE  -  {now:%Y-%m-%d %H:%M} UTC")
    print("=" * 84)

    # ---------- brain ----------
    try:
        h = requests.get("http://127.0.0.1:8000/health", timeout=15).json()
        print(f"brain: UP {h['uptime_seconds']//60} menit, {len(h['strategies'])} slot")
    except Exception as e:
        print("brain: TIDAK MENJAWAB ->", e)

    # ---------- XAU: eterna & zrev ----------
    xau, off = h1_from_mt5("XAUUSD")
    print(f"offset server MT5 -> UTC: {off:+d} jam   (bar M1 terakhir {xau.index[-1]:%H:%M} UTC)")
    hh = xau.resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    cur = pd.Timestamp.now("UTC").floor("1h")
    hc = hh.iloc[:-1] if hh.index[-1] == cur else hh
    px = float(hc["close"].iloc[-1])

    print("\n--- ETERNA (H1, ATR16 x1.8, gate x3.8, TP 1:4) ---")
    se, fu_e, fl_e = supertrend(hc, 16, 1.8)
    st, _, _ = supertrend(hc, 16, 3.8)
    arah_e = "UP" if se.iloc[-1] == 1 else "DOWN"
    arah_t = "UP" if st.iloc[-1] == 1 else "DOWN"
    flip = se.iloc[-1] != se.iloc[-2]
    lvl = fu_e.iloc[-1] if se.iloc[-1] == 1 else fl_e.iloc[-1]
    print(f"  harga {px:.2f} | ST-entry {arah_e} | ST-tren {arah_t} | selaras: {arah_e == arah_t}")
    print(f"  flip di bar tertutup terakhir: {flip}")
    if not np.isnan(lvl):
        print(f"  garis Supertrend entry di {lvl:.2f} -> butuh gerak "
              f"{abs(px - lvl):.2f} ({abs(px-lvl)/px*100:.2f}%) untuk FLIP")
    print(f"  MENUNGGU: flip ST-entry YANG SEARAH ST-tren. Tanpa flip -> tetap FLAT.")

    print("\n--- ZREV (Donchian20 H1 + gate EMA100 & SMA50 harian) ---")
    n = 20
    dh = hc["high"].rolling(n).max().shift(1).iloc[-1]
    dl = hc["low"].rolling(n).min().shift(1).iloc[-1]
    ema = hc["close"].ewm(span=100, adjust=False).mean().iloc[-1]
    dly = hc["close"].resample("1D").last().dropna()
    sma50 = dly.rolling(50).mean().iloc[-1] if len(dly) >= 50 else float("nan")
    print(f"  Donchian atas {dh:.2f} (butuh +{dh-px:.2f}) | bawah {dl:.2f} (butuh {dl-px:.2f})")
    print(f"  EMA100 {ema:.2f} | SMA50-harian {sma50:.2f}")
    gate_up = px > ema and px > sma50
    gate_dn = px < ema and px < sma50
    print(f"  gate NAIK: {gate_up} | gate TURUN: {gate_dn}")
    print(f"  MENUNGGU: close menembus Donchian DAN searah kedua gate.")

    # ---------- NAS: ORB ----------
    print("\n--- ORB (NAS100/US100, range 30m sesi NY) ---")
    ny_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
    if now < ny_open:
        sisa = (ny_open - now).total_seconds() / 60
        print(f"  Sesi NY buka 13:30 UTC -> {sisa:.0f} menit lagi.")
        print(f"  Range terbentuk 13:30-14:00, pending STOP dipasang setelah itu.")
    else:
        nas, _ = h1_from_mt5("US100", 3000)
        seg = nas[(nas.index >= ny_open) & (nas.index < ny_open + dt.timedelta(minutes=30))]
        if len(seg):
            print(f"  Range hari ini: {seg['low'].min():.2f} .. {seg['high'].max():.2f} "
                  f"(lebar {seg['high'].max()-seg['low'].min():.2f})")
        else:
            print("  Belum ada bar di jendela range.")
    print("  MENUNGGU: harga menembus batas range di sisi yang searah SMA50 harian.")

    # ---------- RSI2 ----------
    print("\n--- RSI2 (US100 harian, mean-reversion long-only) ---")
    r = mt5.copy_rates_from_pos("US100", mt5.TIMEFRAME_D1, 0, 400)
    c = pd.Series([x["close"] for x in r])
    d = c.diff()
    up_ = d.clip(lower=0).rolling(2).mean(); dn_ = (-d.clip(upper=0)).rolling(2).mean()
    rsi = (100 - 100 / (1 + up_ / dn_.replace(0, np.nan))).iloc[-1]
    s200 = c.rolling(200).mean().iloc[-1]; s5 = c.rolling(5).mean().iloc[-1]
    print(f"  RSI(2) = {rsi:.1f}  (butuh < 10)")
    print(f"  close {c.iloc[-1]:.1f} vs SMA200 {s200:.1f} -> tren naik: {c.iloc[-1] > s200}")
    print(f"  MENUNGGU: RSI(2) jatuh di bawah 10 SAAT harga masih di atas SMA200.")

    # ---------- posisi ----------
    pos = mt5.positions_get() or []
    print("\n" + "=" * 84)
    print(f"POSISI TERBUKA: {len(pos)}")
    for p in pos:
        print(f"  {p.symbol} magic={p.magic} {'BUY' if p.type==0 else 'SELL'} "
              f"profit={p.profit:+.2f}")
    ai = mt5.account_info()
    print(f"balance {ai.balance:.2f}  equity {ai.equity:.2f}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
