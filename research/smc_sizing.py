"""RISIKO SESUAI BALANCE untuk SMC — permintaan user 2026-08-14.

SEKARANG: lot tetap 0.01. Karena SL berasal dari lebar zona OB (yang berubah-ubah),
risiko per trade ikut berayun 0.6% - 7.9% dari akun. Tidak konsisten.

DIMINTA: risiko diatur sesuai balance.

CARA YANG DIUJI:
    lot = (balance x risk_pct) / (jarak_SL x contract_size)
    dibulatkan ke kelipatan lot broker, dijepit ke [0.01, lot_maks]

KENDALA YANG MENENTUKAN: lot MINIMUM broker 0.01. Pada XAUUSD 0.01 lot = $1 per $1
gerak, jadi risiko = jarak_SL dalam dolar. Untuk menargetkan 2% dari $523 ($10.46),
SL harus <= $10.46. Banyak zona lebih lebar dari itu -> lot 0.01 SUDAH melewati
target dan tidak bisa dikecilkan lagi.

Jadi ada DUA tuas, dan keduanya diuji:
  risk_pct      : target; menentukan lot saat balance sudah cukup besar
  risk_pct_maks : plafon KERAS; trade yang melewatinya DILEWATI

Melewati trade mengubah hasil versus backtest, jadi dampaknya diukur - bukan
diasumsikan. Equity di-compound supaya efek "lot naik saat akun tumbuh" terlihat.

Jalankan: python research/smc_sizing.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from smc_xau_backtest import load_m1, tf, jalankan, malam, SWAP_LONG, SWAP_SHORT
from smc_konfirmasi_m5 import zona_terarm, simulasi

MODAL = 523.28
LOT_MIN, LOT_STEP, LOT_MAKS = 0.01, 0.01, 0.50
CONTRACT = 100          # XAUUSD: 1 lot = 100 oz -> 0.01 lot = $1 per $1 gerak
SPREAD_PER_LOT = 50.0   # $0.50 pada 0.01 lot


def bulat_lot(x: float) -> float:
    return max(LOT_MIN, min(LOT_MAKS, np.floor(x / LOT_STEP) * LOT_STEP))


def equity_path(t: pd.DataFrame, *, risk_pct=None, risk_maks=None,
                lot_tetap=None, modal=MODAL):
    """Jalankan ulang trade dengan lot yang ditentukan risiko; equity di-compound."""
    eq = modal
    kurva, lots, dilewati = [], [], 0
    for _, r in t.sort_values("masuk").iterrows():
        jarak = abs(r.px_in - r.sl_px)
        if jarak <= 0:
            continue
        if lot_tetap is not None:
            lot = lot_tetap
        else:
            lot = bulat_lot((eq * risk_pct) / (jarak * CONTRACT))
        risiko = jarak * CONTRACT * lot
        if risk_maks is not None and risiko > eq * risk_maks:
            dilewati += 1
            continue
        gerak = (r.px_out - r.px_in) * r.arah
        pnl = gerak * CONTRACT * lot - SPREAD_PER_LOT * lot + r.swap * (lot / 0.01)
        eq += pnl
        kurva.append((r.masuk, eq)); lots.append(lot)
        if eq <= 0:
            break
    if not kurva:
        return None
    s = pd.Series([e for _, e in kurva], index=[d for d, _ in kurva])
    dd = float(((s - s.cummax()) / s.cummax()).min())
    thn = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / modal) ** (1 / thn) - 1
    return {"n": len(s), "dilewati": dilewati, "akhir$": round(s.iloc[-1], 2),
            "CAGR%": round(100 * cagr, 1), "maxDD%": round(100 * dd, 1),
            "Calmar": round(cagr / abs(dd), 2) if dd else 0,
            "lot rata2": round(float(np.mean(lots)), 3),
            "lot maks": round(float(np.max(lots)), 2)}


def siapkan(t: pd.DataFrame, rr: float) -> pd.DataFrame:
    """Rekonstruksi harga SL dari px_in/px_out dan rr (SL = risiko 1R)."""
    t = t.copy()
    # trade yang kena SL: px_out ADALAH SL. Yang kena TP: SL = px_in -+ (jarak TP / rr)
    sl = np.where(t.sebab == "SL", t.px_out,
                  t.px_in - t.arah * (t.px_out - t.px_in).abs() / rr)
    t["sl_px"] = sl
    return t


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1(); m5 = tf(m1, "5min")
    aliran = {
        "H1-C (konfirmasi M5)": siapkan(simulasi(zona_terarm(tf(m1, "1h"), False, True), m5, 12), 2.0),
        "H4-B (limit pasif)":   siapkan(simulasi(zona_terarm(tf(m1, "4h"), True, False), m5, None), 2.0),
    }

    for nama, t in aliran.items():
        jarak = (t.px_in - t.sl_px).abs()
        print("\n" + "=" * 100)
        print(f"{nama}   n={len(t)}   jarak SL: median ${jarak.median():.2f}  "
              f"p90 ${jarak.quantile(.9):.2f}  maks ${jarak.max():.2f}")
        print("=" * 100)
        rows = []
        r = equity_path(t, lot_tetap=0.01)
        if r: rows.append({"aturan": "lot TETAP 0.01 (sekarang)", **r})
        for rp in (0.01, 0.02, 0.03):
            r = equity_path(t, risk_pct=rp, risk_maks=None)
            if r: rows.append({"aturan": f"risk {rp:.0%} tanpa plafon", **r})
        for rp, rm in ((0.02, 0.03), (0.02, 0.05), (0.03, 0.05)):
            r = equity_path(t, risk_pct=rp, risk_maks=rm)
            if r: rows.append({"aturan": f"risk {rp:.0%}, plafon {rm:.0%}", **r})
        print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 100)
    print("CATATAN: pada modal $523 lot minimum 0.01 sudah berisiko $3-41 per trade")
    print("(0.6%-7.9%). Target risk_pct baru menggigit saat akun tumbuh; sebelum itu")
    print("yang bekerja adalah PLAFON - dia melewati trade berzona lebar.")
    print("=" * 100)


if __name__ == "__main__":
    main()
