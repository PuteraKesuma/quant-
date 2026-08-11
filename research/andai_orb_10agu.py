"""Andai sistem TIDAK mati: apakah ORB akan trade 10 Agustus 2026?

LATAR: rantai trading mati 2026-08-10 10:19 UTC sampai 2026-08-11 01:47 UTC (~15 jam).
Sesi NY ORB jam 13:30 UTC jatuh PERSIS di dalam jendela mati itu. Pertanyaan yang harus
dijawab jujur: kita kehilangan trade, atau memang tidak ada sinyal?

"Tidak ada trade di riwayat" TIDAK menjawabnya - dua sebab yang sangat berbeda
menghasilkan riwayat kosong yang identik.

Skrip ini memakai aturan yang SAMA dengan pipeline/live/orb_stop_manager.py:
  - gate tren harian: sign(close harian terakhir - SMA50), 0 memblokir sesi (fail-safe)
  - opening range 30 menit dari NY cash open (DST-aware: 13:30 UTC musim panas)
  - breakout PERTAMA setelah range, high dicek sebelum low per bar
  - hanya searah gate tren
  - SL/TP = lebar range (RR 1:1), breakeven di +0.5R

Jalankan: python research/andai_orb_10agu.py
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import pandas as pd

# Simbol BROKER, bukan nama internal. Di FBS-Demo Nasdaq = "US100"; "NAS100" adalah
# nama slot di config.yaml dan copy_rates_* akan mengembalikan None kalau dipakai.
SYMBOL = "US100"
TANGGAL = dt.date(2026, 8, 10)
RANGE_MENIT = 30
TREND_SMA = 50
BUF = 0.0


def open_utc(hari: dt.date) -> tuple[int, int]:
    """NY cash open dalam UTC, sadar DST - meniru _open_time() di manager."""
    et = dt.datetime(hari.year, hari.month, hari.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return (13, 30) if et.dst() != dt.timedelta(0) else (14, 30)


def main() -> None:
    if not mt5.initialize():
        raise SystemExit(f"MT5 gagal: {mt5.last_error()}")

    # ---- gate tren harian (pakai bar harian yang SUDAH selesai) ----
    d1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_D1, 0, TREND_SMA + 10)
    dfd = pd.DataFrame(d1)
    dfd["ts"] = pd.to_datetime(dfd["time"], unit="s", utc=True)
    selesai = dfd[dfd["ts"].dt.date < TANGGAL]           # hanya hari yang sudah tutup
    sma = float(selesai["close"].tail(TREND_SMA).mean())
    last = float(selesai["close"].iloc[-1])
    arah = 1 if last > sma else (-1 if last < sma else 0)
    print(f"gate tren : close {last:.2f} vs SMA{TREND_SMA} {sma:.2f}  ->  "
          f"{'NAIK (hanya BUY)' if arah == 1 else 'TURUN (hanya SELL)' if arah == -1 else 'NETRAL (sesi diblokir)'}")

    if arah == 0:
        print("\nVONIS: sesi diblokir gate tren. Tidak ada trade, mati atau hidup.")
        mt5.shutdown()
        return

    # ---- bar M1 hari itu ----
    h, m = open_utc(TANGGAL)
    mulai = dt.datetime(TANGGAL.year, TANGGAL.month, TANGGAL.day, h, m, tzinfo=dt.timezone.utc)
    akhir_range = mulai + dt.timedelta(minutes=RANGE_MENIT)
    tutup = dt.datetime(TANGGAL.year, TANGGAL.month, TANGGAL.day, 20, 0, tzinfo=dt.timezone.utc)
    print(f"NY open   : {mulai:%H:%M} UTC   range sampai {akhir_range:%H:%M} UTC")

    # copy_rates_range rusak di FBS -> ambil dari posisi lalu saring (catatan lama proyek ini)
    bars = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 60 * 24 * 6)
    if bars is None:
        raise SystemExit(f"tidak ada bar M1: {mt5.last_error()}")
    df = pd.DataFrame(bars)
    off = int(mt5.symbol_info_tick(SYMBOL).time - dt.datetime.now(dt.timezone.utc).timestamp())
    df["ts"] = pd.to_datetime(df["time"] - round(off / 3600) * 3600, unit="s", utc=True)
    df = df.set_index("ts").sort_index()

    rng = df[(df.index >= mulai) & (df.index < akhir_range)]
    if rng.empty:
        print("\nTIDAK BISA DIJAWAB: bar M1 untuk jendela itu sudah tidak ada di MT5.")
        mt5.shutdown()
        return

    hi, lo = float(rng["high"].max()), float(rng["low"].min())
    lebar = hi - lo
    print(f"range     : high {hi:.2f}  low {lo:.2f}  lebar {lebar:.2f}")

    post = df[(df.index >= akhir_range) & (df.index < tutup)]
    sisi, ts = None, None
    for t, bar in post.iterrows():
        if bar["high"] > hi + BUF:
            sisi, ts = "long", t
            break
        if bar["low"] < lo - BUF:
            sisi, ts = "short", t
            break

    if sisi is None:
        print("\nVONIS: TIDAK ADA breakout sampai 20:00 UTC. Sistem hidup pun, nol trade.")
        mt5.shutdown()
        return

    print(f"breakout  : {sisi.upper()} pertama jam {ts:%H:%M} UTC")
    if (sisi == "long" and arah != 1) or (sisi == "short" and arah != -1):
        print("\nVONIS: breakout MELAWAN gate tren -> manager menolak. Nol trade, mati atau hidup.")
        mt5.shutdown()
        return

    entry = hi if sisi == "long" else lo
    sl = entry - lebar if sisi == "long" else entry + lebar
    tp = entry + lebar if sisi == "long" else entry - lebar
    print(f"entry     : {entry:.2f}   SL {sl:.2f}   TP {tp:.2f}   (RR 1:1, BE di +0.5R)")

    # ---- hasilnya bagaimana? SL dicek sebelum TP, breakeven bersenjata di +0.5R ----
    risiko = abs(entry - sl)
    armed = False
    hasil, hasil_ts, hasil_px = "masih terbuka di 20:00", None, float(post["close"].iloc[-1])
    for t, bar in post[post.index >= ts].iterrows():
        if sisi == "long":
            if not armed and (bar["high"] - entry) >= 0.5 * risiko:
                armed = True
            if armed and bar["low"] <= entry:
                hasil, hasil_ts, hasil_px = "breakeven", t, entry
                break
            if bar["low"] <= sl:
                hasil, hasil_ts, hasil_px = "kena SL", t, sl
                break
            if bar["high"] >= tp:
                hasil, hasil_ts, hasil_px = "kena TP", t, tp
                break
        else:
            if not armed and (entry - bar["low"]) >= 0.5 * risiko:
                armed = True
            if armed and bar["high"] >= entry:
                hasil, hasil_ts, hasil_px = "breakeven", t, entry
                break
            if bar["high"] >= sl:
                hasil, hasil_ts, hasil_px = "kena SL", t, sl
                break
            if bar["low"] <= tp:
                hasil, hasil_ts, hasil_px = "kena TP", t, tp
                break

    poin = (hasil_px - entry) if sisi == "long" else (entry - hasil_px)
    info = mt5.symbol_info(SYMBOL)
    usd = poin * 0.03 * info.trade_contract_size * (info.trade_tick_value / info.trade_tick_size / info.trade_contract_size) \
        if info.trade_tick_size else poin * 0.03

    print(f"hasil     : {hasil}" + (f" jam {hasil_ts:%H:%M} UTC" if hasil_ts is not None else ""))
    print(f"\nVONIS: ORB AKAN trade dan kita MELEWATKANNYA.")
    print(f"       {sisi.upper()} @ {entry:.2f} -> {hasil} @ {hasil_px:.2f}")
    print(f"       {poin:+.2f} poin, sekitar {usd:+.2f} USD pada lot 0.03")

    mt5.shutdown()


if __name__ == "__main__":
    main()
