"""ANDAI buku yang SEKARANG jalan di Juni-Juli 2026 - berapa net-nya?

User bertanya setelah melihat rapor asli Juni+Juli = -$261,27. Rapor itu milik buku
LAMA (ZREV, 920625, 920699, dsb) yang sudah dimatikan. Pertanyaannya wajar: kalau
empat sleeve yang sekarang yang jalan, hasilnya berapa?

APA INI DAN APA YANG BUKAN
--------------------------
Ini KONTRAFAKTUAL, bukan bukti. Batasannya harus dibaca sebelum angkanya:

  1. Juni-Juli ADA DI DALAM data yang dipakai merancang keempat sleeve. Jadi hasilnya
     in-sample dan hampir pasti terlalu bagus. Forward test yang berjalan sejak
     Agustus adalah satu-satunya bukti out-of-sample yang kita punya.
  2. Dua bulan = sampel kecil. Untuk SMC yang cuma 0,07 trade/hari, dua bulan
     mungkin hanya berisi segelintir trade. Jangan tarik kesimpulan dari itu.
  3. Backtest tidak tahu soal requote, gap, atau broker menolak order.

Yang PANTAS disimpulkan dari skrip ini cuma satu hal: apakah kerugian Juni-Juli
berasal dari sleeve yang sudah dibuang, atau dari pasarnya sendiri.

BIAYA NYATA DIMODELKAN (bukan angka kotor)
------------------------------------------
  XAUUSD : spread $0,50/trade @0.01 lot; swap LONG -$0,6995/malam,
           SHORT +$0,2491/malam, Rabu 3x
  US100  : $0,10 per poin indeks @0.01 lot; spread+slippage $0,30/trade.
           ORB selalu tutup di akhir sesi -> tidak pernah kena swap.

Semua primitif diambil dari KELAS PRODUKSI (EternaStrategy._supertrend,
SmcLimitManager._pivots/_cari_ob/_ada_fvg/_ada_sweep) supaya tidak ada versi kedua
dari logika yang bisa menyimpang.

MODEL ORB SUDAH DIVALIDASI KE KENYATAAN (11-14 Agustus, satu-satunya jendela di mana
sleeve-nya benar-benar hidup):
  11 Agu  model tidak ada trade  | nyata pending tidak terisi   -> COCOK
  12 Agu  model tidak ada trade  | nyata pending tidak terisi   -> COCOK
  13 Agu  model BUY 14:00 keluar akhir sesi +$2,28 | nyata BUY 14:00 keluar akhir
          sesi +$1,40                                            -> COCOK, selisih $0,88
  14 Agu  model tidak ada trade  | nyata pending DIBATALKAN     -> COCOK
Arah, waktu masuk, dan sebab keluar cocok semua. Selisih $0,88 berarti model ini
sedikit TERLALU OPTIMIS per trade (asumsi biaya $0,30 dan keluar di close M1 terakhir,
bukan di harga market sungguhan). Jadi angka ORB di bawah kalau salah, salahnya ke
arah terlalu bagus - bukan terlalu buruk.

Jalankan: python research/andai_juni_juli.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT))

from pipeline.fetch.base_fetcher import load_config          # noqa: E402
from pipeline.live.signal import EternaStrategy              # noqa: E402
from pipeline.live.smc_limit_manager import SmcLimitManager  # noqa: E402

MULAI = pd.Timestamp("2026-06-01", tz="UTC")
SELESAI = pd.Timestamp("2026-08-01", tz="UTC")
SALDO_AWAL = 435.00        # setoran Juni; dipakai untuk sizing SMC-H1

SPREAD_XAU = 0.50
SWAP_LONG, SWAP_SHORT = -0.6995, 0.2491
NAS_PER_POIN = 0.10        # $ per 1,00 poin indeks @0.01 lot (terukur: 14 poin = $1,40)
NAS_BIAYA = 0.30           # spread + slippage stop order, per trade


# ------------------------------------------------------------------ data
def tarik_m1(simbol: str, n: int = 90000) -> pd.DataFrame:
    """M1 dari terminal MT5, digeser ke UTC sejati. 90000 = batas yang diterima."""
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")
    mt5.symbol_select(simbol, True)
    r = mt5.copy_rates_from_pos(simbol, mt5.TIMEFRAME_M1, 0, n)
    if r is None or len(r) == 0:
        raise RuntimeError(f"{simbol}: tidak ada bar ({mt5.last_error()})")
    d = pd.DataFrame(r)
    d["ts"] = pd.to_datetime(d["time"], unit="s", utc=True) - pd.Timedelta(hours=3)
    d = d.rename(columns={"tick_volume": "volume"})
    return d.set_index("ts")[["open", "high", "low", "close"]].sort_index()


def tarik_d1(simbol: str, n: int = 800) -> pd.Series:
    """Close harian dari MT5 - FEED YANG SAMA dengan M1.

    Wajib begini: gate ORB butuh SMA50 harian, sedangkan M1 hanya sampai 14 Mei
    sehingga SMA-nya NaN sepanjang Juni (versi pertama skrip ini diam-diam
    membuang SELURUH trade ORB Juni karenanya). Menambalnya dengan duckdb TIDAK
    BOLEH: diukur di irisan 1-25 Juni, NAS100 duckdb meleset rata-rata 51,6 poin
    (maks 139) dari US100 FBS - instrumen yang berbeda, bukan sekadar beda spread.
    XAUUSD lebih dekat (rata-rata $0,18) tapi tetap bukan feed yang sama.
    """
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")
    mt5.symbol_select(simbol, True)
    r = mt5.copy_rates_from_pos(simbol, mt5.TIMEFRAME_D1, 0, n)
    if r is None or len(r) == 0:
        raise RuntimeError(f"{simbol} D1: tidak ada bar ({mt5.last_error()})")
    d = pd.DataFrame(r)
    d["ts"] = (pd.to_datetime(d["time"], unit="s", utc=True)
               - pd.Timedelta(hours=3)).dt.normalize()
    return d.set_index("ts")["close"].sort_index()


def malam(masuk: pd.Timestamp, keluar: pd.Timestamp) -> int:
    """Jumlah unit swap: tiap pergantian hari kerja, Rabu dihitung 3x."""
    n = 0
    d = masuk.normalize()
    while d < keluar.normalize():
        d += pd.Timedelta(days=1)
        if d.weekday() >= 5:          # Sabtu/Minggu tidak ada swap
            continue
        n += 3 if d.weekday() == 2 else 1
    return n


def biaya_xau(arah: int, masuk_ts, keluar_ts, lot: float) -> tuple[float, float]:
    """(spread, swap) dalam dolar untuk posisi XAU."""
    m = malam(masuk_ts, keluar_ts)
    swap = m * (SWAP_LONG if arah == 1 else SWAP_SHORT) * (lot / 0.01)
    return SPREAD_XAU * (lot / 0.01), swap


# ------------------------------------------------------------------ ORB
def jalankan_orb(m1: pd.DataFrame, harian: pd.Series, cfg: dict) -> list[dict]:
    """ORB30 di US100, persis aturan orb_stop_manager: gate SMA harian, tembusan
    PERTAMA menentukan, breakeven +0,5R, tutup paksa 20:00 UTC."""
    sp = [s for s in cfg["live"]["strategies"] if s.get("name") == "orb30_nas"][0]
    p = sp["params"]
    rng_min = int(p["range_minutes"])
    tp_mult, sl_mult = float(p["tp_mult"]), float(p["sl_mult"])
    be_r = p.get("breakeven_r")
    n_sma = int(p["trend_sma"])
    eh, em = map(int, p["session_end_utc"].split(":"))

    sma = harian.rolling(n_sma).mean()
    tanpa_sma = 0

    trades = []
    for tgl, grup in m1.groupby(m1.index.normalize()):
        if not (MULAI <= tgl < SELESAI):
            continue
        # gate tren: close harian TERAKHIR YANG SUDAH SELESAI vs SMA-nya (anti-lookahead)
        lalu = harian.index[harian.index < tgl]
        if len(lalu) == 0:
            continue
        d0 = lalu[-1]
        if pd.isna(sma.get(d0, np.nan)):
            tanpa_sma += 1                 # dihitung, supaya tidak hilang diam-diam
            continue
        arah_tren = 1 if harian[d0] > sma[d0] else -1

        buka = tgl.replace(hour=13, minute=30)      # Juni-Juli = DST AS -> 13:30 UTC
        akhir_rng = buka + pd.Timedelta(minutes=rng_min)
        rng = grup.loc[buka:akhir_rng - pd.Timedelta(minutes=1)]
        if len(rng) < rng_min // 2:
            continue
        hi, lo = rng["high"].max(), rng["low"].min()
        ukuran = hi - lo
        if ukuran <= 0:
            continue

        akhir_sesi = tgl.replace(hour=eh, minute=em)
        post = grup.loc[akhir_rng:akhir_sesi]
        if post.empty:
            continue

        # tembusan PERTAMA menentukan nasib sesi
        sisi = 0
        for ts, bar in post.iterrows():
            if bar["high"] > hi:
                sisi = 1; masuk_ts = ts; break
            if bar["low"] < lo:
                sisi = -1; masuk_ts = ts; break
        if sisi == 0 or sisi != arah_tren:
            continue                                 # tidak tembus, atau tembus lawan tren

        entry = hi if sisi == 1 else lo
        risk = sl_mult * ukuran
        sl = entry - risk if sisi == 1 else entry + risk
        tp = entry + tp_mult * ukuran if sisi == 1 else entry - tp_mult * ukuran

        armed = False
        keluar = None
        for ts, bar in post.loc[masuk_ts:].iterrows():
            if sisi == 1:
                if be_r is not None and not armed and (bar["high"] - entry) >= be_r * risk:
                    armed = True
                if armed and bar["low"] <= entry:
                    keluar = (ts, entry, "BE"); break
                if bar["low"] <= sl:
                    keluar = (ts, sl, "SL"); break
                if bar["high"] >= tp:
                    keluar = (ts, tp, "TP"); break
            else:
                if be_r is not None and not armed and (entry - bar["low"]) >= be_r * risk:
                    armed = True
                if armed and bar["high"] >= entry:
                    keluar = (ts, entry, "BE"); break
                if bar["high"] >= sl:
                    keluar = (ts, sl, "SL"); break
                if bar["low"] <= tp:
                    keluar = (ts, tp, "TP"); break
        if keluar is None:                            # tutup paksa di akhir sesi
            akhir = post.loc[masuk_ts:].iloc[-1]
            keluar = (post.loc[masuk_ts:].index[-1], akhir["close"], "SESI")

        kts, kpx, sebab = keluar
        kotor = (kpx - entry) * sisi * NAS_PER_POIN        # $0,10 per poin @0.01 lot
        trades.append({"sleeve": "ORB", "masuk": masuk_ts, "keluar": kts, "arah": sisi,
                       "lot": 0.01, "sebab": sebab, "swap": 0.0,
                       "pnl": kotor - NAS_BIAYA})
    if tanpa_sma:
        print("  [ORB] PERINGATAN: %d hari dilewati karena SMA%d belum terbentuk"
              % (tanpa_sma, n_sma))
    return trades


# ------------------------------------------------------------------ ETERNA
def jalankan_eterna(m1: pd.DataFrame, cfg: dict) -> list[dict]:
    sp = [s for s in cfg["live"]["strategies"] if s.get("name") == "eterna_xau"][0]
    st = EternaStrategy(sp, cfg, data=None)
    h = m1.resample(st.timeframe, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    d_e = st._supertrend(h, st.mult_entry)
    d_t = st._supertrend(h, st.mult_trend)
    hi = h["high"].to_numpy(); lo = h["low"].to_numpy()
    c = h["close"].to_numpy(); idx = h.index
    cap = float((cfg.get("governor") or {}).get("max_risk_per_trade", 90.0))

    trades = []
    pos = 0; entry = sl = tp = 0.0; masuk_ts = None
    for i in range(st.atr_period + st.struct_bars + 2, len(h)):
        # keluar dulu (SL didahulukan - pesimistis)
        if pos != 0:
            kpx = ksebab = None
            if pos == 1:
                if lo[i] <= sl: kpx, ksebab = sl, "SL"
                elif hi[i] >= tp and tp > 0: kpx, ksebab = tp, "TP"
            else:
                if hi[i] >= sl: kpx, ksebab = sl, "SL"
                elif lo[i] <= tp and tp > 0: kpx, ksebab = tp, "TP"
            # flip berlawanan menutup lebih awal
            if kpx is None and d_e[i] != d_e[i - 1] and d_e[i] == -pos:
                kpx, ksebab = c[i], "FLIP"
            if kpx is not None:
                sprd, swp = biaya_xau(pos, masuk_ts, idx[i], 0.01)
                kotor = (kpx - entry) * pos * 100 * 0.01
                trades.append({"sleeve": "ETERNA", "masuk": masuk_ts, "keluar": idx[i],
                               "arah": pos, "lot": 0.01, "sebab": ksebab,
                               "pnl": kotor - sprd + swp, "swap": swp})
                pos = 0

        if pos != 0 or i + 1 >= len(h):
            continue
        if d_e[i] == d_e[i - 1] or d_e[i] != d_t[i]:
            continue                                   # bukan flip, atau gate menolak
        arah = d_e[i]
        jendela = slice(max(0, i - st.struct_bars + 1), i + 1)
        s = lo[jendela].min() if arah == 1 else hi[jendela].max()
        jarak = abs(c[i] - s)
        if jarak < st.min_sl_dist:
            continue
        if jarak * 100 * 0.01 > cap:                   # batas risiko per trade
            continue
        if not (MULAI <= idx[i] < SELESAI):
            continue
        entry = c[i]; sl = s
        tp = entry + st.tp_ratio * jarak * arah if st.tp_ratio > 0 else 0.0
        pos = arah; masuk_ts = idx[i]
    return trades


# ------------------------------------------------------------------ SMC
def jalankan_smc(m1: pd.DataFrame, cfg: dict, nama: str, saldo: float) -> list[dict]:
    sp = [s for s in cfg["live"]["strategies"] if s.get("name") == nama][0]
    m = SmcLimitManager.__new__(SmcLimitManager)     # tanpa menyentuh MT5
    p = sp["params"]
    m.timeframe = p["timeframe"]; m.k = int(p["swing_k"])
    m.ob_lookback = int(p["ob_lookback"]); m.expiry_bars = int(p["expiry_bars"])
    m.rr = float(p["rr"]); m.buffer_frac = float(p["buffer_frac"])
    m.use_fvg = bool(p.get("use_fvg", False)); m.use_sweep = bool(p.get("use_sweep", False))
    m.sweep_window = int(p.get("sweep_window", 5))
    m.sl_maks_usd = float(p.get("sl_maks_usd", 0.0)); m.contract = 100
    m.risk_pct = float(p.get("risk_pct", 0.0)); m.lot_maks = float(p.get("lot_maks", 0.05))
    m.lot = 0.01; m.magic = sp["magic"]
    maks_hari = int(p.get("max_setups_per_day", 2))

    h = m1.resample(m.timeframe, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    o = h["open"].to_numpy(); c = h["close"].to_numpy()
    hi = h["high"].to_numpy(); lo = h["low"].to_numpy()
    idx = h.index; n = len(h)

    sh, slp = m._pivots(hi, lo, m.k)
    lvl_sl = m._level_terkonfirmasi(slp, n) if m.use_sweep else None
    lvl_sh = m._level_terkonfirmasi(sh, n) if m.use_sweep else None

    i_sh = i_sl = 0; last_sh = last_sl = None
    sh_tbs = sl_tbs = False
    pend = None; pos = 0; p_sl = p_tp = 0.0; p_lot = 0.01; masuk_ts = None
    per_hari = defaultdict(int)
    trades = []

    for j in range(1, n):
        while i_sh < len(sh) and sh[i_sh][2] <= j:
            last_sh = sh[i_sh][1]; i_sh += 1; sh_tbs = False
        while i_sl < len(slp) and slp[i_sl][2] <= j:
            last_sl = slp[i_sl][1]; i_sl += 1; sl_tbs = False

        if pos != 0:
            kpx = ksebab = None
            if pos == 1:
                if lo[j] <= p_sl: kpx, ksebab = p_sl, "SL"
                elif hi[j] >= p_tp: kpx, ksebab = p_tp, "TP"
            else:
                if hi[j] >= p_sl: kpx, ksebab = p_sl, "SL"
                elif lo[j] <= p_tp: kpx, ksebab = p_tp, "TP"
            if kpx is not None:
                sprd, swp = biaya_xau(pos, masuk_ts, idx[j], p_lot)
                kotor = (kpx - p_ent) * pos * 100 * p_lot
                trades.append({"sleeve": nama, "masuk": masuk_ts, "keluar": idx[j],
                               "arah": pos, "lot": p_lot, "sebab": ksebab,
                               "pnl": kotor - sprd + swp, "swap": swp})
                pos = 0

        if pend is not None and pos == 0:
            arah, px, s_, t_, exp_bar, _b = pend
            kena = (lo[j] <= px) if arah == 1 else (hi[j] >= px)
            if kena:
                pos, p_sl, p_tp, p_ent = arah, s_, t_, px
                p_lot = m.lot
                if m.risk_pct > 0:
                    jarak = abs(px - s_)
                    mentah = (saldo * m.risk_pct) / (jarak * m.contract) if jarak > 0 else m.lot
                    p_lot = max(0.01, min(m.lot_maks, np.floor(mentah / 0.01) * 0.01))
                    p_lot = round(p_lot, 2)
                masuk_ts = idx[j]; pend = None
            elif j >= exp_bar:
                pend = None
        elif pend is not None:
            pend = None

        arah = 0
        if last_sh is not None and not sh_tbs and c[j] > last_sh:
            arah = 1; sh_tbs = True
        elif last_sl is not None and not sl_tbs and c[j] < last_sl:
            arah = -1; sl_tbs = True
        if arah == 0 or pos != 0 or pend is not None:
            continue
        if per_hari[idx[j].date()] >= maks_hari:
            continue

        ob = m._cari_ob(o, c, hi, lo, j, arah)
        if ob is None:
            continue
        i_ob, ob_lo, ob_hi = ob
        if ob_hi <= ob_lo:
            continue
        if m.use_fvg and not m._ada_fvg(hi, lo, i_ob, j, arah):
            continue
        if m.use_sweep and not m._ada_sweep(hi, lo, c, lvl_sl if arah == 1 else lvl_sh, i_ob, arah):
            continue
        buf = (ob_hi - ob_lo) * m.buffer_frac
        if arah == 1:
            px = ob_hi; s = ob_lo - buf
            if px <= s or px >= c[j]:
                continue
            t = px + m.rr * (px - s)
        else:
            px = ob_lo; s = ob_hi + buf
            if px >= s or px <= c[j]:
                continue
            t = px - m.rr * (s - px)
        if m._lewati_karena_sl(px, s):
            continue
        if not (MULAI <= idx[j] < SELESAI):
            continue
        pend = (arah, px, s, t, j + m.expiry_bars, j)
        per_hari[idx[j].date()] += 1
    return trades


# ------------------------------------------------------------------ main
def main() -> None:
    cfg = load_config()
    print("Menarik M1 dari MT5 ...")
    xau = tarik_m1("XAUUSD")
    nas = tarik_m1("US100")
    nas_d1 = tarik_d1("US100")
    print("  XAUUSD M1 %d bar  %s .. %s" % (len(xau), xau.index[0], xau.index[-1]))
    print("  US100  M1 %d bar  %s .. %s" % (len(nas), nas.index[0], nas.index[-1]))
    print("  US100  D1 %d bar  %s .. %s\n" % (len(nas_d1), nas_d1.index[0].date(),
                                              nas_d1.index[-1].date()))

    semua = []
    semua += jalankan_orb(nas, nas_d1, cfg)
    semua += jalankan_eterna(xau, cfg)
    semua += jalankan_smc(xau, cfg, "smc_xau", SALDO_AWAL)
    semua += jalankan_smc(xau, cfg, "smc_xau_h1", SALDO_AWAL)

    df = pd.DataFrame(semua)
    if df.empty:
        print("TIDAK ADA TRADE sama sekali di jendela ini.")
        return
    df = df.sort_values("masuk").reset_index(drop=True)
    df["bulan"] = df["masuk"].dt.strftime("%Y-%m")

    print("=" * 86)
    print("ANDAI BUKU SEKARANG JALAN DI JUNI-JULI 2026")
    print("=" * 86)
    print("%-14s %7s %10s %8s %8s %7s" % ("sleeve", "trade", "NET $", "menang", "WR%", "swap$"))
    print("-" * 86)
    for s, g in df.groupby("sleeve"):
        wr = 100.0 * (g.pnl > 0).sum() / len(g)
        print("%-14s %7d %+10.2f %8d %7.0f%% %+7.2f" % (
            s, len(g), g.pnl.sum(), (g.pnl > 0).sum(), wr, g.get("swap", pd.Series([0])).sum()))
    print("-" * 86)
    print("%-14s %7d %+10.2f %8d %7.0f%%" % (
        "TOTAL", len(df), df.pnl.sum(), (df.pnl > 0).sum(),
        100.0 * (df.pnl > 0).sum() / len(df)))

    print("\nPer bulan:")
    for b, g in df.groupby("bulan"):
        print("  %s  %3d trade  net %+8.2f" % (b, len(g), g.pnl.sum()))

    print("\nPer bulan x sleeve:")
    piv = df.pivot_table(index="bulan", columns="sleeve", values="pnl",
                         aggfunc="sum", fill_value=0.0)
    print(piv.round(2).to_string())

    print("\nSebab keluar:")
    for s, g in df.groupby("sleeve"):
        print("  %-14s %s" % (s, dict(g.sebab.value_counts())))

    hari = (SELESAI - MULAI).days
    print("\nFrekuensi: %d trade / %d hari = %.2f trade/hari" % (len(df), hari, len(df) / hari))

    print("\n" + "=" * 86)
    print("PEMBANDING - yang SUNGGUH terjadi (buku lama, dari riwayat broker):")
    print("  Juni  -$33,63   Juli -$227,64   gabungan -$261,27")
    print("=" * 86)
    print("INGAT: Juni-Juli ada DI DALAM data perancangan keempat sleeve. Angka di atas")
    print("in-sample dan hampir pasti terlalu bagus. Bukti sesungguhnya cuma forward")
    print("test sejak Agustus.")


if __name__ == "__main__":
    main()
