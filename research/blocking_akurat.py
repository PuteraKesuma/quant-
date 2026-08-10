"""FASE-34: simulasi _book_conflict di LEVEL TRADE (menggantikan proxy bulanan yang cacat).

KESALAHAN YANG DIPERBAIKI: fase-32/33 memperkirakan pemblokiran dengan menolkan eterna
di bulan-bulan yang PnL-nya SEARAH dengan zrev. Itu proxy buruk - dua strategi bisa
sama-sama untung sebulan sambil memegang arah BERLAWANAN di waktu yang berbeda. Hasilnya
("eterna diblokir 5 dari 6 bulan") hampir pasti melebih-lebihkan.

Yang benar: _book_conflict bekerja per-ENTRY, membandingkan arah posisi yang SEDANG
terbuka pada DETIK entry itu. Skrip ini menyimulasikannya persis:
  - bangun daftar posisi zrev (waktu masuk, waktu keluar, arah)
  - untuk tiap entry eterna, cek apakah ada posisi zrev yang SEDANG terbuka DAN searah
  - kalau ya -> entry eterna itu dibuang (persis yang terjadi live)

Asimetri yang disimulasikan (dikonfirmasi dari kode):
  signal.py:790  ZRevStrategy  memanggil _book_conflict -> tapi eterna (920627) TIDAK ada
                 di governor.magics, jadi zrev MENGABAIKAN eterna
  signal.py:1583 EternaStrategy memanggil _book_conflict -> zrev (920622) ADA di book,
                 jadi eterna DIBLOKIR zrev
  Akibatnya: zrev selalu menang, eterna selalu mengalah.

Jalankan: python research/blocking_akurat.py
"""
import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))

RAW = r"C:\Quant\data\Level_0_Raw"
CAPITAL, COST, LOT = 1000.0, 0.50, 0.01
MIN_SL = 0.30


def load_h1():
    con = duckdb.connect(rf"{RAW}\XAUUSD_1m.duckdb", read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


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


def eterna_trades(h):
    """Kembalikan DAFTAR TRADE lengkap: masuk, keluar, arah, pnl."""
    P, ME, MT, TPR = 16, 1.8, 3.8, 4.0
    se, st = supertrend(h, P, ME), supertrend(h, P, MT)
    sd = se.where(se != se.shift(1)).shift(1).to_numpy(); td = st.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()
    pos = 0; entry = sl = tp = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = (sl if lo[i] <= sl else (tp if hi[i] >= tp else None)) if pos == 1 \
                  else (sl if hi[i] >= sl else (tp if lo[i] <= tp else None))
            if hit is not None:
                out.append((h.index[ei], h.index[i], pos, entry, hit)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], h.index[i], pos, entry, o[i])); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue
        pos, entry, ei = s, o[i], i
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + TPR * dist if s == 1 else o[i] - TPR * dist
    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.arah * LOT * 100 - COST
    return t


def zrev_trades(h):
    """Donchian20 always-in + gate EMA100 & SMA50-harian, ATR stop 3.0 - dengan waktu keluar."""
    N, ATRM = 20, 3.0
    hh = h["high"].rolling(N).max().shift(1)
    ll = h["low"].rolling(N).min().shift(1)
    ema = h["close"].ewm(span=100, adjust=False).mean().shift(1)
    dly = h["close"].resample("1D").last().dropna()
    smap = {ts.date(): v for ts, v in dly.rolling(50).mean().shift(1).items()}
    sma = pd.Series([smap.get(d, np.nan) for d in h.index.date], index=h.index)
    a = atr_s(h, 14).shift(1)
    o, hi, lo, c = (h["open"].to_numpy(), h["high"].to_numpy(),
                    h["low"].to_numpy(), h["close"].to_numpy())
    HH, LL, EM, SM, AT = (hh.to_numpy(), ll.to_numpy(), ema.to_numpy(),
                          sma.to_numpy(), a.to_numpy())
    pos = 0; entry = sl = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = (sl if lo[i] <= sl else None) if pos == 1 else (sl if hi[i] >= sl else None)
            if hit is not None:
                out.append((h.index[ei], h.index[i], pos, entry, hit)); pos = 0
        if np.isnan(HH[i]) or np.isnan(EM[i]) or np.isnan(SM[i]) or np.isnan(AT[i]):
            continue
        px = c[i-1]
        up = (px > EM[i]) and (px > SM[i])
        dn = (px < EM[i]) and (px < SM[i])
        sig = 1 if (px >= HH[i-1] and up) else (-1 if (px <= LL[i-1] and dn) else 0)
        if sig == 0:
            continue
        if pos == -sig:
            out.append((h.index[ei], h.index[i], pos, entry, o[i])); pos = 0
        if pos == 0:
            pos, entry, ei = sig, o[i], i
            sl = o[i] - ATRM * AT[i] if sig == 1 else o[i] + ATRM * AT[i]
    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.arah * LOT * 100 - COST
    return t


def main():
    h = load_h1()
    et = eterna_trades(h)
    zt = zrev_trades(h)
    print(f"eterna : {len(et)} trade, net ${et.pnl.sum():+.0f}")
    print(f"zrev   : {len(zt)} trade, net ${zt.pnl.sum():+.0f}")

    # ---- simulasi pemblokiran PER-ENTRY ----
    blocked = []
    z = zt.sort_values("masuk").reset_index(drop=True)
    for r in et.itertuples():
        # posisi zrev yang SEDANG terbuka tepat saat eterna mau masuk
        open_now = z[(z.masuk <= r.masuk) & (z.keluar > r.masuk)]
        same_dir = open_now[open_now.arah == r.arah]
        blocked.append(len(same_dir) > 0)
    et["diblokir"] = blocked
    nb = int(et.diblokir.sum())

    print("\n" + "=" * 92)
    print("HASIL SIMULASI PER-TRADE (yang benar)")
    print("=" * 92)
    print(f"  entry eterna DIBLOKIR : {nb} dari {len(et)}  ({100*nb/len(et):.1f}%)")
    print(f"  entry eterna LOLOS    : {len(et)-nb}")
    lolos = et[~et.diblokir]
    print(f"\n  net eterna TANPA pemblokiran : ${et.pnl.sum():+.0f}")
    print(f"  net eterna SETELAH diblokir  : ${lolos.pnl.sum():+.0f}  "
          f"({100*lolos.pnl.sum()/et.pnl.sum():.0f}% dari aslinya)")

    print("\n  PERBANDINGAN dengan proxy bulanan yang cacat (fase-32/33):")
    print("    proxy bulanan bilang: eterna diblokir 5 dari 6 bulan 2026 (~83%)")
    print(f"    simulasi per-trade  : eterna diblokir {100*nb/len(et):.1f}% dari SEMUA entry")

    # ---- 2026 saja ----
    s26 = pd.Timestamp("2026-01-01", tz="UTC")
    e26 = et[et.masuk >= s26]
    l26 = e26[~e26.diblokir]
    print("\n" + "=" * 92)
    print("KHUSUS 2026")
    print("=" * 92)
    print(f"  entry eterna 2026 : {len(e26)}   diblokir {int(e26.diblokir.sum())} "
          f"({100*e26.diblokir.mean():.0f}%)")
    print(f"  net tanpa blok    : ${e26.pnl.sum():+.2f}")
    print(f"  net setelah blok  : ${l26.pnl.sum():+.2f}")
    m = l26.set_index("masuk").pnl.resample("ME").sum()
    mall = e26.set_index("masuk").pnl.resample("ME").sum()
    print(f"\n  {'Bulan':<10}{'tanpa blok':>13}{'setelah blok':>15}{'entry':>8}{'diblokir':>10}")
    for ts in mall.index:
        cnt = int((e26.masuk.dt.to_period('M') == ts.to_period('M')).sum())
        blk = int(e26[(e26.masuk.dt.to_period('M') == ts.to_period('M'))].diblokir.sum())
        print(f"  {ts:%b %Y}{'':<3}{mall.get(ts,0):>13.2f}{m.get(ts,0):>15.2f}{cnt:>8}{blk:>10}")

    et.to_csv(r"C:\Quant\_MONITOR\eterna_blocking.csv", index=False)
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_blocking.csv")


if __name__ == "__main__":
    main()
