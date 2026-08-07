"""ETERNA fase-14: UJI PARITAS kode live vs kode riset.

Riset dan implementasi live ditulis TERPISAH (research/eterna_*.py vs
pipeline/live/signal.py::EternaStrategy). Kalau keduanya tidak menghasilkan suara yang
IDENTIK pada data yang sama, maka angka backtest tidak berlaku untuk yang di-deploy.

Ini uji yang dulu menangkap bug window ORB (commit d4f6028: "initial mismatch flags were
this script's own window bug"). Jangan pernah deploy tanpa ini.

Cara: ambil frame H1 yang SAMA, jalankan _vote() milik class live dan replay versi riset,
lalu bandingkan jumlah suara long/short di beberapa titik waktu.

Jalankan: python research/eterna_parity_check.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

from pipeline.live.signal import EternaStrategy

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
STRUCT, MIN_SL = 20, 0.50
ATRS, MULT_E, MULT_T, TPS = [7, 10, 14, 20], [1.8, 2.5], [3.8, 5.0], [3.0, 4.0]
MEMBERS = [(a, me, mt, tp) for a in ATRS for me in MULT_E for mt in MULT_T for tp in TPS]


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


# ---------- versi RISET (ditulis ulang mandiri, sengaja tidak meng-import class live) ----------
def atr_s(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def st_research(df, period, mult):
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
    return d


def vote_research(h):
    sts = {}
    for a in ATRS:
        for m in set(MULT_E) | set(MULT_T):
            sts[(a, m)] = st_research(h, a, m)
    o = h["open"].to_numpy(); hi = h["high"].to_numpy(); lo = h["low"].to_numpy()
    slo = h["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = h["high"].rolling(STRUCT).max().shift(1).to_numpy()
    n = len(h)
    longs = shorts = 0
    for (a, me, mt, tpr) in MEMBERS:
        se, st = sts[(a, me)], sts[(a, mt)]
        pos = 0; sl = tp = 0.0
        for i in range(2, n):
            if pos != 0:
                hit = None
                if pos == 1:
                    hit = sl if lo[i] <= sl else (tp if hi[i] >= tp else None)
                else:
                    hit = sl if hi[i] >= sl else (tp if lo[i] <= tp else None)
                if hit is not None:
                    pos = 0
            if se[i-1] == se[i-2]:
                continue
            s = se[i-1]
            if pos == -s:
                pos = 0
            if pos != 0 or st[i-1] != s:
                continue
            raw = slo[i] if s == 1 else shi[i]
            if np.isnan(raw):
                continue
            dist = abs(o[i] - raw)
            if dist < MIN_SL:
                continue
            pos = s
            sl = o[i] - dist if s == 1 else o[i] + dist
            tp = o[i] + tpr * dist if s == 1 else o[i] - tpr * dist
        if pos == 1:
            longs += 1
        elif pos == -1:
            shorts += 1
    return longs, shorts


def main():
    h = load_h1()
    print(f"H1 {len(h):,} bar  {h.index[0]} .. {h.index[-1]}\n")

    spec = {"name": "eterna_xau", "symbol": "XAUUSD", "lot": 0.01, "magic": 920627,
            "params": {"atr_periods": ATRS, "mult_entries": MULT_E,
                       "mult_trends": MULT_T, "tp_ratios": TPS,
                       "struct_bars": STRUCT, "min_sl_dist": MIN_SL}}
    strat = EternaStrategy(spec, {"symbols": {"XAUUSD": {"mt5_symbol": "XAUUSD"}}}, data=None)

    print(f"{'titik uji (akhir frame)':30} {'LIVE long/short':>18} {'RISET long/short':>18}  hasil")
    print("-" * 92)
    ok_all = True
    for cut in (2000, 5000, 12000, 20000, 28000, len(h)):
        frame = h.iloc[:cut]
        lv_l, lv_s, _ = strat._vote(frame)
        rs_l, rs_s = vote_research(frame)
        ok = (lv_l == rs_l) and (lv_s == rs_s)
        ok_all &= ok
        print(f"{str(frame.index[-1]):30} {f'{lv_l}/{lv_s}':>18} {f'{rs_l}/{rs_s}':>18}  "
              f"{'COCOK' if ok else '*** BEDA ***'}")

    print("-" * 92)
    if ok_all:
        print("\nPARITAS TERBUKTI: kode live menghasilkan suara IDENTIK dengan kode riset.")
        print("Angka backtest sah dipakai untuk slot yang di-deploy.")
    else:
        print("\n*** PARITAS GAGAL *** — JANGAN deploy. Angka backtest tidak berlaku.")


if __name__ == "__main__":
    main()
