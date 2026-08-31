"""Baca catatan regime pasar.

    python tools\\regime_report.py            -> ringkasan + 20 pergantian terakhir
    python tools\\regime_report.py --hari 30  -> batasi ke 30 hari terakhir

Alat ini MEMBACA saja. Tidak ada di sini yang mengubah setelan atau menyentuh
posisi -- keputusan mau riset ulang atau mengganti parameter tetap di tanganmu.

Angka "per basket" berasal dari backtest tick asli empat tahun (2023-2026)
dengan FINAL.set, gate mati, 741 basket:
    RANGING  +$4,71   CAMPURAN  +$0,20   TREN  -$0,67
Itu CATATAN MASA LALU, bukan ramalan.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, r"C:\Quant")

from pipeline.live.regime import (ER_RANGING, ER_TREN, HARAPAN_PER_BASKET,  # noqa: E402
                                  HISTORY, STATE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hari", type=int, default=90, help="jendela riwayat (hari)")
    args = ap.parse_args()

    print("=" * 66)
    print("  CATATAN REGIME PASAR - XAUUSD")
    print("=" * 66)

    if STATE.exists():
        s = json.loads(STATE.read_text(encoding="utf-8"))
        sejak = s.get("since")
        # Umur diambil dari selisih epoch bar (keduanya waktu server), BUKAN
        # dari jam sistem -- epoch MT5 adalah waktu server UTC+3 yang dikemas
        # seolah UTC, dan membandingkannya dengan jam UTC nyata pernah
        # menghasilkan "-1 hari 22 jam".
        j = s.get("umur_jam")
        lama = "" if j is None else f"  ({int(j // 24)} hari {int(j % 24)} jam)"
        print(f"\n  regime sekarang : {s.get('regime')}{lama}")
        print(f"  sejak           : {sejak}  (waktu server, UTC+3)")
        print(f"  ER              : {s.get('er')}   (RANGING <= {ER_RANGING}, "
              f"TREN > {ER_TREN})")
        print(f"  ATR H1          : ${s.get('atr')}")
        print(f"  pergantian       : {s.get('changes')}x sejak mulai mencatat")
        h = s.get("harapan_per_basket")
        if h is not None:
            print(f"  catatan historis : basket di regime ini rata-rata ${h:+.2f} "
                  f"(backtest 4 tahun)")
        if s.get("pending"):
            print(f"  sedang menuju   : {s['pending']} "
                  f"({s['pending_bars']} bar, butuh 2)")
    else:
        print("\n  belum ada state -- brain belum sempat mencatat")

    if not HISTORY.exists():
        print("\n  belum ada riwayat.")
        return

    semua = []
    for ln in HISTORY.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
            # Stempel bar adalah waktu SERVER dan naif. Jendelanya dihitung
            # mundur dari bar TERAKHIR di catatan, bukan dari jam sistem --
            # membandingkan waktu server dengan jam UTC nyata itu yang membuat
            # umur regime tercetak negatif sebelumnya.
            d["_t"] = datetime.fromisoformat(d["bar"].replace("+00:00", ""))
        except Exception:                                    # noqa: BLE001
            continue
        semua.append(d)

    baris = []
    if semua:
        batas = max(d["_t"] for d in semua) - timedelta(days=args.hari)
        baris = [d for d in semua if d["_t"] >= batas]

    if not baris:
        print(f"\n  tidak ada catatan dalam {args.hari} hari terakhir.")
        return

    print(f"\n  --- {args.hari} hari terakhir: {len(baris)} bar H1 tercatat ---")
    c = Counter(d["regime"] for d in baris)
    tot = sum(c.values())
    print(f"\n  {'regime':12}{'bar':>7}{'porsi waktu':>14}{'per basket':>13}")
    print("  " + "-" * 44)
    for k in ("RANGING", "CAMPURAN", "TREN"):
        n = c.get(k, 0)
        print(f"  {k:12}{n:>7}{n / tot * 100:>13.1f}%"
              f"{HARAPAN_PER_BASKET[k]:>+13.2f}")

    # Bobot harapan menurut porsi waktu yang BENAR-BENAR terjadi belakangan.
    harap = sum(c.get(k, 0) / tot * HARAPAN_PER_BASKET[k] for k in HARAPAN_PER_BASKET)
    print(f"\n  campuran regime belakangan ini setara ${harap:+.2f} per basket")
    print(f"  (rata-rata empat tahun: ${sum(HARAPAN_PER_BASKET.values()) / 3:+.2f})")

    ganti = [d for d in baris if d.get("berganti")]
    print(f"\n  --- pergantian regime ({len(ganti)}) ---")
    if not ganti:
        print("  tidak ada pergantian dalam jendela ini")
    for d in ganti[-20:]:
        print(f"  {d['_t']:%Y-%m-%d %H:%M} server  -> {d['regime']:9} "
              f"ER {d['er']:.3f}  ATR ${d['atr']:.2f}")


if __name__ == "__main__":
    main()
