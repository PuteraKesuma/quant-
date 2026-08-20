"""ETERNA: apakah ENSEMBLE mengalahkan KONFIGURASI TUNGGAL yang kita jalankan live?

KENAPA SKRIP INI ADA
Fase-11 (walk-forward) menemukan ensemble TETAP 8-anggota menghasilkan $2.115 sementara
menyetel ulang parameter tiap tahun hanya $1.339 -- jadi "ensemble" terlihat menjanjikan.
Fase-13 (voting) lalu menemukan bentuk voting TIDAK setara dengan portofolio paralel.

Tapi kedua studi itu membandingkan ensemble dengan ensemble. TIDAK SATU PUN membandingkan
ensemble dengan hal yang sebenarnya kita jalankan: SATU konfigurasi (ATR16, entry 1.8,
tren 3.8, TP 1:4). Dan angka tunggal yang beredar (maxDD -11%) berasal dari skrip lain
dengan konvensi lain, jadi tidak boleh dibandingkan silang.

Skrip ini menjalankan SEMUANYA di satu harness dengan konvensi LIVE yang identik, supaya
perbandingannya sah.

KONVENSI LIVE (mengikuti pipeline/live/signal.py::EternaStrategy dan EternaBot.mq5)
  - bertindak hanya pada bar H1 TERTUTUP, satu keputusan per bar
  - masuk saat Supertrend entry FLIP dan Supertrend tren SETUJU
  - harga masuk = CLOSE bar sinyal (bukan open bar berikutnya)
  - SL = ekstrem `struct_bars` bar tertutup TERMASUK bar sinyal
  - TP = tp_ratio x jarak itu; tolak kalau jarak < min_sl_dist
  - flip berlawanan menutup posisi lebih awal
  - satu posisi pada satu waktu
  - risk cap $ menolak trade yang jarak stop-nya terlalu lebar

[2026-08-21 KOREKSI DATA -- BACA SEBELUM MEMPERCAYAI ANGKA APA PUN DI SINI]
Versi pertama skrip ini memuat bar dari duckdb XAUUSD_1m. Sumber itu BOLONG untuk
2026: jendela 2026-02-16 00:00..10:00 hanya berisi 1 bar, sementara Strategy Tester
punya 9. Supertrend path-dependent, jadi bar yang hilang menggeser jalur band dan
membalik arah gate tren. Gejalanya: EA mencatat 110 trade di 2023, skrip ini 71.
load_h1() sekarang memakai copy_rates_from_pos (satu-satunya sumber yang cocok
dengan tester) dan menolak jalan kalau riwayatnya terlalu bolong. Kesimpulan
ensemble/voting yang dihasilkan SEBELUM koreksi ini batal.

ATURAN KEPUTUSAN -- DIKUNCI SEBELUM HASIL TERLIHAT
Ensemble diadopsi HANYA jika, dibanding konfigurasi tunggal live:
  1. Ret/DD NAIK, DAN
  2. net TIDAK turun lebih dari 10%, DAN
  3. unggul di MAYORITAS tahun (bukan cuma total).
Kalau tidak ketiganya terpenuhi -> tetap pakai konfigurasi tunggal. Aturan ini ditulis
lebih dulu supaya pemenang tidak bisa dipilih setelah melihat angka.

CATATAN DRAWDOWN
Drawdown dihitung mark-to-market per bar H1 memakai ekstrem yang MERUGIKAN di dalam bar,
bukan dari kurva trade-tertutup. Kurva trade-tertutup menyembunyikan momen paling sakit;
pengukuran sebelumnya di proyek ini menunjukkan basis-bar meremehkan DD sekitar 2x.
Angka final tetap harus diverifikasi di MT5 Strategy Tester (tick sungguhan).

Jalankan: python research/eterna_ensemble_final.py
"""
import warnings

warnings.filterwarnings("ignore")

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

# (duckdb sengaja TIDAK dipakai lagi -- lihat catatan di load_h1)

LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL = 1000.0          # dilaporkan juga terhadap ekuitas nyata di akhir
RISK_CAP = 70.0           # hasil sweep 2026-08-20 (90 -> 70)
MIN_SL_DIST = 0.30        # nilai live
TP_RATIO = 4.0            # nilai live

# Konfigurasi TUNGGAL yang benar-benar jalan di akun.
LIVE = dict(atr=16, me=1.8, mt=3.8, struct=16)

# Anggota ensemble dipilih A PRIORI: docstring EternaStrategy mencatat atr_period
# 10..24 adalah dataran (plateau) yang sehat dan 16 sengaja diambil di TENGAHnya.
# Jadi anggota merentang dataran itu -- BUKAN hasil pencarian di data ini, supaya
# tidak ada bias seleksi.
ENS_ATRS = [10, 14, 20, 24]
ENS_MTS = [3.8, 5.0]
MEMBERS = [dict(atr=p, me=1.8, mt=m, struct=p) for p in ENS_ATRS for m in ENS_MTS]

YEARS = [2021, 2022, 2023, 2024, 2025, 2026]


def load_h1() -> pd.DataFrame:
    """Bar H1 dari terminal MT5 lewat copy_rates_from_pos.

    JANGAN memakai duckdb atau copy_rates_range untuk ini. Terukur 2026-08-21,
    jendela 2026-02-16 00:00..10:00 waktu server:

        copy_rates_from_pos  -> 9 bar   (cocok persis dengan Strategy Tester)
        copy_rates_range     -> 2 bar
        duckdb XAUUSD_1m     -> 1 bar

    Supertrend bersifat PATH-DEPENDENT: band final dibawa maju sampai ditembus,
    jadi bar yang hilang menggeser seluruh jalur band dan mengubah arah tren
    berbar-bar sesudahnya. Akibatnya bukan sekadar meleset sedikit -- studi yang
    dijalankan di atas data bolong menghasilkan 71 trade di 2023 sementara EA yang
    sama di tester menghasilkan 110, dan gate trennya berbeda arah di bar yang sama.

    Seluruh kesimpulan ensemble/voting versi pertama skrip ini dibuat di atas data
    duckdb dan HARUS dianggap batal. Acuan kebenaran adalah MT5 Strategy Tester.
    """
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")
    try:
        r = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 99000)
    finally:
        mt5.shutdown()
    if r is None or len(r) == 0:
        raise RuntimeError("copy_rates_from_pos tidak mengembalikan bar")

    df = pd.DataFrame(r)
    # Stempel bar adalah waktu SERVER broker (UTC+3 / UTC+2 saat DST). Label tidak
    # memengaruhi simulasi -- hanya urutan dan isi bar -- jadi dipakai apa adanya,
    # dan irisan per tahun cuma meleset beberapa jam di batas tahun.
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("ts")[["open", "high", "low", "close"]].sort_index()

    # Penjaga: kalau riwayat bolong parah, gagalkan dengan berisik daripada
    # diam-diam menghasilkan angka yang salah.
    span_h = (df.index[-1] - df.index[0]).total_seconds() / 3600.0
    if len(df) < 0.5 * span_h * (5.0 / 7.0):
        raise RuntimeError(f"riwayat H1 terlalu bolong: {len(df)} bar untuk rentang {span_h:.0f} jam")
    return df


def atr_wilder(df: pd.DataFrame, n: int) -> np.ndarray:
    """ATR Wilder, sama persis dengan EternaStrategy._atr (ewm alpha=1/n)."""
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def supertrend_dir(df: pd.DataFrame, period: int, mult: float) -> np.ndarray:
    """+1/-1 per bar. Port langsung dari EternaStrategy._supertrend (band terkunci)."""
    a = atr_wilder(df, period)
    hl2 = ((df["high"] + df["low"]) / 2.0).to_numpy()
    up, lo = hl2 + mult * a, hl2 - mult * a
    c = df["close"].to_numpy()
    n = len(c)
    fu = np.full(n, np.nan)
    fl = np.full(n, np.nan)
    d = np.ones(n, dtype=int)
    for i in range(1, n):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            d[i] = d[i - 1]
            continue
        fu[i] = up[i] if (np.isnan(fu[i - 1]) or up[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lo[i] if (np.isnan(fl[i - 1]) or lo[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if not np.isnan(fu[i - 1]) and c[i] > fu[i]:
            d[i] = 1
        elif not np.isnan(fl[i - 1]) and c[i] < fl[i]:
            d[i] = -1
        else:
            d[i] = d[i - 1]
    return d


def struct_stop(low: np.ndarray, high: np.ndarray, i: int, bars: int, side: int) -> float:
    """Ekstrem `bars` bar tertutup TERMASUK bar i -- irisan h_c.iloc[-struct_bars:]."""
    lo = max(0, i - bars + 1)
    return float(low[lo:i + 1].min()) if side == 1 else float(high[lo:i + 1].max())


def simulate(df: pd.DataFrame, decide, struct_bars_of, cap=RISK_CAP):
    """Mesin bersama. `decide(i)` -> (side, struct_bars) atau None.

    Mengembalikan (trades_df, equity_series) di mana equity adalah mark-to-market
    per bar: realisasi + floating memakai ekstrem yang MERUGIKAN di dalam bar.
    """
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(df)

    pos = 0
    entry = sl = tp = 0.0
    ei = 0
    dist0 = 0.0
    trades = []
    realised = np.zeros(n)
    floating = np.zeros(n)
    run = 0.0

    for i in range(1, n):
        # --- kelola posisi terbuka lebih dulu (bar ini bisa menyentuh SL/TP) ---
        if pos != 0:
            hit = None
            if pos == 1:
                if l[i] <= sl:
                    hit = sl
                elif h[i] >= tp:
                    hit = tp
            else:
                if h[i] >= sl:
                    hit = sl
                elif l[i] <= tp:
                    hit = tp
            if hit is not None:
                pnl = (hit - entry) * pos * LOT * CONTRACT - COST
                run += pnl
                trades.append((df.index[ei], df.index[i], pos, entry, hit, dist0, pnl))
                pos = 0
            else:
                adverse = l[i] if pos == 1 else h[i]
                floating[i] = (adverse - entry) * pos * LOT * CONTRACT

        realised[i] = run

        # --- keputusan pada bar tertutup i ---
        d = decide(i)
        if pos != 0 and d is not None and d[0] == -pos:
            # flip berlawanan menutup lebih awal, di close bar sinyal
            pnl = (c[i] - entry) * pos * LOT * CONTRACT - COST
            run += pnl
            realised[i] = run
            floating[i] = 0.0
            trades.append((df.index[ei], df.index[i], pos, entry, c[i], dist0, pnl))
            pos = 0

        if pos != 0 or d is None:
            continue

        side, sb = d
        px = c[i]
        raw = struct_stop(l, h, i, sb, side)
        dist = abs(px - raw)
        if dist < MIN_SL_DIST:
            continue
        if cap > 0 and dist * LOT * CONTRACT > cap:
            continue
        pos, entry, ei, dist0 = side, px, i, dist
        sl = px - dist if side == 1 else px + dist
        tp = px + TP_RATIO * dist if side == 1 else px - TP_RATIO * dist

    t = pd.DataFrame(trades, columns=["t_in", "t_out", "dir", "px_in", "px_out", "dist", "pnl"])
    eq = pd.Series(realised + floating, index=df.index)
    return t, eq


def make_single(df, cfg):
    de = supertrend_dir(df, cfg["atr"], cfg["me"])
    dt = supertrend_dir(df, cfg["atr"], cfg["mt"])

    def decide(i):
        if de[i] == de[i - 1]:
            return None                      # tanpa flip, tanpa sinyal
        if dt[i] != de[i]:
            return None                      # gate tren tidak setuju
        return (int(de[i]), cfg["struct"])
    return decide


def make_vote(df, members, thresh):
    """Voting: tiap anggota memberi arah 'siap' (+1/-1/0). Masuk saat suara bersih
    melewati ambang DAN arah konsensus baru berubah. struct_bars = median anggota
    yang setuju -- bentuk yang bisa dieksekusi dengan SATU posisi 0.01 lot."""
    des = [supertrend_dir(df, m["atr"], m["me"]) for m in members]
    dts = [supertrend_dir(df, m["atr"], m["mt"]) for m in members]
    sbs = [m["struct"] for m in members]
    k = len(members)

    ready = np.zeros((k, len(df)), dtype=int)
    for j in range(k):
        agree = des[j] == dts[j]
        ready[j] = np.where(agree, des[j], 0)

    net = ready.sum(axis=0) / k
    cons = np.where(net >= thresh, 1, np.where(net <= -thresh, -1, 0))

    def decide(i):
        if cons[i] == 0 or cons[i] == cons[i - 1]:
            return None                      # butuh perubahan konsensus yang BARU
        side = int(cons[i])
        sb = [sbs[j] for j in range(k) if ready[j, i] == side]
        return (side, int(np.median(sb)) if sb else LIVE["struct"])
    return decide


def metrics(t: pd.DataFrame, eq: pd.Series, lo=None, hi=None) -> dict:
    if lo is not None:
        t = t[(t.t_in >= lo) & (t.t_in < hi)]
        eq = eq[(eq.index >= lo) & (eq.index < hi)]
    if t.empty or eq.empty:
        return dict(n=0, net=0.0, pf=0.0, dd=0.0, retdd=0.0, wr=0.0)
    eq = eq - eq.iloc[0]
    dd = float((eq - eq.cummax()).min())
    win = float(t.pnl[t.pnl > 0].sum())
    loss = float(-t.pnl[t.pnl <= 0].sum())
    net = float(t.pnl.sum())
    return dict(n=len(t), net=net,
                pf=(win / loss if loss > 0 else float("inf")),
                dd=dd,
                retdd=(net / abs(dd) if dd < 0 else float("inf")),
                wr=100.0 * float((t.pnl > 0).mean()))


def row(name, m):
    return (f"{name:34s} {m['n']:>5d} {m['net']:>9.0f} {m['pf']:>6.2f} "
            f"{m['dd']:>9.0f} {m['retdd']:>7.2f} {m['wr']:>6.1f}")


if __name__ == "__main__":
    df = load_h1()
    print(f"H1 {len(df):,} bar  {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}")
    print(f"cap ${RISK_CAP:.0f} | TP 1:{TP_RATIO:.0f} | biaya ${COST}/trade | {LOT} lot\n")

    cands = [("TUNGGAL (live: ATR16 e1.8 t3.8)", make_single(df, LIVE))]
    # Sapuan HALUS di seluruh tingkat suara yang bisa dicapai (kelipatan 1/8).
    # Tujuannya bukan mencari yang terbaik -- tapi melihat apakah kandidat yang
    # lolos berdiri di DATARAN atau hanya PUNCAK sendirian. Puncak tunggal yang
    # diapit lembah hampir selalu kebetulan; proyek ini sudah memakai aturan
    # "tengah dataran mengalahkan puncak dataran" saat memilih atr_period 16.
    for k in range(1, 9):
        th = k / 8.0
        cands.append((f"VOTING >={k}/8 suara bersih (amb {th:.0%})", make_vote(df, MEMBERS, th)))

    print("=" * 104)
    print("SELURUH PERIODE  (tidak ada parameter yang di-fit di sini -- semua a priori)")
    print("=" * 104)
    print(f"{'kandidat':34s} {'n':>5s} {'net$':>9s} {'PF':>6s} {'maxDD$':>9s} {'Ret/DD':>7s} {'WR%':>6s}")
    print("-" * 104)

    res = {}
    for name, dec in cands:
        t, eq = simulate(df, dec, None)
        res[name] = (t, eq)
        print(row(name, metrics(t, eq)))

    print("\n" + "=" * 104)
    print("PER TAHUN  net$ (dan maxDD$)")
    print("=" * 104)
    hdr = f"{'kandidat':34s}" + "".join(f"{y:>17d}" for y in YEARS)
    print(hdr)
    print("-" * 104)
    per_year = {}
    for name, (t, eq) in res.items():
        cells, ys = [], {}
        for y in YEARS:
            lo = pd.Timestamp(f"{y}-01-01", tz="UTC")
            hi = pd.Timestamp(f"{y + 1}-01-01", tz="UTC")
            m = metrics(t, eq, lo, hi)
            ys[y] = m
            cells.append(f"{m['net']:>9.0f}({m['dd']:>6.0f})")
        per_year[name] = ys
        print(f"{name:34s}" + "".join(cells))

    # ---- aturan keputusan yang sudah dikunci di docstring ----
    base_name = cands[0][0]
    bt, beq = res[base_name]
    bm = metrics(bt, beq)
    by = per_year[base_name]

    print("\n" + "=" * 104)
    print("VONIS  (aturan dikunci sebelum hasil: Ret/DD naik, net tidak -10%, unggul mayoritas tahun)")
    print("=" * 104)
    print(f"acuan TUNGGAL: net ${bm['net']:.0f}  Ret/DD {bm['retdd']:.2f}  maxDD ${bm['dd']:.0f}\n")

    winner = None
    for name, (t, eq) in res.items():
        if name == base_name:
            continue
        m = metrics(t, eq)
        c1 = m["retdd"] > bm["retdd"]
        c2 = m["net"] >= 0.90 * bm["net"]
        wins = sum(1 for y in YEARS if per_year[name][y]["net"] > by[y]["net"])
        c3 = wins > len(YEARS) / 2
        ok = c1 and c2 and c3
        print(f"{name:34s} RetDD {m['retdd']:>5.2f} {'OK ' if c1 else 'GAGAL'} | "
              f"net {m['net']:>7.0f} {'OK ' if c2 else 'GAGAL'} | "
              f"tahun unggul {wins}/{len(YEARS)} {'OK ' if c3 else 'GAGAL'} | "
              f"{'>>> LOLOS' if ok else 'ditolak'}")
        if ok and (winner is None or m["retdd"] > metrics(*res[winner])["retdd"]):
            winner = name

    print()
    if winner:
        print(f">> ENSEMBLE MENANG: {winner}")
        print("   Langkah wajib berikutnya: verifikasi di MT5 Strategy Tester (tick sungguhan)")
        print("   sebelum menyentuh apa pun yang live.")
    else:
        print(">> TIDAK ADA ENSEMBLE YANG LOLOS. Konfigurasi TUNGGAL tetap dipakai.")
        print("   Ini hasil yang sah, bukan kegagalan: menambah anggota tidak otomatis")
        print("   menambah edge kalau anggotanya berkorelasi tinggi -- mereka salah")
        print("   bersamaan, jadi drawdown tidak terdiversifikasi.")
