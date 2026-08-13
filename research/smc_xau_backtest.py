"""SMC (Smart Money Concepts) di XAUUSD — uji apakah konsepnya punya edge nyata.

LATAR:
User minta sleeve baru berbasis SMC + follow-the-trend, dengan limit order di zona
Order Block dan ada expiry. Bagian "limit order + expiry" sudah dipastikan didukung
FBS (expiration_mode=15, stops_level=0). Yang BELUM dipastikan: apakah aturan SMC-nya
sendiri menghasilkan uang. Itu yang diuji di sini.

SMC ditulis sebagai aturan MEKANIS, bukan interpretasi gambar. Empat komponen:

  swing pivot  : high[i] tertinggi dalam jendela +-k bar.
                 ANTI-LOOKAHEAD: pivot di bar i baru BOLEH DIPAKAI dari bar i+k,
                 karena sebelum itu kita belum tahu k bar ke kanan.
  BOS          : close menembus swing high (bullish) / swing low (bearish) yang
                 sudah terkonfirmasi. Ini yang mendefinisikan "tren" versi SMC.
  Order Block  : candle berlawanan arah TERAKHIR sebelum impulse yang bikin BOS.
                 Zona = [low, high] candle itu.
  FVG          : celah harga; bullish kalau low[i+1] > high[i-1].
  sweep        : wick menembus swing sebelumnya lalu close balik ke dalam.

EKSEKUSI YANG DIMODELKAN PERSIS SEPERTI RENCANA LIVE:
  setelah BOS -> pasang BUY LIMIT di ujung atas zona OB (harga harus retrace turun
  ke zona; ini "mitigation entry" standar). SL di bawah low OB dikurangi buffer.
  TP = R x jarak SL. Order KEDALUWARSA setelah `expiry` bar kalau tidak kena.
  Order pending dibatalkan juga kalau BOS berlawanan muncul duluan.

BIAYA NYATA (pelajaran mahal dari sesi sebelumnya - jangan diulang):
  spread $0.50 per trade pada 0.01 lot
  swap LONG -$0.6995/malam, SHORT +$0.2491/malam, Rabu 3x
  slippage limit order = 0 (limit terisi di harga limit atau lebih baik - ini
  justru keunggulan limit order dibanding market order)

4 KONFIGURASI DIUJI (permintaan user - dilaporkan APA ADANYA, tanpa cherry-pick):
  A. OB + BOS            (inti, paling sedikit parameter)
  B. A + FVG
  C. A + liquidity sweep
  D. A + FVG + sweep     (semua)
x 4 timeframe = 16 kombinasi. JUMLAH TRIAL INI DILAPORKAN dan dipakai untuk
Deflated Sharpe - 16 trial menaikkan ambang Sharpe yang dibutuhkan secara signifikan.

Jalankan: python research/smc_xau_backtest.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))

LOT, SPREAD = 0.01, 0.50
SWAP_LONG, SWAP_SHORT = -0.6995, 0.2491
CAPITAL = 521.88


# ---------------------------------------------------------------- data

def load_m1() -> pd.DataFrame:
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / "XAUUSD_1m.duckdb"), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def tf(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    return m1.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def malam(masuk: pd.Timestamp, keluar: pd.Timestamp) -> float:
    """Jumlah unit swap antara masuk dan keluar. Rabu 3x, akhir pekan tidak ada."""
    tot, d = 0.0, masuk.normalize()
    while d < keluar.normalize():
        d += pd.Timedelta(days=1)
        if d > keluar.normalize():
            break
        wd = d.weekday()
        if wd in (5, 6):
            continue
        tot += 3.0 if wd == 2 else 1.0
    return tot


# ---------------------------------------------------------------- primitif SMC

def swing_pivots(h: pd.DataFrame, k: int):
    """Swing high/low fractal. Mengembalikan (level, bar_konfirmasi) per pivot.

    ANTI-LOOKAHEAD: pivot di bar i butuh k bar di kanannya, jadi baru diketahui
    di bar i+k. Yang dikembalikan adalah indeks KONFIRMASI, bukan indeks pivot.
    """
    hi, lo = h["high"].to_numpy(), h["low"].to_numpy()
    n = len(h)
    sh, sl = [], []          # (idx_pivot, level, idx_konfirmasi)
    for i in range(k, n - k):
        w_hi = hi[i - k:i + k + 1]
        w_lo = lo[i - k:i + k + 1]
        if hi[i] == w_hi.max() and (w_hi.argmax() == k):
            sh.append((i, hi[i], i + k))
        if lo[i] == w_lo.min() and (w_lo.argmin() == k):
            sl.append((i, lo[i], i + k))
    return sh, sl


def cari_ob(h: pd.DataFrame, j: int, arah: int, maks: int):
    """Order Block = candle berlawanan arah terakhir sebelum impulse ke bar j.

    Untuk BOS bullish (arah=+1): cari mundur dari j-1 candle bearish terakhir
    (close < open). Zonanya [low, high] candle itu.
    """
    o = h["open"].to_numpy(); c = h["close"].to_numpy()
    hi = h["high"].to_numpy(); lo = h["low"].to_numpy()
    awal = max(0, j - maks)
    for i in range(j - 1, awal - 1, -1):
        bearish = c[i] < o[i]
        if (arah == 1 and bearish) or (arah == -1 and not bearish):
            return i, lo[i], hi[i]
    return None


def ada_fvg(h: pd.DataFrame, i0: int, i1: int, arah: int) -> bool:
    """FVG di dalam leg impulse antara bar OB (i0) dan bar BOS (i1).

    bullish FVG: low[t+1] > high[t-1]  (celah ke atas)
    bearish FVG: high[t+1] < low[t-1]
    """
    hi, lo = h["high"].to_numpy(), h["low"].to_numpy()
    for t in range(max(i0 + 1, 1), min(i1, len(h) - 1)):
        if arah == 1 and lo[t + 1] > hi[t - 1]:
            return True
        if arah == -1 and hi[t + 1] < lo[t - 1]:
            return True
    return False


def level_terkonfirmasi(pivots, n: int) -> np.ndarray:
    """Array level pivot terakhir YANG SUDAH TERKONFIRMASI di tiap bar (nan kalau belum).

    Dihitung sekali di depan supaya loop utama tidak jadi O(n^2). Tetap
    anti-lookahead: level pivot i baru muncul di posisi i+k ke atas.
    """
    out = np.full(n, np.nan)
    p = 0
    cur = np.nan
    for j in range(n):
        while p < len(pivots) and pivots[p][2] <= j:
            cur = pivots[p][1]; p += 1
        out[j] = cur
    return out


def ada_sweep(hi, lo, c, lvl_lawan: np.ndarray, i_ob: int, arah: int, jendela: int) -> bool:
    """Liquidity sweep: sekitar bar OB, wick menembus pivot lawan lalu close balik.

    Untuk setup bullish, kita mau melihat sweep ke BAWAH swing low sebelumnya
    (ambil likuiditas stop) sebelum harga berbalik naik.
    """
    for t in range(max(0, i_ob - jendela), i_ob + 1):
        ref = lvl_lawan[t]
        if ref != ref:                      # nan: belum ada pivot terkonfirmasi
            continue
        if arah == 1 and lo[t] < ref and c[t] > ref:
            return True
        if arah == -1 and hi[t] > ref and c[t] < ref:
            return True
    return False


# ---------------------------------------------------------------- mesin

def jalankan(h: pd.DataFrame, *, k=3, ob_lookback=10, expiry=12, rr=2.0,
             buffer_frac=0.10, pakai_fvg=False, pakai_sweep=False,
             sweep_window=5) -> pd.DataFrame:
    """Satu posisi pada satu waktu. Limit order dengan expiry, persis rencana live."""
    sh, sl_piv = swing_pivots(h, k)
    o = h["open"].to_numpy(); c = h["close"].to_numpy()
    hi = h["high"].to_numpy(); lo = h["low"].to_numpy()
    idx = h.index
    n = len(h)
    lvl_sl = level_terkonfirmasi(sl_piv, n)   # untuk sweep setup bullish
    lvl_sh = level_terkonfirmasi(sh, n)       # untuk sweep setup bearish

    trades = []
    pos = 0                     # 0 datar, +1 long, -1 short
    entry = sl = tp = 0.0; ei = 0
    pend = None                 # (arah, harga_limit, sl, tp, bar_kedaluwarsa)
    i_sh = i_sl = 0
    last_sh = last_sl = None    # level pivot terakhir YANG SUDAH TERKONFIRMASI
    # BOS adalah PERISTIWA, bukan keadaan: satu level hanya boleh ditembus sekali.
    # Tanpa penanda ini, tiap bar setelah penembusan lolos syarat lagi dan
    # jumlah trade menggelembung palsu.
    sh_ditembus = sl_ditembus = False

    for j in range(1, n):
        # --- perbarui pivot yang sudah terkonfirmasi di bar j (bukan setelahnya)
        while i_sh < len(sh) and sh[i_sh][2] <= j:
            last_sh = sh[i_sh][1]; i_sh += 1; sh_ditembus = False
        while i_sl < len(sl_piv) and sl_piv[i_sl][2] <= j:
            last_sl = sl_piv[i_sl][1]; i_sl += 1; sl_ditembus = False

        # --- kelola posisi terbuka lebih dulu (SL diprioritaskan = konservatif)
        if pos != 0:
            if pos == 1:
                if lo[j] <= sl:
                    trades.append((idx[ei], idx[j], 1, entry, sl, "SL")); pos = 0
                elif hi[j] >= tp:
                    trades.append((idx[ei], idx[j], 1, entry, tp, "TP")); pos = 0
            else:
                if hi[j] >= sl:
                    trades.append((idx[ei], idx[j], -1, entry, sl, "SL")); pos = 0
                elif lo[j] <= tp:
                    trades.append((idx[ei], idx[j], -1, entry, tp, "TP")); pos = 0

        # --- pending limit: cek terisi / kedaluwarsa
        if pend is not None and pos == 0:
            arah, px, s, t, exp_bar = pend
            kena = (lo[j] <= px) if arah == 1 else (hi[j] >= px)
            if kena:
                pos, entry, ei = arah, px, j
                sl, tp = s, t
                pend = None
            elif j >= exp_bar:
                pend = None                      # KEDALUWARSA, tidak jadi trade
        elif pend is not None and pos != 0:
            pend = None                          # sudah ada posisi, batalkan

        # --- deteksi BOS di bar j (pakai pivot yang terkonfirmasi SEBELUM j).
        # Penanda ditandai di sini APA PUN yang terjadi setelahnya, supaya
        # penembusan yang sama tidak dipanen berkali-kali walau setup ditolak.
        arah = 0
        if last_sh is not None and not sh_ditembus and c[j] > last_sh:
            arah = 1; sh_ditembus = True
        elif last_sl is not None and not sl_ditembus and c[j] < last_sl:
            arah = -1; sl_ditembus = True
        if arah == 0:
            continue
        if pos != 0 or pend is not None:
            continue

        ob = cari_ob(h, j, arah, ob_lookback)
        if ob is None:
            continue
        i_ob, ob_lo, ob_hi = ob
        if ob_hi <= ob_lo:
            continue

        if pakai_fvg and not ada_fvg(h, i_ob, j, arah):
            continue
        if pakai_sweep:
            lawan = lvl_sl if arah == 1 else lvl_sh
            if not ada_sweep(hi, lo, c, lawan, i_ob, arah, sweep_window):
                continue

        tinggi = ob_hi - ob_lo
        buf = tinggi * buffer_frac
        if arah == 1:
            px = ob_hi                       # mitigation entry di ujung atas zona
            s = ob_lo - buf
            if px <= s:
                continue
            t = px + rr * (px - s)
            if px >= c[j]:                   # zona harus DI BAWAH harga sekarang
                continue
        else:
            px = ob_lo
            s = ob_hi + buf
            if px >= s:
                continue
            t = px - rr * (s - px)
            if px <= c[j]:
                continue

        pend = (arah, px, s, t, j + expiry)

    t = pd.DataFrame(trades, columns=["masuk", "keluar", "arah", "px_in", "px_out", "sebab"])
    if len(t) == 0:
        return t
    t["kotor"] = (t.px_out - t.px_in) * t.arah * LOT * 100
    t["malam"] = [malam(a, b) for a, b in zip(t.masuk, t.keluar)]
    t["swap"] = np.where(t.arah == 1, t.malam * SWAP_LONG, t.malam * SWAP_SHORT)
    t["pnl"] = t.kotor - SPREAD + t.swap
    return t


# ---------------------------------------------------------------- metrik

def dsr(sharpe: float, n_obs: int, n_trial: int, skew: float, kurt: float) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado). Menghukum jumlah trial."""
    if n_obs < 10 or n_trial < 1:
        return float("nan")
    e = 0.5772156649
    sr0 = np.sqrt(1.0 / n_obs) * (
        (1 - e) * stats.norm.ppf(1 - 1.0 / n_trial)
        + e * stats.norm.ppf(1 - 1.0 / (n_trial * np.e)))
    den = np.sqrt(max(1e-12, 1 - skew * sharpe + (kurt - 1) / 4.0 * sharpe ** 2))
    return float(stats.norm.cdf((sharpe - sr0) * np.sqrt(n_obs - 1) / den))


def ringkas(t: pd.DataFrame, label: str, n_trial: int) -> dict:
    kosong = {"konfigurasi": label, "n": len(t), "net$": 0.0, "PF": 0.0,
              "winrate%": 0, "maxDD%": 0.0, "thn+": "-", "impas%": 0.0,
              "margin": 0.0, "DSR": 0.0}
    if len(t) < 20:
        return kosong
    d = t.set_index("masuk").pnl
    eq = CAPITAL + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    thn = d.groupby(d.index.year).sum()
    menang, kalah = d[d > 0], d[d < 0]
    impas = (100 * abs(kalah.mean()) / (menang.mean() + abs(kalah.mean()))
             if len(menang) and len(kalah) else 0.0)
    wr = 100 * (d > 0).mean()

    mo = d.resample("ME").sum()
    mo = mo.loc[mo.index >= d.index.min()]
    sr = (mo.mean() / mo.std(ddof=1) * np.sqrt(12)) if mo.std(ddof=1) > 0 else 0.0
    p = dsr(sr / np.sqrt(12), len(mo), n_trial, float(mo.skew()), float(mo.kurt()) + 3.0)

    return {"konfigurasi": label, "n": len(d), "net$": round(d.sum(), 2),
            "PF": round((w / l) if l > 0 else 99, 2), "winrate%": round(wr),
            "maxDD%": round(100 * dd, 1),
            "thn+": f"{int((thn > 0).sum())}/{len(thn)}",
            "impas%": round(impas, 1), "margin": round(wr - impas, 1),
            "DSR": round(p, 3) if p == p else 0.0}


KONFIG = [
    ("A OB+BOS",        dict(pakai_fvg=False, pakai_sweep=False)),
    ("B +FVG",          dict(pakai_fvg=True,  pakai_sweep=False)),
    ("C +sweep",        dict(pakai_fvg=False, pakai_sweep=True)),
    ("D +FVG+sweep",    dict(pakai_fvg=True,  pakai_sweep=True)),
]
TIMEFRAME = [("15min", "M15"), ("1h", "H1"), ("4h", "H4"), ("1D", "D1")]
N_TRIAL = len(KONFIG) * len(TIMEFRAME)


def main():
    print("Membangun data ...", flush=True)
    m1 = load_m1()
    print(f"  XAUUSD M1: {len(m1):,} bar, {m1.index.min():%Y-%m-%d} .. {m1.index.max():%Y-%m-%d}")
    print(f"  JUMLAH TRIAL yang dilaporkan ke DSR: {N_TRIAL} "
          f"({len(KONFIG)} konfigurasi x {len(TIMEFRAME)} timeframe)")

    simpan = {}
    for rule, lab in TIMEFRAME:
        h = tf(m1, rule)
        print("\n" + "=" * 110)
        print(f"TIMEFRAME {lab}   ({len(h):,} bar)")
        print("=" * 110)
        rows = []
        for nama, kw in KONFIG:
            print(f"  .. {nama}", flush=True)
            t = jalankan(h, **kw)
            simpan[(lab, nama)] = t
            rows.append(ringkas(t, nama, N_TRIAL))
        print(pd.DataFrame(rows).to_string(index=False), flush=True)

    # ---- pembanding buy & hold pada periode yang sama
    print("\n" + "=" * 110)
    print("PEMBANDING WAJIB: beli-dan-tahan XAUUSD 0.01 lot pada periode yang sama")
    print("=" * 110)
    bh = (m1["close"].iloc[-1] - m1["close"].iloc[0]) * LOT * 100
    thn_bh = (m1.index[-1] - m1.index[0]).days / 365.25
    print(f"  beli-dan-tahan 0.01 lot: ${bh:,.2f} selama {thn_bh:.1f} tahun "
          f"(emas naik {m1['close'].iloc[-1]/m1['close'].iloc[0]:.2f}x)")
    print("  >> setiap strategi long-bias di emas akan terlihat untung di periode ini.")
    print("     Sleeve baru HARUS mengalahkan angka ini, bukan sekadar positif.")

    simpan_path = ROOT / "research" / "_smc_hasil.pkl"
    pd.to_pickle(simpan, simpan_path)
    print(f"\n  trade mentah disimpan -> {simpan_path}")
    print("=" * 110)


if __name__ == "__main__":
    main()
