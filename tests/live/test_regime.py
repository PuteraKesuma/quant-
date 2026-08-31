"""Tes RegimeWatcher.

Yang dijaga di sini, berurutan dari yang paling mudah salah:
  - ambang klasifikasi persis di batas tersil
  - ER dihitung dari bar TERTUTUP saja (bar berjalan harus dibuang)
  - histeresis: label tidak boleh berkedip tiap jam
  - tidak bekerja dua kali untuk bar yang sama
  - watcher TIDAK PERNAH memanggil apa pun yang mengubah akun
"""
from __future__ import annotations

import json

import pytest

from pipeline.live import regime as R


class FakeRates:
    """Peniru array terstruktur MT5: mendukung r["close"] dan len(r)."""

    def __init__(self, times, opens, highs, lows, closes):
        self._d = {"time": times, "open": opens, "high": highs,
                   "low": lows, "close": closes}

    def __getitem__(self, k):
        return self._d[k]

    def __len__(self):
        return len(self._d["time"])


def buat_rates(closes, t0=1_700_000_000, step=3600):
    """Bar H1 dari deret close. High/low dibuat rapat supaya ATR kecil dan
    tidak mengganggu pengujian ER."""
    n = len(closes)
    times = [t0 + i * step for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return FakeRates(times, list(closes), highs, lows, list(closes))


class FakeMT5:
    TIMEFRAME_H1 = 16385

    def __init__(self, rates):
        self._rates = rates
        self.calls = 0

    def copy_rates_from_pos(self, symbol, tf, start, count):
        self.calls += 1
        return self._rates


@pytest.fixture(autouse=True)
def _isolasi(tmp_path, monkeypatch):
    """State dan history tidak boleh menyentuh _MONITOR asli saat tes."""
    monkeypatch.setattr(R, "STATE", tmp_path / "regime.json")
    monkeypatch.setattr(R, "HISTORY", tmp_path / "regime_history.jsonl")


def pasang_mt5(monkeypatch, rates):
    fake = FakeMT5(rates)
    monkeypatch.setattr("pipeline.live.book._mt5", lambda: fake)
    return fake


# --------------------------------------------------------------- klasifikasi
def test_ambang_ranging_inklusif():
    assert R.klasifikasi(R.ER_RANGING) == "RANGING"


def test_tepat_di_atas_ranging_jadi_campuran():
    assert R.klasifikasi(R.ER_RANGING + 1e-6) == "CAMPURAN"


def test_ambang_tren_eksklusif():
    assert R.klasifikasi(R.ER_TREN) == "CAMPURAN"
    assert R.klasifikasi(R.ER_TREN + 1e-6) == "TREN"


def test_ekstrem():
    assert R.klasifikasi(0.0) == "RANGING"
    assert R.klasifikasi(1.0) == "TREN"


# ---------------------------------------------------------------- perhitungan
def test_garis_lurus_er_satu():
    """Harga naik rata: perpindahan bersih = jumlah langkah, jadi ER = 1."""
    closes = [100.0 + i for i in range(60)]
    er, _ = R.RegimeWatcher._hitung(buat_rates(closes))
    assert er == pytest.approx(1.0, abs=1e-9)


def test_bolak_balik_er_nol():
    """Naik-turun bergantian: perpindahan bersih 0, jadi ER = 0."""
    closes = [100.0 + (i % 2) for i in range(60)]
    er, _ = R.RegimeWatcher._hitung(buat_rates(closes))
    assert er == pytest.approx(0.0, abs=1e-9)


def test_bar_berjalan_dibuang():
    """Bar terakhir belum tutup. Menambahkan lonjakan di sana TIDAK boleh
    mengubah ER -- kalau berubah, watcher memakai data yang belum final."""
    closes = [100.0 + i for i in range(60)]
    er_a, _ = R.RegimeWatcher._hitung(buat_rates(closes))
    er_b, _ = R.RegimeWatcher._hitung(buat_rates(closes + [9999.0]))
    assert er_a == pytest.approx(er_b, abs=1e-9)


def test_denominator_nol_tidak_meledak():
    """Harga datar sempurna: pembagi 0. Harus 0.0, bukan ZeroDivisionError."""
    er, _ = R.RegimeWatcher._hitung(buat_rates([100.0] * 60))
    assert er == 0.0


# ------------------------------------------------------------------ histeresis
def test_label_pertama_langsung_dipakai(monkeypatch):
    pasang_mt5(monkeypatch, buat_rates([100.0 + i for i in range(60)]))
    w = R.RegimeWatcher()
    s = w.poll()
    assert s["regime"] == "TREN"
    assert s["changes"] == 0          # penetapan awal bukan pergantian


def test_butuh_dua_bar_untuk_berganti(monkeypatch):
    naik = [100.0 + i for i in range(60)]
    w = R.RegimeWatcher()
    pasang_mt5(monkeypatch, buat_rates(naik))
    assert w.poll()["regime"] == "TREN"

    # satu bar ranging saja: belum boleh berganti
    datar = naik + [159.0, 160.0]
    datar = datar[:-2] + [159.5, 159.4]
    pasang_mt5(monkeypatch, buat_rates([100.0 + (i % 2) for i in range(61)]))
    s = w.poll()
    assert s["regime"] == "TREN", "berganti terlalu cepat -- histeresis rusak"
    assert s["pending"] == "RANGING"
    assert s["pending_bars"] == 1

    # bar kedua ranging: baru berganti
    pasang_mt5(monkeypatch, buat_rates([100.0 + (i % 2) for i in range(62)]))
    s = w.poll()
    assert s["regime"] == "RANGING"
    assert s["changes"] == 1


def test_pending_direset_kalau_kembali(monkeypatch):
    naik = [100.0 + i for i in range(60)]
    w = R.RegimeWatcher()
    pasang_mt5(monkeypatch, buat_rates(naik))
    w.poll()

    pasang_mt5(monkeypatch, buat_rates([100.0 + (i % 2) for i in range(61)]))
    assert w.poll()["pending"] == "RANGING"

    # kembali TREN sebelum dikukuhkan -> pending harus bersih
    pasang_mt5(monkeypatch, buat_rates([100.0 + i for i in range(62)]))
    s = w.poll()
    assert s["regime"] == "TREN"
    assert s["pending"] is None
    assert s["changes"] == 0


def test_bar_sama_tidak_diolah_dua_kali(monkeypatch):
    rates = buat_rates([100.0 + i for i in range(60)])
    fake = pasang_mt5(monkeypatch, rates)
    w = R.RegimeWatcher()
    w.poll()
    n = fake.calls
    w.poll()
    assert fake.calls == n + 1, "tetap boleh menanyai MT5"
    assert w.changes == 0


def test_data_kurang_tidak_meledak(monkeypatch):
    pasang_mt5(monkeypatch, buat_rates([100.0, 101.0, 102.0]))
    w = R.RegimeWatcher()
    s = w.poll()
    assert s["regime"] is None


# ---------------------------------------------------------------- persistensi
def test_state_bertahan_antar_instance(monkeypatch):
    pasang_mt5(monkeypatch, buat_rates([100.0 + i for i in range(60)]))
    R.RegimeWatcher().poll()
    lain = R.RegimeWatcher()
    assert lain.regime == "TREN"
    assert lain.last_bar is not None


def test_history_ditulis(monkeypatch):
    pasang_mt5(monkeypatch, buat_rates([100.0 + i for i in range(60)]))
    R.RegimeWatcher().poll()
    baris = R.HISTORY.read_text(encoding="utf-8").strip().splitlines()
    assert len(baris) == 1
    d = json.loads(baris[0])
    assert d["regime"] == "TREN"
    assert "er" in d and "bar" in d


def test_state_memuat_harapan_per_basket(monkeypatch):
    pasang_mt5(monkeypatch, buat_rates([100.0 + i for i in range(60)]))
    s = R.RegimeWatcher().poll()
    assert s["harapan_per_basket"] == R.HARAPAN_PER_BASKET["TREN"]


# ------------------------------------------------------ jaminan tidak bertindak
def test_tidak_pernah_menyentuh_akun(monkeypatch):
    """Watcher hanya boleh MEMBACA. Kalau suatu saat ada yang menambahkan
    order_send / position_close ke sini, tes ini yang menangkapnya."""
    rates = buat_rates([100.0 + i for i in range(60)])

    class MT5Galak(FakeMT5):
        def order_send(self, *a, **k):      # pragma: no cover
            raise AssertionError("watcher mencoba mengirim order")

        def positions_get(self, *a, **k):   # pragma: no cover
            raise AssertionError("watcher menyentuh posisi")

    galak = MT5Galak(rates)
    monkeypatch.setattr("pipeline.live.book._mt5", lambda: galak)
    R.RegimeWatcher().poll()


def test_sumber_kode_tanpa_pemanggilan_berbahaya():
    """Penjagaan statis: berkas regime.py tidak boleh memuat perintah eksekusi."""
    import inspect
    src = inspect.getsource(R)
    for terlarang in ("order_send", "position_close", "PositionModify",
                      "OrderDelete", "TRADE_ACTION"):
        assert terlarang not in src, f"regime.py memuat {terlarang}"
