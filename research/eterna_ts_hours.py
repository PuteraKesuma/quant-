"""ETERNA fase-10: dua celah terakhir — TRAILING STOP dan PEMINDAIAN JAM.

Basis = ensemble yang LOLOS validasi fase-9:
  H1 / mult_entry 1.8 / mode KONSERVATIF / SL struktur 20 / TP 1:3 / semua sesi
  ensemble 8 anggota: ATR {7,10,14,20} x mult_tren {3.8, 5.0}
  (@biaya $0.50: net $2136, PF 1.40, maxDD -$216, Ret/DD 1.79, 6/6 -> 5/6 tahun hijau)

BAGIAN A — TRAILING STOP (port setia EA baris 221-245: aktif setelah profit > start,
  SL digeser ke harga - jarak, RATCHET (tidak pernah mundur)). Diuji dalam satuan R
  supaya ikut skala SL struktur, plus versi setia EA ($7 tetap = 700 poin).
  Anti-lookahead: trailing di-update SETELAH pengecekan exit bar itu, jadi baru
  berlaku mulai bar berikutnya.

BAGIAN B — PEMINDAIAN JAM (0-23 UTC). Bahaya overfit tinggi: 24 percobaan pasti
  menghasilkan pemenang. Jadi yang dinilai BUKAN jam terbaik, melainkan BENTUK
  sebarannya — apakah ada blok jam bersebelahan yang sama-sama sehat (masuk akal
  secara sesi pasar), atau cuma paku-paku acak (overfit).

Jalankan: python research/eterna_ts_hours.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL = 1000.0
MIN_SL_DIST, STRUCT, TP_R, MULT_E = 0.50, 20, 3.0, 1.8
MEMBERS = [(p, mt) for p in (7, 10, 14, 20) for mt in (3.8, 5.0)]


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr(df, period)
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


def run(df, st_e, st_t, use_tp=True, ts_start_r=None, ts_dist_r=None,
        ts_fixed_start=None, ts_fixed_dist=None, hours=None):
    """Mode konservatif + SL struktur + TP opsional + trailing stop opsional."""
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = df["high"].rolling(STRUCT).max().shift(1).to_numpy()
    hr = df.index.hour.to_numpy()

    pos, entry, sl, tp, risk0, ei, out = 0, 0.0, 0.0, 0.0, 0.0, 0, []

    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                if l[i] <= sl:
                    hit = sl
                elif use_tp and h[i] >= tp:
                    hit = tp
            else:
                if h[i] >= sl:
                    hit = sl
                elif use_tp and l[i] <= tp:
                    hit = tp
            if hit is not None:
                out.append((df.index[ei], pos, entry, hit, risk0)); pos = 0

        # --- trailing: update SETELAH exit bar ini -> berlaku mulai bar berikutnya ---
        if pos != 0 and (ts_start_r or ts_fixed_start):
            if ts_fixed_start:
                start_d, dist_d = ts_fixed_start, ts_fixed_dist
            else:
                start_d, dist_d = ts_start_r * risk0, ts_dist_r * risk0
            if pos == 1:
                if h[i] - entry > start_d:
                    new_sl = h[i] - dist_d
                    if new_sl > sl:
                        sl = new_sl
            else:
                if entry - l[i] > start_d:
                    new_sl = l[i] + dist_d
                    if new_sl < sl:
                        sl = new_sl

        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((df.index[ei], pos, entry, o[i], risk0)); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        if hours is not None and hr[i] not in hours:
            continue
        px = o[i]
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(px - raw)
        if dist < MIN_SL_DIST:
            continue
        pos, entry, ei, risk0 = s, px, i, dist
        sl = px - dist if s == 1 else px + dist
        tp = px + TP_R * dist if s == 1 else px - TP_R * dist

    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t


def ensemble(df, sts, **kw):
    parts = []
    for (p, mt), (se, stt) in sts.items():
        t = run(df, se, stt, **kw)
        if t is not None and len(t):
            x = t[["t_in", "pnl", "R"]].copy()
            x["pnl"] /= len(sts)
            parts.append(x)
    if not parts:
        return None
    return pd.concat(parts).sort_values("t_in").reset_index(drop=True)


def stats(c):
    if c is None or len(c) < 50:
        return None
    w, l = c.loc[c.pnl > 0, "pnl"], c.loc[c.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    eq = c.pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    yr = c.groupby(c.t_in.dt.year).pnl.sum()
    m = c.set_index("t_in").pnl.resample("ME").sum()
    m = m[m != 0]
    return {"n": len(c), "net": round(c.pnl.sum()), "PF": round(pf, 2),
            "maxDD": round(dd), "RetDD": round((c.pnl.sum() / 5.5) / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr>0).sum())}/{len(yr)}",
            "merah%": round(100 * (m < 0).mean())}


def main():
    d = load_h1()
    sts = {(p, mt): (supertrend(d, p, MULT_E), supertrend(d, p, mt)) for p, mt in MEMBERS}
    print(f"H1 {len(d):,} bar | ensemble {len(sts)} anggota | biaya ${COST}/trade\n")

    print("=" * 112)
    print("BAGIAN A — TRAILING STOP")
    print("=" * 112)
    base = stats(ensemble(d, sts))
    print(f"{'varian':40} {'n':>5} {'net$':>7} {'PF':>6} {'maxDD':>7} {'Ret/DD':>7} "
          f"{'hijau':>6} {'merah%':>7}")
    print(f"{'BASELINE (tanpa TS, TP 1:3)':40} {base['n']:5} {base['net']:7} {base['PF']:6.2f} "
          f"{base['maxDD']:7} {base['RetDD']:7.2f} {base['hijau']:>6} {base['merah%']:7}")
    print("-" * 112)

    rows = [("BASELINE tanpa TS", base)]
    for start_r in (0.5, 1.0, 1.5, 2.0):
        for dist_r in (0.5, 1.0, 1.5):
            if dist_r > start_r:
                continue
            s = stats(ensemble(d, sts, ts_start_r=start_r, ts_dist_r=dist_r))
            if s:
                lab = f"TS start {start_r}R jarak {dist_r}R (TP 1:3)"
                rows.append((lab, s))
                print(f"{lab:40} {s['n']:5} {s['net']:7} {s['PF']:6.2f} {s['maxDD']:7} "
                      f"{s['RetDD']:7.2f} {s['hijau']:>6} {s['merah%']:7}")
    # versi setia EA: 700 poin = $7 tetap
    s = stats(ensemble(d, sts, ts_fixed_start=7.0, ts_fixed_dist=7.0))
    if s:
        rows.append(("TS EA asli $7/$7 (TP 1:3)", s))
        print(f"{'TS EA asli $7/$7 (TP 1:3)':40} {s['n']:5} {s['net']:7} {s['PF']:6.2f} "
              f"{s['maxDD']:7} {s['RetDD']:7.2f} {s['hijau']:>6} {s['merah%']:7}")
    # TS sebagai pengganti TP
    for start_r, dist_r in ((1.0, 1.0), (1.5, 1.0), (2.0, 1.5)):
        s = stats(ensemble(d, sts, use_tp=False, ts_start_r=start_r, ts_dist_r=dist_r))
        if s:
            lab = f"TS {start_r}R/{dist_r}R TANPA TP (biar lari)"
            rows.append((lab, s))
            print(f"{lab:40} {s['n']:5} {s['net']:7} {s['PF']:6.2f} {s['maxDD']:7} "
                  f"{s['RetDD']:7.2f} {s['hijau']:>6} {s['merah%']:7}")

    best = max(rows, key=lambda r: r[1]["RetDD"])
    print("-" * 112)
    print(f"TERBAIK by Ret/DD : {best[0]}  (Ret/DD {best[1]['RetDD']}, "
          f"net ${best[1]['net']}, DD ${best[1]['maxDD']})")
    print(f"BASELINE          : Ret/DD {base['RetDD']}, net ${base['net']}, DD ${base['maxDD']}")
    if best[1]["RetDD"] <= base["RetDD"] * 1.10:
        print(">> Trailing stop TIDAK memberi perbaikan berarti (<10%). Best practice: JANGAN dipakai.")
    else:
        print(">> Trailing stop memberi perbaikan; perlu dicek apakah bertahan di uji regime.")

    print("\n" + "=" * 112)
    print("BAGIAN B — PEMINDAIAN JAM ENTRY (UTC)")
    print("Yang dinilai BUKAN jam terbaik, tapi BENTUK sebaran (blok bersebelahan vs paku acak)")
    print("=" * 112)
    per_hour = []
    for hh in range(24):
        c = ensemble(d, sts, hours={hh})
        if c is None or len(c) < 40:
            per_hour.append((hh, 0, np.nan, np.nan))
            continue
        per_hour.append((hh, len(c), c.pnl.sum(), c.R.mean()))

    print(f"{'jam':>4} {'n':>5} {'net$':>8} {'avgR':>8}  grafik")
    for hh, n, net, ar in per_hour:
        if n == 0:
            print(f"{hh:4} {'-':>5} {'-':>8} {'-':>8}")
            continue
        bar = "#" * max(0, int(net / 15)) if net > 0 else "." * max(1, int(-net / 15))
        print(f"{hh:4} {n:5} {net:8.0f} {ar:+8.4f}  {bar}")

    ph = pd.DataFrame(per_hour, columns=["jam", "n", "net", "avgR"]).dropna()
    good = ph[ph.net > 0].jam.tolist()
    print(f"\nJam dengan net positif : {good}")
    # cari blok bersebelahan terpanjang
    blocks, cur = [], []
    for hh in range(24):
        if hh in good:
            cur.append(hh)
        else:
            if cur:
                blocks.append(cur); cur = []
    if cur:
        blocks.append(cur)
    blocks.sort(key=len, reverse=True)
    print(f"Blok bersebelahan terpanjang : {blocks[0] if blocks else '-'} "
          f"({len(blocks[0]) if blocks else 0} jam)")
    print(f"Jumlah blok terpisah         : {len(blocks)}")
    print("  Sedikit blok panjang = pola sesi NYATA. Banyak blok pendek terpencar = acak/overfit.")

    if blocks and len(blocks[0]) >= 3:
        win = set(blocks[0])
        c = ensemble(d, sts, hours=win)
        s = stats(c)
        if s:
            print(f"\nEnsemble dibatasi jam {sorted(win)}:")
            print(f"  n={s['n']}  net=${s['net']}  PF={s['PF']}  maxDD=${s['maxDD']}  "
                  f"Ret/DD={s['RetDD']}  hijau={s['hijau']}  merah={s['merah%']}%")
            print(f"  BASELINE semua jam    : net=${base['net']}  Ret/DD={base['RetDD']}  "
                  f"hijau={base['hijau']}")
            if s["RetDD"] > base["RetDD"] * 1.15:
                print("  >> Filter jam MEMPERBAIKI. Tapi ingat: 24 percobaan -> wajib cek regime dulu.")
            else:
                print("  >> Filter jam tidak memperbaiki berarti. Best practice: pakai SEMUA jam.")


if __name__ == "__main__":
    main()
