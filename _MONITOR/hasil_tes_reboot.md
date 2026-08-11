# Hasil tes reboot

Boot terakhir  : 2026-08-11 02:49:16 UTC (05:49:16 waktu broker)
Laporan direvisi: 2026-08-11 03:07 UTC

> **Laporan otomatis versi pertama DITARIK.** Vonisnya kebetulan benar, tapi buktinya
> salah — dan tes yang tidak bisa gagal lebih berbahaya daripada tes yang gagal.
> `deal.time` dari MT5 memakai waktu server broker (FBS = UTC+3) sementara skrip
> membandingkannya dengan epoch waktu lokal. Selisih 3 jam itu membuat filter
> meloloskan deal sampai 3 jam SEBELUM boot, jadi laporan mengutip deal baseline
> pra-reboot (05:43-05:48 broker) sebagai bukti pasca-reboot. Skrip sudah diperbaiki
> memakai perbandingan NOMOR TIKET, yang tidak punya zona waktu.

## 1. Auto-login — LULUS

Ada sesi interaktif aktif tanpa satu pun koneksi RDP. Windows login sendiri.

```
 SESSIONNAME       USERNAME                 ID  STATE
 services                                    0  Disc
>console           Administrator             1  Active
 rdp-tcp                                 65536  Listen
```

## 2. Watchdog + rantai — LULUS

| Komponen | Status |
|---|---|
| watchdog | HIDUP, detak 12 detik lalu |
| brain | HIDUP, 3,4 menit setelah boot |
| xau_executor | HIDUP |
| orb_stop_manager | HIDUP |
| MT5 | SIAP, login 106271896 FBS-Demo, Algo Trading ON |

## 3. Order sungguhan setelah boot — LULUS

Boot 05:49:16 waktu broker. Deal di bawah ini semuanya SESUDAH itu, tanpa ada
manusia login atau menjalankan apa pun:

```
05:53:00 broker  BUY  IN   @ 4412.77  +0.00  flowtest_dummy
05:54:01 broker  SELL OUT  @ 4410.57  -2.20  xauexec_close
05:55:01 broker  SELL IN   @ 4411.40  +0.00  flowtest_dummy
05:56:01 broker  BUY  OUT  @ 4410.80  +0.60  xauexec_close
05:57:00 broker  BUY  IN   @ 4412.65  +0.00  flowtest_dummy
05:58:00 broker  SELL OUT  @ 4410.95  -1.70  xauexec_close
05:59:01 broker  SELL IN   @ 4411.74  +0.00  flowtest_dummy
06:00:01 broker  BUY  OUT  @ 4411.14  +0.60  xauexec_close
06:01:07 broker  BUY  IN   @ 4412.42  +0.00  flowtest_dummy
06:02:00 broker  SELL OUT  @ 4412.48  +0.06  xauexec_close
06:03:01 broker  SELL IN   @ 4413.52  +0.00  flowtest_dummy
06:04:01 broker  BUY  OUT  @ 4413.13  +0.39  xauexec_close
06:05:02 broker  BUY  IN   @ 4413.72  +0.00  flowtest_dummy
06:06:00 broker  SELL OUT  @ 4413.57  -0.15  xauexec_close
```

Rantai brain -> xau_executor -> MT5 mengirim DAN menutup order sendiri.

## Vonis

**SEMUA LULUS.** VPS reboot -> Windows login sendiri -> Task Scheduler menaikkan
watchdog -> watchdog menghidupkan MT5 + brain + executor -> order terkirim. Tidak
ada satu langkah pun yang butuh manusia.

Biaya uji: **-$3.60** spread dari 24 deal dummy. Saldo 561.07 -> 557.47.
`flowtest_dummy` sudah DIMATIKAN lagi (config.yaml, magic 920699).
