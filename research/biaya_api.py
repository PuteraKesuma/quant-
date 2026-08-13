"""Biaya API SMC — perkiraan di depan, lalu diganti angka NYATA begitu ada data.

Dipakai untuk menjawab "isi API berapa dollar". Skrip ini sengaja memisahkan dua hal:

  A. PERKIRAAN — berdasarkan frekuensi zona yang dihitung pasti dari backtest,
     tapi dengan konsumsi token web_search yang masih TEBAKAN. Itu bagian yang
     paling tidak pasti dan mendominasi biaya.

  B. NYATA — dibaca dari smc_rr_journal.jsonl yang mencatat token tiap panggilan.
     Begitu ada beberapa puluh baris, angka ini menggantikan perkiraan.

Jalankan lagi setelah sebulan: `python research/biaya_api.py`
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"C:\Quant")

# Harga per 1 juta token (Sonnet 5; harga intro $2/$10 berlaku s/d 2026-08-31)
IN_PER_M, OUT_PER_M = 3.00, 15.00
CARI_PER_1000 = 10.00          # web_search: $10 per 1.000 pencarian

# Frekuensi PASTI dari backtest (research: hitung zona ter-arm, bukan yang terisi)
ZONA_PER_TAHUN = 119           # H4-B 38 + H1-C 81
ORB_TRADE_PER_TAHUN = 120      # perkiraan; advisor dipicu per POSISI untuk 920617

# Tebakan token per panggilan (bagian yang paling tidak pasti)
TEBAK = {
    "rr":      {"in": 19_700, "out": 1_000, "cari": 3},   # 2 chart + web_search
    "advisor": {"in": 21_300, "out":   700, "cari": 3},   # 3 chart + web_search
    "orb":     {"in":  6_300, "out":   700, "cari": 0},   # 3 chart, TANPA web_search
}


def biaya(t: dict) -> float:
    return (t["in"] / 1e6 * IN_PER_M + t["out"] / 1e6 * OUT_PER_M
            + t["cari"] / 1000 * CARI_PER_1000)


def perkiraan() -> None:
    print("=" * 78)
    print("A. PERKIRAAN (frekuensi PASTI, token masih tebakan)")
    print("=" * 78)
    per_zona = biaya(TEBAK["rr"]) + biaya(TEBAK["advisor"])
    thn_zona = per_zona * ZONA_PER_TAHUN
    thn_orb = biaya(TEBAK["orb"]) * ORB_TRADE_PER_TAHUN
    print(f"  per zona ter-arm (agent RR + advisor) : ${per_zona:.3f}")
    print(f"  {ZONA_PER_TAHUN} zona/tahun                        : ${thn_zona:.2f}/tahun")
    print(f"  advisor ORB {ORB_TRADE_PER_TAHUN} posisi/tahun          : ${thn_orb:.2f}/tahun")
    print(f"  {'TOTAL':<38}: ${thn_zona + thn_orb:.2f}/tahun  "
          f"(${(thn_zona + thn_orb)/12:.2f}/bulan)")
    print("\n  Bagian paling tidak pasti: konsumsi token web_search (~15rb/panggilan)."
          "\n  Kalau nyatanya 3x lebih boros, biayanya jadi ~3x. Karena itu skrip ini"
          "\n  membaca pemakaian NYATA dari jurnal — lihat bagian B.")


def nyata() -> None:
    j = ROOT / "smc_rr_journal.jsonl"
    print("\n" + "=" * 78)
    print("B. NYATA (dari smc_rr_journal.jsonl)")
    print("=" * 78)
    if not j.exists():
        print("  belum ada jurnal — belum pernah ada panggilan sukses.")
        return
    baris = [json.loads(l) for l in j.read_text(encoding="utf-8").splitlines() if l.strip()]
    pakai = [b for b in baris if b.get("token", {}).get("in")]
    print(f"  {len(baris)} baris jurnal, {len(pakai)} punya data token")
    if not pakai:
        print("  (baris tanpa token = panggilan GAGAL, mis. kredit habis -> fallback ke mesin)")
        return
    tot = {"in": 0, "out": 0, "cari": 0}
    for b in pakai:
        for k in tot:
            tot[k] += int(b["token"].get(k, 0) or 0)
    n = len(pakai)
    rata = {k: v / n for k, v in tot.items()}
    b_rata = biaya(rata)
    print(f"  rata-rata per panggilan: in {rata['in']:,.0f}  out {rata['out']:,.0f}  "
          f"cari {rata['cari']:.1f}  -> ${b_rata:.3f}")
    print(f"  proyeksi {ZONA_PER_TAHUN} zona/tahun x 2 panggilan: "
          f"${b_rata * ZONA_PER_TAHUN * 2:.2f}/tahun")


if __name__ == "__main__":
    perkiraan()
    nyata()
    print("\n" + "=" * 78)
