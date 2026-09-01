"""Tes latch max-loss governor.

Kejadian 2026-09-01: pemilik berpindah akun real. Selagi MT5 berpindah, satu
bacaan equity di bawah lantai $330 sudah cukup membuat governor melatch
"maxloss" SELAMANYA -- latch itu tidak punya reset harian seperti latch harian.
Akibatnya akun BARU yang sehat (equity $489,79, jauh di atas lantai) diblokir
tanpa satu pun kerugian nyata, dan `_flatten()` ikut terpanggil; kebetulan tidak
ada posisi terbuka saat itu.

Dua penjagaan yang diuji di sini:
  1. butuh TIGA bacaan berturut-turut di bawah lantai sebelum melatch
  2. latch itu milik AKUN tempat dia dipasang -- berpindah akun memulai bersih
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pipeline.live import monthly_governor as G


class FakeMT5:
    def __init__(self, equity, login=1111):
        self._ai = SimpleNamespace(equity=equity, login=login, balance=equity)
        self.flattened = 0

    def account_info(self):
        return self._ai

    def symbol_info_tick(self, sym):
        return None


def buat(tmp_path, monkeypatch, floor=330.0, buffer=0.0):
    monkeypatch.setattr(G, "STATE", tmp_path / "governor.json")
    cfg = {"governor": {"mode": "rules", "magics": [20250822],
                        "daily_loss_usd": 150.0, "daily_stop_buffer": 0.0,
                        "maxloss_floor": floor, "maxloss_buffer": buffer},
           "live": {"mt5_server_utc_offset_hours": 3}}
    gov = G.MonthlyGovernor(cfg)
    # P&L harian dan flatten dinetralkan: yang diuji di sini hanya jalur maxloss.
    monkeypatch.setattr(gov, "_today_realized", lambda *a, **k: 0.0)
    flat = {"n": 0}
    monkeypatch.setattr(gov, "_flatten", lambda mt5: flat.__setitem__("n", flat["n"] + 1))
    return gov, flat


def baca(tmp_path):
    return json.loads((tmp_path / "governor.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------- butuh 3 bacaan
def test_satu_bacaan_rendah_tidak_melatch(tmp_path, monkeypatch):
    gov, flat = buat(tmp_path, monkeypatch)
    gov._rules_poll(FakeMT5(equity=300.0))
    s = baca(tmp_path)
    assert s["paused_maxloss"] is False, "melatch dari SATU bacaan -- bug lama kembali"
    assert s["maxloss_breaches"] == 1
    assert flat["n"] == 0, "flatten dijalankan sebelum dikonfirmasi"


def test_dua_bacaan_masih_belum(tmp_path, monkeypatch):
    gov, _ = buat(tmp_path, monkeypatch)
    for _ in range(2):
        gov._rules_poll(FakeMT5(equity=300.0))
    s = baca(tmp_path)
    assert s["paused_maxloss"] is False
    assert s["maxloss_breaches"] == 2


def test_tiga_bacaan_melatch_dan_flatten(tmp_path, monkeypatch):
    gov, flat = buat(tmp_path, monkeypatch)
    for _ in range(3):
        gov._rules_poll(FakeMT5(equity=300.0))
    s = baca(tmp_path)
    assert s["paused_maxloss"] is True
    assert s["reason"] == "maxloss"
    assert flat["n"] == 1, "flatten harus jalan tepat sekali saat dilatch"


def test_bacaan_sehat_mereset_hitungan(tmp_path, monkeypatch):
    """Ini yang membedakan gangguan sesaat dari kerugian nyata."""
    gov, flat = buat(tmp_path, monkeypatch)
    gov._rules_poll(FakeMT5(equity=300.0))
    gov._rules_poll(FakeMT5(equity=300.0))
    gov._rules_poll(FakeMT5(equity=489.79))          # pulih
    assert baca(tmp_path)["maxloss_breaches"] == 0
    gov._rules_poll(FakeMT5(equity=300.0))
    s = baca(tmp_path)
    assert s["paused_maxloss"] is False
    assert s["maxloss_breaches"] == 1
    assert flat["n"] == 0


def test_equity_nol_tidak_dianggap_pelanggaran(tmp_path, monkeypatch):
    """MT5 mengembalikan 0 saat belum siap. Nol bukan kerugian."""
    gov, _ = buat(tmp_path, monkeypatch)
    for _ in range(5):
        gov._rules_poll(FakeMT5(equity=0.0))
    s = baca(tmp_path)
    assert s["paused_maxloss"] is False
    assert s["maxloss_breaches"] == 0


# ------------------------------------------------------------ milik akunnya
def test_latch_tidak_terbawa_ke_akun_baru(tmp_path, monkeypatch):
    """Skenario 2026-09-01 persis."""
    gov, _ = buat(tmp_path, monkeypatch)
    for _ in range(3):
        gov._rules_poll(FakeMT5(equity=300.0, login=28908348))
    assert baca(tmp_path)["paused_maxloss"] is True

    # pindah ke akun baru yang sehat
    gov._rules_poll(FakeMT5(equity=489.79, login=28910085))
    s = baca(tmp_path)
    assert s["paused_maxloss"] is False, "larangan akun lama terbawa ke akun baru"
    assert s["paused"] is False
    assert s["login"] == 28910085
    assert s["maxloss_breaches"] == 0


def test_latch_bertahan_di_akun_yang_sama(tmp_path, monkeypatch):
    """Sekali dilatch pada akunnya sendiri, tetap terkunci walau equity pulih --
    itu memang maksud aturan max-loss."""
    gov, _ = buat(tmp_path, monkeypatch)
    for _ in range(3):
        gov._rules_poll(FakeMT5(equity=300.0, login=28908348))
    gov._rules_poll(FakeMT5(equity=489.79, login=28908348))
    s = baca(tmp_path)
    assert s["paused_maxloss"] is True
    assert s["paused"] is True


def test_login_dicatat_di_state(tmp_path, monkeypatch):
    gov, _ = buat(tmp_path, monkeypatch)
    gov._rules_poll(FakeMT5(equity=489.79, login=28910085))
    assert baca(tmp_path)["login"] == 28910085


def test_akun_sehat_tetap_aktif(tmp_path, monkeypatch):
    gov, flat = buat(tmp_path, monkeypatch)
    for _ in range(5):
        gov._rules_poll(FakeMT5(equity=489.79, login=28910085))
    s = baca(tmp_path)
    assert s["paused"] is False
    assert s["reason"] == ""
    assert flat["n"] == 0


def test_lantai_nol_menonaktifkan_aturan(tmp_path, monkeypatch):
    gov, _ = buat(tmp_path, monkeypatch, floor=0.0)
    for _ in range(5):
        gov._rules_poll(FakeMT5(equity=1.0))
    assert baca(tmp_path)["paused_maxloss"] is False
