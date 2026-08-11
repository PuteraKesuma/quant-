"""Probe MT5 - membedakan "terminal64.exe ada" dari "MT5 benar-benar bisa trading".

KENAPA ADA: watchdog dulu hanya mengecek `Get-Process terminal64`. Cek itu LOLOS
walaupun terminalnya duduk diam di dialog login, atau tombol Algo Trading mati.
Dua keadaan itu membuat semua monitor hijau sementara tidak ada satu order pun bisa
terkirim - persis jenis kegagalan diam yang paling mahal di sini.

Exit code dipakai watchdog sebagai hasil:
    0 = SEHAT      account_info() ada DAN terminal_info().trade_allowed == True
    1 = MATI       initialize() gagal atau account_info() None (belum login / terminal tutup)
    2 = TERKUNCI   terhubung dan login, TAPI trade_allowed == False

Bedanya 1 dan 2 penting: kode 1 pantas memicu restart terminal64. Kode 2 TIDAK -
tombol Algo Trading adalah keadaan GUI yang tersimpan, restart tidak memperbaikinya,
hanya manusia yang bisa. Watchdog yang me-restart terus-menerus untuk kode 2 cuma
membuang waktu dan mengaburkan jurnal.

Cetak satu baris ringkas ke stdout supaya CEK_TRADING.bat bisa memakainya langsung.

Jalankan:  python C:\\Quant\\_MONITOR\\mt5_probe.py
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except Exception as e:                                  # noqa: BLE001
        print(f"MATI    - modul MetaTrader5 tidak bisa diimpor: {e}")
        return 1

    if not mt5.initialize():
        print(f"MATI    - initialize() gagal: {mt5.last_error()}")
        return 1

    try:
        acc = mt5.account_info()
        if acc is None:
            print(f"MATI    - account_info() None (belum login): {mt5.last_error()}")
            return 1

        term = mt5.terminal_info()
        npos = len(mt5.positions_get() or [])
        nord = len(mt5.orders_get() or [])
        ringkas = (f"login={acc.login} server={acc.server} balance={acc.balance:.2f} "
                   f"equity={acc.equity:.2f} posisi={npos} pending={nord}")

        if term is None or not term.trade_allowed:
            print(f"TERKUNCI - Algo Trading MATI di terminal. {ringkas}")
            return 2

        print(f"SEHAT   - {ringkas}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
