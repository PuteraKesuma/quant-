"""BasketGuardian: penjaga terakhir untuk basket Semi Marti.

Kelas ini BOLEH menutup posisi di akun sungguhan, jadi setiap syarat yang
menahannya harus punya test. Yang diuji bukan cuma "apakah dia menutup saat
rugi dalam", tapi terutama SEMUA keadaan di mana dia HARUS DIAM -- karena
penjaga yang terlalu cepat bertindak lebih berbahaya daripada tidak ada penjaga.

Satu test menirukan kebiasaan broker ini: mengembalikan retcode 0 pada setiap
order padahal eksekusinya berhasil. Bug itu melumpuhkan Dual Entry EA selama
berminggu-minggu tanpa ketahuan, jadi penutupan di sini diverifikasi dengan
MEMBACA ULANG buku posisi, bukan dengan mempercayai kode balasan.
"""
from __future__ import annotations

import pytest

from pipeline.live import book


# --------------------------------------------------------------- MT5 palsu
class FakePos:
    def __init__(self, ticket, profit, swap=0.0, magic=book.SEMI_MARTI_MAGIC,
                 symbol="XAUUSD", ptype=0, volume=0.01):
        self.ticket = ticket
        self.profit = profit
        self.swap = swap
        self.magic = magic
        self.symbol = symbol
        self.type = ptype
        self.volume = volume


class FakeTick:
    bid = 4000.0
    ask = 4000.5


class FakeResult:
    def __init__(self, retcode, comment=""):
        self.retcode = retcode
        self.comment = comment


class FakeMT5:
    """Cukup meniru bagian API MT5 yang dipakai guardian."""

    POSITION_TYPE_BUY = 0
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1

    def __init__(self, positions, close_works=True, retcode=0):
        self._pos = list(positions)
        self.close_works = close_works
        self.retcode = retcode
        self.sent: list[dict] = []

    def positions_get(self, symbol=None):
        return list(self._pos)

    def symbol_info_tick(self, symbol):
        return FakeTick()

    def order_send(self, req):
        self.sent.append(req)
        if self.close_works:
            self._pos = [p for p in self._pos if p.ticket != req["position"]]
        return FakeResult(self.retcode, "Done")


@pytest.fixture
def patch_mt5(monkeypatch):
    def _apply(fake):
        monkeypatch.setattr(book, "_mt5", lambda: fake)
        return fake
    return _apply


def guardian(tmp_path, **kw):
    kw.setdefault("journal", tmp_path / "j.jsonl")
    return book.BasketGuardian(**kw)


# ------------------------------------------------------------------- diam
def test_diam_saat_tidak_ada_posisi(tmp_path, patch_mt5):
    m = patch_mt5(FakeMT5([]))
    g = guardian(tmp_path)
    for _ in range(10):
        g.poll()
    assert m.sent == []
    assert g.fired_count == 0


def test_diam_saat_rugi_masih_di_atas_ambang(tmp_path, patch_mt5):
    """-$100 belum melewati -$110. EA masih punya kesempatan bertindak."""
    m = patch_mt5(FakeMT5([FakePos(1, -50.0), FakePos(2, -50.0)]))
    g = guardian(tmp_path)
    for _ in range(10):
        g.poll()
    assert m.sent == []
    assert g.breaches == 0


def test_diam_saat_untung(tmp_path, patch_mt5):
    m = patch_mt5(FakeMT5([FakePos(1, 20.0), FakePos(2, 20.0)]))
    g = guardian(tmp_path)
    g.poll()
    assert m.sent == []


def test_satu_tick_buruk_tidak_cukup(tmp_path, patch_mt5):
    """Satu pelanggaran tunggal tidak boleh memicu -- bisa jadi harga sesat."""
    m = patch_mt5(FakeMT5([FakePos(1, -200.0)]))
    g = guardian(tmp_path, confirm_polls=3)
    g.poll()
    assert m.sent == []
    assert g.breaches == 1


def test_hitungan_direset_kalau_pulih(tmp_path, patch_mt5):
    fake = FakeMT5([FakePos(1, -200.0)])
    patch_mt5(fake)
    g = guardian(tmp_path, confirm_polls=3)
    g.poll()
    g.poll()
    assert g.breaches == 2
    fake._pos = [FakePos(1, -10.0)]          # harga berbalik
    g.poll()
    assert g.breaches == 0
    assert fake.sent == []


# --------------------------------------------------------------- bertindak
def test_menutup_setelah_pelanggaran_berturut(tmp_path, patch_mt5):
    m = patch_mt5(FakeMT5([FakePos(1, -60.0), FakePos(2, -60.0)]))
    g = guardian(tmp_path, confirm_polls=3)
    g.poll(); g.poll()
    assert m.sent == []                      # belum, baru 2 kali
    g.poll()
    assert len(m.sent) == 2                  # kedua kaki ditutup
    assert g.fired_count == 1
    assert m.positions_get() == []


def test_retcode_nol_tetap_dianggap_sukses_kalau_posisi_hilang(tmp_path, patch_mt5):
    """Broker ini SELALU balas retcode 0. Yang menentukan adalah buku posisi."""
    m = patch_mt5(FakeMT5([FakePos(1, -120.0)], close_works=True, retcode=0))
    g = guardian(tmp_path, confirm_polls=1)
    g.poll()
    rec = [l for l in (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rec) == 1
    assert '"failed": []' in rec[0]
    assert '"closed": [1]' in rec[0]


def test_gagal_menutup_dilaporkan_bukan_didiamkan(tmp_path, patch_mt5):
    """retcode 10009 'sukses' tapi posisi MASIH ADA -> harus dicatat gagal."""
    m = patch_mt5(FakeMT5([FakePos(1, -120.0)], close_works=False, retcode=10009))
    g = guardian(tmp_path, confirm_polls=1)
    g.poll()
    body = (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    assert '"closed": []' in body
    assert '"failed": [1]' in body


def test_cooldown_mencegah_menembak_berulang(tmp_path, patch_mt5):
    fake = FakeMT5([FakePos(1, -120.0)], close_works=False, retcode=0)
    patch_mt5(fake)
    g = guardian(tmp_path, confirm_polls=1, cooldown_s=999)
    g.poll()
    assert g.fired_count == 1
    n = len(fake.sent)
    for _ in range(5):
        g.poll()
    assert g.fired_count == 1                # tidak menembak lagi
    assert len(fake.sent) == n


def test_swap_ikut_dihitung(tmp_path, patch_mt5):
    """EA mengukur basketnya dengan profit + swap; guardian harus sama."""
    m = patch_mt5(FakeMT5([FakePos(1, -105.0, swap=-10.0)]))
    g = guardian(tmp_path, confirm_polls=1)
    g.poll()
    assert g.fired_count == 1                # -115 <= -110


def test_magic_lain_diabaikan(tmp_path, patch_mt5):
    """Posisi eterna tidak boleh ikut dihitung, apalagi ikut ditutup."""
    m = patch_mt5(FakeMT5([FakePos(9, -300.0, magic=920627)]))
    g = guardian(tmp_path, confirm_polls=1)
    for _ in range(5):
        g.poll()
    assert m.sent == []
    assert g.fired_count == 0


def test_state_bisa_dibaca_untuk_health(tmp_path, patch_mt5):
    patch_mt5(FakeMT5([FakePos(1, -20.0)]))
    g = guardian(tmp_path)
    g.poll()
    s = g.state()
    assert s["armed"] is True
    assert s["hard_stop_usd"] == -110.0
    assert s["ea_stop_usd"] == -75.0
    assert s["legs"] == 1
    assert s["fired_count"] == 0
