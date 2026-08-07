"""ETERNA fase-15: laporan LENGKAP per-trade Jan 2026 s/d hari ini, modal $1000.

Data lokal (DuckDB) berhenti 2026-06-25. Skrip ini MENYAMBUNG dengan tarikan segar
Dukascopy untuk menutup celah ~6 minggu, supaya laporan benar-benar sampai "saat ini".

Bentuk yang disimulasikan = BENTUK LIVE (voting 32 anggota, ambang 15% suara bersih),
bukan bentuk portofolio — jadi angkanya sesuai dengan slot eterna_xau yang di-deploy.

Anggota di-warm-up dari SELURUH sejarah (2021+) supaya state mereka pada 1 Jan 2026
sudah benar; yang DILAPORKAN hanya trade dengan tanggal masuk >= 2026-01-01.

Jalankan: python research/eterna_2026_report.py
"""
import warnings
warnings.filterwarnings("ignore")

import datetime as dt
import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, STRUCT, MIN_SL, VOTE_TH = 1000.0, 20, 0.50, 0.15
ATRS, MULT_E, MULT_T, TPS = [7, 10, 14, 20], [1.8, 2.5], [3.8, 5.0], [3.0, 4.0]
MEMBERS = [(a, me, mt, tp) for a in ATRS for me in MULT_E for mt in MULT_T for tp in TPS]
START = pd.Timestamp("2026-01-01", tz="UTC")


def load_merged():
    con = duckdb.connect(DB, read_only=True)
    loc = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    loc["ts"] = pd.to_datetime(loc["ts"], utc=True)
    loc = loc.set_index("ts")
    last = loc.index[-1]
    print(f"Data lokal   : {len(loc):,} bar, sampai {last}")

    import dukascopy_python
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD
    fresh = dukascopy_python.fetch(
        INSTRUMENT_FX_METALS_XAU_USD, dukascopy_python.INTERVAL_MIN_1,
        dukascopy_python.OFFER_SIDE_BID,
        (last - pd.Timedelta(days=2)).to_pydatetime().replace(tzinfo=None),
        dt.datetime.utcnow())
    fresh.index = pd.to_datetime(fresh.index, utc=True)
    fresh = fresh[["open", "high", "low", "close"]]
    fresh = fresh[fresh.index > last]
    print(f"Data Dukascopy tambahan: {len(fresh):,} bar, sampai {fresh.index[-1]}")

    m = pd.concat([loc, fresh]).sort_index()
    m = m[~m.index.duplicated(keep="first")]
    return m.resample("1h", label="left", closed="left").agg(
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


def member_states(h, sts):
    n = len(h)
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = h["high"].rolling(STRUCT).max().shift(1).to_numpy()
    states = np.zeros((len(MEMBERS), n), dtype=np.int8)
    for k, (a, me, mt, tpr) in enumerate(MEMBERS):
        se = sts[(a, me)].to_numpy(); st = sts[(a, mt)].to_numpy()
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
            if se[i-1] != se[i-2]:
                s = int(se[i-1])
                if pos == -s:
                    pos = 0
                if pos == 0 and st[i-1] == s:
                    raw = slo[i] if s == 1 else shi[i]
                    if not np.isnan(raw):
                        dist = abs(o[i] - raw)
                        if dist >= MIN_SL:
                            pos = s
                            sl = o[i] - dist if s == 1 else o[i] + dist
                            tp = o[i] + tpr * dist if s == 1 else o[i] - tpr * dist
            states[k, i] = pos
    return states


def vote_trades(h, states):
    n = len(h)
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = h["high"].rolling(STRUCT).max().shift(1).to_numpy()
    tps = np.array([m[3] for m in MEMBERS])
    longs = (states == 1).sum(axis=0); shorts = (states == -1).sum(axis=0)
    need = VOTE_TH * len(MEMBERS)

    pos = 0; entry = sl = tp = risk = 0.0; ei = 0; out = []
    for i in range(1, n):
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
        net = longs[i] - shorts[i]
        want = 1 if net >= need else (-1 if net <= -need else 0)
        if pos != 0 and want != pos:
            out.append((h.index[ei], h.index[i], pos, entry, o[i], risk, "vote")); pos = 0
        if pos == 0 and want != 0:
            raw = slo[i] if want == 1 else shi[i]
            if np.isnan(raw):
                continue
            dist = abs(o[i] - raw)
            if dist < MIN_SL:
                continue
            agree = states[:, i] == want
            tpr = float(np.median(tps[agree])) if agree.any() else 3.5
            pos, entry, ei, risk = want, o[i], i, dist
            sl = o[i] - dist if want == 1 else o[i] + dist
            tp = o[i] + tpr * dist if want == 1 else o[i] - tpr * dist
    if pos != 0:                                   # posisi masih terbuka -> tandai
        out.append((h.index[ei], h.index[-1], pos, entry, h["close"].iloc[-1], risk, "OPEN"))
    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out", "risk", "sebab"])
    t["pnl"] = (t.px_out - t.px_in) * t.arah * LOT * CONTRACT - COST
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t


def main():
    h = load_merged()
    print(f"H1 gabungan  : {len(h):,} bar, {h.index[0]} .. {h.index[-1]}\n")
    sts = {}
    for a in ATRS:
        for m in set(MULT_E) | set(MULT_T):
            sts[(a, m)] = supertrend(h, a, m)
    print("Mensimulasikan 32 anggota (warm-up dari 2021) ...", flush=True)
    states = member_states(h, sts)
    all_t = vote_trades(h, states)

    t = all_t[all_t.masuk >= START].reset_index(drop=True).copy()
    t["equity"] = CAPITAL + t.pnl.cumsum()
    t["puncak"] = t.equity.cummax()
    t["dd$"] = t.equity - t.puncak
    t["dd%"] = 100 * t["dd$"] / t["puncak"]

    print("\n" + "=" * 126)
    print(f"LAPORAN PER-TRADE — 1 Jan 2026 s/d {h.index[-1]:%d %b %Y}   |   modal awal $1.000, "
          f"lot 0.01, biaya ${COST}/trade")
    print("=" * 126)
    print(f"{'#':>3} {'MASUK':>16} {'KELUAR':>16} {'ARAH':>5} {'HARGA IN':>9} {'HARGA OUT':>9} "
          f"{'SEBAB':>5} {'R':>6} {'PnL $':>8} {'EQUITY':>9} {'DD %':>7}")
    print("-" * 126)
    for i, r in t.iterrows():
        print(f"{i+1:>3} {r.masuk:%Y-%m-%d %H:%M} {r.keluar:%Y-%m-%d %H:%M} "
              f"{'BUY' if r.arah == 1 else 'SELL':>5} {r.px_in:9.2f} {r.px_out:9.2f} "
              f"{r.sebab:>5} {r.R:+6.2f} {r.pnl:+8.2f} {r.equity:9.2f} {r['dd%']:+7.1f}")
    print("-" * 126)

    n = len(t)
    win = int((t.pnl > 0).sum())
    gross_w = t.loc[t.pnl > 0, "pnl"].sum()
    gross_l = abs(t.loc[t.pnl <= 0, "pnl"].sum())
    pf = gross_w / gross_l if gross_l else float("inf")
    print(f"\n{'RINGKASAN':<28}")
    print(f"  Jumlah trade        : {n}   (menang {win}, kalah {n-win}, "
          f"win-rate {100*win/n:.1f}%)")
    print(f"  Net PnL             : ${t.pnl.sum():+,.2f}   "
          f"({100*t.pnl.sum()/CAPITAL:+.1f}% dari modal)")
    print(f"  Equity akhir        : ${t.equity.iloc[-1]:,.2f}")
    print(f"  Profit Factor       : {pf:.2f}")
    print(f"  Rata-rata per trade : ${t.pnl.mean():+,.2f}   (avgR {t.R.mean():+.3f})")
    print(f"  Trade terbaik       : ${t.pnl.max():+,.2f}    terburuk ${t.pnl.min():+,.2f}")
    print(f"  MAX DRAWDOWN        : ${t['dd$'].min():,.2f}  ({t['dd%'].min():.1f}%)")
    lo = t.loc[t['dd$'].idxmin()]
    print(f"    -> titik terdalam : {lo.masuk:%d %b %Y} (equity ${lo.equity:,.2f} "
          f"dari puncak ${lo.puncak:,.2f})")
    print(f"  Sebab keluar        : " + ", ".join(
        f"{k}={v}" for k, v in t.sebab.value_counts().items()))

    print(f"\n{'PER BULAN':<28}")
    m = t.set_index("masuk").pnl.resample("ME")
    for ts, v in m.sum().items():
        cnt = m.count().get(ts, 0)
        if cnt == 0:
            continue
        bar = ("#" * int(abs(v) / 8)) if v > 0 else ("." * max(1, int(abs(v) / 8)))
        print(f"  {ts:%b %Y}  {v:+9.2f}  ({cnt:>2} trade)  {bar}")

    t.to_csv(r"C:\Quant\_MONITOR\eterna_2026_trades.csv", index=False)
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_2026_trades.csv")


if __name__ == "__main__":
    main()
