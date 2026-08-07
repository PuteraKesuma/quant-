"""ETERNA fase-19: ULANG SELURUH RANTAI VALIDASI untuk parameter TERKOREKSI.

Semua validasi sebelumnya (fase 9, 11, 13, 15) memakai struct=20 yang SALAH dan tidak
berlaku lagi. Konfigurasi yang divalidasi di sini:

    H1 | entry Supertrend(ATR_p, x1.8) | gate Supertrend(ATR_p, x3.8) | KONSERVATIF
       | SL = ekstrem struktur ATR_p bar tertutup (terikat, seperti EA baris 259)
       | TP 1:4 | semua jam | tanpa martingale/trailing/breakeven

Empat uji:
  A. PLATEAU   — tetangga parameter di sekelilingnya sehat, atau ini titik keberuntungan?
  B. WALK-FWD  — anchored; konfigurasi TETAP vs optimasi-ulang tiap tahun.
  C. BENTUK LIVE — ATR 14 tunggal / ATR 20 tunggal / vote-2. Mana yang di-deploy?
  D. LAPORAN 2026 — per-trade Jan 2026 s/d hari ini (data lokal + tarikan Dukascopy segar).

Jalankan: python research/eterna_revalidate.py
"""
import warnings
warnings.filterwarnings("ignore")

import datetime as dt
import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, MIN_SL = 1000.0, 0.30
MULT_E, MULT_T, TP_R = 1.8, 3.8, 4.0
CORE = [14, 20]
SIDEWAYS = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
START26 = pd.Timestamp("2026-01-01", tz="UTC")


def load_1m(fresh=False):
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    if not fresh:
        return df
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
    add = add[add.index > last]
    print(f"  + {len(add):,} bar segar Dukascopy s/d {add.index[-1]}")
    m = pd.concat([df, add]).sort_index()
    return m[~m.index.duplicated(keep="first")]


def rs(df, tf="1h"):
    return df.resample(tf, label="left", closed="left").agg(
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


def run(h, p, me=MULT_E, mt=MULT_T, tp_r=TP_R, detail=False):
    st_e, st_t = supertrend(h, p, me), supertrend(h, p, mt)
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(p).min().shift(1).to_numpy()
    shi = h["high"].rolling(p).max().shift(1).to_numpy()
    pos = 0; entry = sl = tp = risk = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = why = None
            if pos == 1:
                if lo[i] <= sl:
                    hit, why = sl, "SL"
                elif hi[i] >= tp:
                    hit, why = tp, "TP"
            else:
                if hi[i] >= sl:
                    hit, why = sl, "SL"
                elif lo[i] <= tp:
                    hit, why = tp, "TP"
            if hit is not None:
                out.append((h.index[ei], h.index[i], pos, entry, hit, risk, why)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], h.index[i], pos, entry, o[i], risk, "flip")); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue
        pos, entry, ei, risk = s, o[i], i, dist
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + tp_r * dist if s == 1 else o[i] - tp_r * dist
    if pos != 0 and detail:
        out.append((h.index[ei], h.index[-1], pos, entry, h["close"].iloc[-1], risk, "OPEN"))
    if not out:
        return None
    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out", "risk", "sebab"])
    t["pnl"] = (t.px_out - t.px_in) * t.arah * LOT * CONTRACT - COST
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t.set_index("masuk") if not detail else t


def stat(pnl, label):
    if pnl is None or len(pnl) < 60:
        return None
    eq = CAPITAL + pnl.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    yr = pnl.groupby(pnl.index.year).sum()
    m = pnl.resample("ME").sum(); m = m[m != 0]
    streak = mx = 0
    for v in m:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    yrs = (pnl.index[-1] - pnl.index[0]).days / 365.25
    thn = 100 * (pnl.sum() / yrs) / CAPITAL
    return {"konfig": label, "n": len(pnl), "net": round(pnl.sum()), "PF": round(pf, 2),
            "maxDD%": round(dd, 1), "thn%": round(thn, 1),
            "Ret/DD": round(thn / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr>0).sum())}/{len(yr)}", "merah%": round(100*(m<0).mean()),
            "beruntun": mx, "sideways": round(pnl.loc[SIDEWAYS[0]:SIDEWAYS[1]].sum())}


def main():
    h = rs(load_1m())
    print(f"H1 {len(h):,} bar\n")

    # ---------------- A. PLATEAU ----------------
    print("=" * 118)
    print("A. PLATEAU — tetangga parameter di sekeliling konfigurasi terpilih")
    print("=" * 118)
    rows = []
    for p in (10, 12, 14, 16, 18, 20, 24):
        t = run(h, p)
        s = stat(t.pnl, f"ATR/struct {p}")
        if s:
            rows.append(s)
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n  variasi mult_entry (ATR 14):")
    rows2 = []
    for me in (1.4, 1.6, 1.8, 2.0, 2.2):
        t = run(h, 14, me=me)
        s = stat(t.pnl, f"entry x{me}")
        if s:
            rows2.append(s)
    print(pd.DataFrame(rows2).to_string(index=False))
    print("\n  variasi TP (ATR 14):")
    rows3 = []
    for tp in (3.0, 3.5, 4.0, 4.5, 5.0):
        t = run(h, 14, tp_r=tp)
        s = stat(t.pnl, f"TP 1:{tp:g}")
        if s:
            rows3.append(s)
    print(pd.DataFrame(rows3).to_string(index=False))

    # ---------------- B. WALK-FORWARD ----------------
    print("\n" + "=" * 118)
    print("B. WALK-FORWARD ANCHORED — latih 2021..N-1, uji tahun N")
    print("=" * 118)
    grid = {}
    for p in (10, 12, 14, 16, 18, 20, 24):
        for me in (1.6, 1.8, 2.0):
            for tp in (3.0, 4.0, 5.0):
                t = run(h, p, me=me, tp_r=tp)
                if t is not None and len(t) >= 100:
                    grid[(p, me, tp)] = t
    fixed = run(h, 14).pnl
    print(f"{'thn':>6} | {'TETAP (ATR14)':>16} | {'PILIH-1 dari data latih':>34}")
    tot_a = tot_b = 0
    for yr in (2022, 2023, 2024, 2025, 2026):
        a0, a1 = pd.Timestamp(f"{yr}-01-01", tz="UTC"), pd.Timestamp(f"{yr+1}-01-01", tz="UTC")
        a = fixed.loc[a0:a1].sum()
        best, bk = -9e9, None
        for k, t in grid.items():
            tr = t.pnl.loc[:a0]
            if len(tr) >= 60 and tr.mean() > best:
                best, bk = tr.mean(), k
        b = grid[bk].pnl.loc[a0:a1].sum()
        tot_a += a; tot_b += b
        print(f"{yr:>6} | {a:16.0f} | {b:10.0f}  ATR{bk[0]} e{bk[1]} TP1:{bk[2]:g}")
    print(f"{'TOTAL':>6} | {tot_a:16.0f} | {tot_b:16.0f}")
    print(f"\n  >> {'TETAP menang' if tot_a >= tot_b else 'PILIH-1 menang'} "
          f"(selisih ${abs(tot_a-tot_b):,.0f})")

    # ---------------- C. BENTUK LIVE ----------------
    print("\n" + "=" * 118)
    print("C. BENTUK LIVE — mana yang di-deploy?")
    print("=" * 118)
    t14, t20 = run(h, 14), run(h, 20)
    live = [stat(t14.pnl, "ATR 14 TUNGGAL"), stat(t20.pnl, "ATR 20 TUNGGAL"),
            stat(pd.concat([t14.pnl / 2, t20.pnl / 2]).sort_index(), "portofolio 14+20 (1/2)")]
    print(pd.DataFrame([x for x in live if x]).to_string(index=False))
    print("\n  Catatan: konfigurasi TUNGGAL tidak butuh voting -> live persis sama dengan")
    print("  backtest, tanpa masalah lot minimum. Itu keunggulan besar untuk deployment.")

    # ---------------- D. LAPORAN 2026 ----------------
    print("\n" + "=" * 118)
    print("D. LAPORAN PER-TRADE 2026 (ATR 14 tunggal) — data lokal + Dukascopy segar")
    print("=" * 118)
    hf = rs(load_1m(fresh=True))
    td = run(hf, 14, detail=True)
    t = td[td.masuk >= START26].reset_index(drop=True).copy()
    t["equity"] = CAPITAL + t.pnl.cumsum()
    t["dd%"] = 100 * (t.equity - t.equity.cummax()) / t.equity.cummax()
    print(f"{'#':>3} {'MASUK':>16} {'KELUAR':>16} {'ARAH':>5} {'IN':>9} {'OUT':>9} "
          f"{'SEBAB':>5} {'R':>6} {'PnL$':>8} {'EQUITY':>9} {'DD%':>7}")
    print("-" * 118)
    for i, r in t.iterrows():
        print(f"{i+1:>3} {r.masuk:%Y-%m-%d %H:%M} {r.keluar:%Y-%m-%d %H:%M} "
              f"{'BUY' if r.arah == 1 else 'SELL':>5} {r.px_in:9.2f} {r.px_out:9.2f} "
              f"{r.sebab:>5} {r.R:+6.2f} {r.pnl:+8.2f} {r.equity:9.2f} {r['dd%']:+7.1f}")
    print("-" * 118)
    n = len(t); win = int((t.pnl > 0).sum())
    gl = abs(t.loc[t.pnl <= 0, "pnl"].sum())
    print(f"  Trade {n} (menang {win}, WR {100*win/n:.1f}%)   "
          f"Net ${t.pnl.sum():+,.2f} ({100*t.pnl.sum()/CAPITAL:+.1f}%)   "
          f"PF {t.loc[t.pnl>0,'pnl'].sum()/gl if gl else 0:.2f}")
    print(f"  Equity akhir ${t.equity.iloc[-1]:,.2f}   "
          f"MAX DD {t['dd%'].min():.1f}%   "
          f"risiko median {100*(t.risk*LOT*CONTRACT).median()/CAPITAL:.1f}% modal "
          f"(max {100*(t.risk*LOT*CONTRACT).max()/CAPITAL:.1f}%)")
    print("\n  PER BULAN:")
    m = t.set_index("masuk").pnl.resample("ME")
    for ts, v in m.sum().items():
        c = m.count().get(ts, 0)
        if c:
            print(f"    {ts:%b %Y}  {v:+9.2f}  ({c:>2} trade)  "
                  + ("#" * int(abs(v)/8) if v > 0 else "." * max(1, int(abs(v)/8))))
    t.to_csv(r"C:\Quant\_MONITOR\eterna_2026_corrected.csv", index=False)
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_2026_corrected.csv")


if __name__ == "__main__":
    main()
