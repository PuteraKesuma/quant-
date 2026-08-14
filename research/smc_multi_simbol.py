"""SMC di BANYAK SIMBOL — cara menaikkan frekuensi yang MEMPERKUAT bukti, bukan melemahkan.

MASALAHNYA: di XAUUSD saja, cuma 39.8% hari punya zona dan 20.9% hari jadi trade.
User ingin minimal 1 trade/hari. Melonggarkan aturan sudah diuji 3 kali dan selalu rugi.

IDE INI BERBEDA SECARA MENDASAR dari tiga percobaan sebelumnya:
    aturan TIDAK diubah sama sekali. Yang ditambah adalah PASARNYA.

Kenapa itu penting secara statistik (_DOC/best_practice.md):
    satu aturan tetap x 9 pasar  = 1 trial dengan 9 sampel   -> MEMPERKUAT bukti
    9 parameter x 1 pasar        = 9 trial                    -> MELEMAHKAN bukti
Jadi kalau aturan yang sama juga menghasilkan di EURUSD/GBPUSD/AUDUSD/NZDUSD, itu
bukti bahwa edge-nya STRUKTURAL, bukan kebetulan cocok dengan emas.

Kalau justru rugi di simbol lain, itu juga informasi berharga: berarti "edge" di XAU
kemungkinan besar artefak, dan itu HARUS dilaporkan apa adanya.

SIMBOL: hanya yang USD-nya di BELAKANG (EURUSD, GBPUSD, AUDUSD, NZDUSD) + XAUUSD.
USDJPY/USDCHF/USDCAD sengaja DILEWATI: USD di depan, jadi nilai per pip butuh
konversi mata uang quote yang tidak dimodelkan di sini. Lebih baik menguji sedikit
simbol dengan biaya yang benar daripada banyak simbol dengan biaya yang dikarang.

BIAYA: spread dan swap diambil LANGSUNG dari MT5 (bukan tebakan), per simbol.

Jalankan: python research/smc_multi_simbol.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from smc_xau_backtest import tf, malam
from smc_konfirmasi_m5 import zona_terarm, simulasi

LOT = 0.01
SIMBOL = ["XAUUSD", "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]


def muat(sym: str) -> pd.DataFrame:
    f = ROOT / "data" / "Level_0_Raw" / f"{sym}_1m.duckdb"
    if not f.exists():
        return pd.DataFrame()
    con = duckdb.connect(str(f), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def biaya_broker(mt5, sym: str, mt5_sym: str) -> dict:
    """Spread dan swap NYATA dari MT5, bukan tebakan."""
    i = mt5.symbol_info(mt5_sym)
    if i is None:
        mt5.symbol_select(mt5_sym, True)
        i = mt5.symbol_info(mt5_sym)
    if i is None:
        return {}
    t = mt5.symbol_info_tick(mt5_sym)
    spread_harga = (t.ask - t.bid) if t and t.ask and t.bid else i.spread * i.point
    # $ per 1.0 gerak harga pada LOT
    dollar_per_unit = i.trade_contract_size * LOT
    return {
        "spread$": spread_harga * dollar_per_unit,
        "swap_long$": i.swap_long * i.point * dollar_per_unit,
        "swap_short$": i.swap_short * i.point * dollar_per_unit,
        "dollar_per_unit": dollar_per_unit,
        "digits": i.digits,
    }


def hitung_pnl(t: pd.DataFrame, b: dict) -> pd.DataFrame:
    t = t.copy()
    t["kotor"] = (t.px_out - t.px_in) * t.arah * b["dollar_per_unit"]
    t["malam"] = [malam(x, y) for x, y in zip(t.masuk, t.keluar)]
    t["swap"] = np.where(t.arah == 1, t.malam * b["swap_long$"], t.malam * b["swap_short$"])
    t["pnl"] = t.kotor - b["spread$"] + t.swap
    return t


def ringkas(t: pd.DataFrame, label: str, n_hari: int, modal=523.28) -> dict:
    if len(t) < 15:
        return {"simbol": label, "n": len(t), "per hari": 0.0, "net$": 0.0,
                "PF": 0.0, "WR%": 0, "maxDD%": 0.0, "thn+": "-"}
    d = t.set_index("masuk").pnl.sort_index()
    eq = modal + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    thn = d.groupby(d.index.year).sum()
    return {"simbol": label, "n": len(d), "per hari": round(len(d) / n_hari, 3),
            "net$": round(d.sum(), 2), "PF": round(w / l if l else 99, 2),
            "WR%": round(100 * (d > 0).mean()), "maxDD%": round(100 * dd, 1),
            "thn+": f"{int((thn > 0).sum())}/{len(thn)}"}


def main():
    import MetaTrader5 as mt5
    from pipeline.fetch.base_fetcher import load_config
    cfg = load_config()
    mt5.initialize()

    print("BIAYA NYATA DARI MT5 (bukan tebakan)")
    print("=" * 92)
    biaya = {}
    for s in SIMBOL:
        ms = cfg["symbols"].get(s, {}).get("mt5_symbol", s)
        b = biaya_broker(mt5, s, ms)
        if not b:
            print(f"  {s:<8} TIDAK TERSEDIA di MT5 - dilewati"); continue
        biaya[s] = b
        print(f"  {s:<8} spread ${b['spread$']:.3f}  swap L ${b['swap_long$']:+.4f} "
              f"S ${b['swap_short$']:+.4f} /malam  ($/unit {b['dollar_per_unit']:.0f})")
    mt5.shutdown()

    semua = []
    print("\n" + "=" * 92)
    print("ATURAN IDENTIK H1-C (OB+BOS+sweep, konfirmasi M5 12 bar, rr 2.0) DI TIAP SIMBOL")
    print("=" * 92)
    rows = []
    n_hari_ref = 0
    for s in SIMBOL:
        if s not in biaya:
            continue
        m1 = muat(s)
        if m1.empty:
            print(f"  {s}: tidak ada data"); continue
        hd = pd.Series(m1.index.normalize().unique()); n_hari = int((hd.dt.weekday < 5).sum())
        n_hari_ref = max(n_hari_ref, n_hari)
        m5 = tf(m1, "5min")
        t = simulasi(zona_terarm(tf(m1, "1h"), False, True), m5, 12)
        if len(t) == 0:
            print(f"  {s}: tidak ada trade"); continue
        t = hitung_pnl(t, biaya[s])
        t["sym"] = s
        semua.append(t)
        rows.append(ringkas(t, s, n_hari))
    print(pd.DataFrame(rows).to_string(index=False))

    if not semua:
        return
    gab = pd.concat(semua).sort_values("masuk")
    print("\n" + "=" * 92)
    print("GABUNGAN (semua simbol jalan bersamaan, masing-masing 0.01 lot)")
    print("=" * 92)
    print(pd.DataFrame([ringkas(gab, "GABUNGAN", n_hari_ref)]).to_string(index=False))
    hz = gab.masuk.dt.normalize()
    print(f"\n  hari dengan >=1 trade : {hz.nunique()} dari {n_hari_ref} "
          f"({100*hz.nunique()/n_hari_ref:.1f}%)")
    per = hz.value_counts()
    print(f"  rata-rata trade/hari  : {len(gab)/n_hari_ref:.2f}")
    for k in sorted(per.unique())[:6]:
        print(f"    hari dengan {k} trade : {(per==k).sum()}")
    menang = [r for r in rows if r["PF"] > 1.0]
    print(f"\n  simbol dengan PF > 1  : {len(menang)} dari {len(rows)}")
    print("  >> Kalau aturan yang SAMA menghasilkan di banyak pasar, edge-nya struktural.")
    print("     Kalau cuma di XAU, kemungkinan besar artefak - dan itu harus diakui.")
    print("=" * 92)


if __name__ == "__main__":
    main()
