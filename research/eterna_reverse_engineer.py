"""ETERNA fase-25: REKAYASA BALIK setelan asli dari riwayat deal MT5 (magic 920641).

Riwayat akun demo mengungkap eterna yang BENAR-BENAR dijalankan user (28 Jul - 5 Agu 2026):
    28 Jul 10:55:52  BUY  IN   4048.97  eternabot_direct
    28 Jul 10:55:54  SELL OUT  4048.63  eternabot_revers    -0.34
    29 Jul 19:30:15  BUY  IN   4016.39  eternabot_direct
    04 Agu 21:30:28  SELL OUT  4084.98  eternabot_reverse  +68.59
    04 Agu 21:30:28  SELL IN   4084.98  eternabot_direct
    05 Agu 04:46:23  BUY  OUT  4094.98  [sl 4094.98]       -10.00

Sudah pasti dari bukti:
  - MODE_DIRECT  (keluar & masuk di detik DAN harga yang sama = always-in stop-and-reverse)
  - SL_MANUAL $10 tetap (entry 4084.98 -> SL 4094.98, rugi persis -10.00 @0.01 lot)
  - TANPA TP     (trade menang keluar lewat sinyal balik, bukan target)

Yang BELUM diketahui: TIMEFRAME dan MULTIPLIER entry.
Skrip ini mencarinya secara empiris: timeframe/multiplier mana yang menghasilkan flip
Supertrend TEPAT pada waktu-waktu di atas. Waktu broker = UTC+3, jadi dicek keduanya.

Jalankan: python research/eterna_reverse_engineer.py
"""
import warnings
warnings.filterwarnings("ignore")

import datetime as dt
import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"

# waktu flip nyata dari riwayat deal (UTC)
REAL_FLIPS = [
    (pd.Timestamp("2026-07-28 10:55:52", tz="UTC"), +1),   # BUY
    (pd.Timestamp("2026-07-28 10:55:54", tz="UTC"), -1),   # langsung balik
    (pd.Timestamp("2026-07-29 19:30:15", tz="UTC"), +1),   # BUY (ditahan 6 hari)
    (pd.Timestamp("2026-08-04 21:30:28", tz="UTC"), -1),   # balik ke SELL
]
TFS = {"M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h"}
MULTS = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 3.8]
PERIODS = [7, 10, 14, 20]


def load():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    import dukascopy_python
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD
    last = df.index[-1]
    add = dukascopy_python.fetch(
        INSTRUMENT_FX_METALS_XAU_USD, dukascopy_python.INTERVAL_MIN_1,
        dukascopy_python.OFFER_SIDE_BID,
        (last - pd.Timedelta(days=2)).to_pydatetime().replace(tzinfo=None),
        dt.datetime.utcnow())
    add.index = pd.to_datetime(add.index, utc=True)
    add = add[["open", "high", "low", "close"]]
    m = pd.concat([df, add[add.index > last]]).sort_index()
    return m[~m.index.duplicated(keep="first")]


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
    return pd.Series(d, index=df.index)


def main():
    m1 = load()
    print(f"data 1m s/d {m1.index[-1]}\n")
    win0 = pd.Timestamp("2026-07-27", tz="UTC")
    win1 = pd.Timestamp("2026-08-06", tz="UTC")

    print("=" * 110)
    print("Mencari timeframe + multiplier yang flip-nya COCOK dengan riwayat deal nyata")
    print("Toleransi: flip Supertrend terjadi di bar yang menutup <= 65 menit sebelum eksekusi")
    print("=" * 110)
    rows = []
    for tf_l, tf in TFS.items():
        d = m1.resample(tf, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        step = pd.Timedelta(tf)
        for p in PERIODS:
            for mu in MULTS:
                st = supertrend(d, p, mu)
                fl = st[st != st.shift(1)].loc[win0:win1]
                hits = 0
                detail = []
                for ts_real, dir_real in REAL_FLIPS:
                    # bar yang flip harus SUDAH TERTUTUP sebelum eksekusi
                    cand = fl[(fl.index + step <= ts_real) &
                              (fl.index + step >= ts_real - pd.Timedelta(minutes=65))]
                    match = [t for t, v in cand.items() if v == dir_real]
                    if match:
                        hits += 1
                        detail.append(f"{match[-1]:%m-%d %H:%M}")
                    else:
                        detail.append("-")
                if hits >= 2:
                    rows.append({"TF": tf_l, "atr": p, "mult": mu, "cocok": f"{hits}/4",
                                 "flip": " | ".join(detail), "n_flip_periode": len(fl)})
    if not rows:
        print("  tidak ada kombinasi yang cocok >=2 dari 4 flip")
    else:
        out = pd.DataFrame(rows).sort_values("cocok", ascending=False)
        print(out.to_string(index=False))

    # berapa sering flip di tiap TF selama 5,5 tahun (mode direct = tiap flip = 1 trade)
    print("\n" + "=" * 110)
    print("Sanity check: mode DIRECT berarti tiap flip = 1 trade. Berapa trade/tahun?")
    print("=" * 110)
    for tf_l, tf in TFS.items():
        d = m1.resample(tf, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        st = supertrend(d, 10, 1.2)
        n = int((st != st.shift(1)).sum())
        yrs = (d.index[-1] - d.index[0]).days / 365.25
        print(f"  {tf_l:>4} ATR10 x1.2 : {n:>7} flip = {n/yrs:>8.0f} trade/tahun")
    print("\n  Posisi user ditahan 6 HARI -> timeframe yang masuk akal hanya yang")
    print("  trade/tahun-nya kecil. M5/M15 mustahil menahan 6 hari.")


if __name__ == "__main__":
    main()
