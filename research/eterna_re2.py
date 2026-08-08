"""ETERNA fase-26: rekayasa balik tahap-2 — multiplier BESAR di M30/H1/H4/D1.

Fase-25 buntu karena hanya menyapu multiplier <= 3.8. Petunjuk baru: dua entry yang wajar
jatuh TEPAT di batas bar M30 dalam waktu server broker (UTC+3):
    19:30:15 UTC = 22:30 server  -> batas M30
    21:30:28 UTC = 00:30 server  -> batas M30
(entry 10:55:52 diabaikan: posisinya dibalik 2 DETIK kemudian = artefak restart EA,
 bukan sinyal sungguhan.)

Tapi M30 x1.2 = 999 trade/tahun, mustahil menahan posisi 6 hari. Jadi multiplier-nya
pasti jauh lebih besar. Di sini disapu 2..14.

Syarat yang harus dipenuhi bersamaan:
  1. flip BUY  di bar yang tutup tepat sebelum 29 Jul 19:30 UTC
  2. flip SELL di bar yang tutup tepat sebelum 04 Agu 21:30 UTC
  3. TIDAK ada flip di antara keduanya (posisi ditahan 6 hari tanpa terputus)

Jalankan: python research/eterna_re2.py
"""
import warnings
warnings.filterwarnings("ignore")

import datetime as dt
import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
BUY_AT  = pd.Timestamp("2026-07-29 19:30:15", tz="UTC")
SELL_AT = pd.Timestamp("2026-08-04 21:30:28", tz="UTC")
TFS = {"M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}
MULTS = [2.0, 2.5, 3.0, 3.8, 4.5, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 14.0]
PERIODS = [7, 10, 14, 20]


def load():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv WHERE ts >= '2026-04-01' ORDER BY ts").df()
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
    m = pd.concat([df, add[["open", "high", "low", "close"]][add.index > last]]).sort_index()
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
    print(f"data 1m {m1.index[0]} .. {m1.index[-1]}\n")
    print("=" * 112)
    print("Syarat: flip BUY sebelum 29 Jul 19:30, flip SELL sebelum 04 Agu 21:30,")
    print("        dan TIDAK ada flip di antaranya (posisi utuh 6 hari)")
    print("=" * 112)
    print(f"{'TF':>4} {'atr':>4} {'mult':>6} {'flip BUY':>17} {'flip SELL':>17} "
          f"{'flip di tengah':>15} {'skor':>6}")
    best = []
    for tf_l, tf in TFS.items():
        d = m1.resample(tf, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        step = pd.Timedelta(tf)
        for p in PERIODS:
            for mu in MULTS:
                st = supertrend(d, p, mu)
                fl = st[st != st.shift(1)]
                bu = fl[(fl == 1) & (fl.index + step <= BUY_AT) &
                        (fl.index + step >= BUY_AT - pd.Timedelta(hours=6))]
                se = fl[(fl == -1) & (fl.index + step <= SELL_AT) &
                        (fl.index + step >= SELL_AT - pd.Timedelta(hours=6))]
                if len(bu) == 0 or len(se) == 0:
                    continue
                b_ts, s_ts = bu.index[-1], se.index[-1]
                if s_ts <= b_ts:
                    continue
                mid = fl[(fl.index > b_ts) & (fl.index < s_ts)]
                score = 2 - min(len(mid), 2)
                best.append((score, tf_l, p, mu, b_ts, s_ts, len(mid)))
                print(f"{tf_l:>4} {p:>4} {mu:>6.1f} {b_ts:%m-%d %H:%M}"
                      f"{'':>3}{s_ts:%m-%d %H:%M}{'':>3}{len(mid):>15} {score:>6}")
    print("-" * 112)
    perfect = [x for x in best if x[6] == 0]
    if perfect:
        print(f"\nCOCOK SEMPURNA (0 flip di tengah): {len(perfect)} kombinasi")
        for _, tf, p, mu, b, s, _m in sorted(perfect, key=lambda z: (z[1], z[2], z[3])):
            print(f"  {tf} ATR {p} x{mu}  -> BUY {b:%d %b %H:%M}  SELL {s:%d %b %H:%M}")
    else:
        print("\nTIDAK ADA yang cocok sempurna. Kandidat terdekat:")
        for x in sorted(best, key=lambda z: z[6])[:8]:
            print(f"  {x[1]} ATR {x[2]} x{x[3]}  flip di tengah: {x[6]}")


if __name__ == "__main__":
    main()
