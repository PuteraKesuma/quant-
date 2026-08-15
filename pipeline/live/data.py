"""Live bar provider for signal evaluation.

Pulls recent M1 OHLCV straight from the MT5 terminal (already open to run the
EA), mirroring the normalisation in `pipeline/fetch/mt5_fetcher.py`: UTC index,
columns open/high/low/close/volume. The `orb` signal needs only today's bars up
to and including the opening range, so a few hundred bars is plenty.
"""
from pathlib import Path
import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config


class DataProvider:
    """Returns recent M1 bars for a config symbol from the live MT5 terminal."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self._initialized = False
        self._offset_hours: int | None = None   # cached broker server->UTC offset
        self._offset_calon: int | None = None   # calon offset baru, menunggu konfirmasi ke-2

    def _ensure_mt5(self):
        import MetaTrader5 as mt5
        if not self._initialized:
            if not mt5.initialize():
                raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
            self._initialized = True
        return mt5

    def recent_bars(self, symbol: str, n: int) -> pd.DataFrame:
        """DataFrame indexed by UTC ts with open/high/low/close/volume (newest last)."""
        mt5 = self._ensure_mt5()
        mt5_symbol = self.cfg["symbols"][symbol]["mt5_symbol"]

        # symbol_info() sesekali mengembalikan None walau simbolnya ada - terpantau 32x
        # pada 2026-08-14, membuat /signals balas 500 dan xau_executor tidak bisa
        # mengambil sinyal eterna sama sekali selama jendela itu. Penyebab paling umum:
        # simbol belum/keluar dari Market Watch, atau terminal sedang menyambung ulang.
        # Coba pilih simbolnya dulu sebelum menyerah - jangan langsung melempar.
        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            mt5.symbol_select(mt5_symbol, True)
            info = mt5.symbol_info(mt5_symbol)
            if info is None:
                raise ValueError(f"Symbol '{mt5_symbol}' not found in MT5.")
            logger.warning(f"[{symbol}] symbol_info None -> pulih setelah symbol_select")
        if not info.visible:
            mt5.symbol_select(mt5_symbol, True)

        rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M1, 0, n)
        if rates is None or len(rates) == 0:
            logger.warning(f"[{symbol}] No live bars from MT5: {mt5.last_error()}")
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rates)
        df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"})
        df = df.set_index("ts")[["open", "high", "low", "close", "volume"]].sort_index()

        # MT5 bar times are in BROKER SERVER time, not UTC. Shift to true UTC so the
        # UTC session windows (e.g. 13:30 NY) line up with the research. Without this
        # the live ORB evaluates a window offset by the broker's UTC offset.
        offset = self._server_offset_hours(mt5, mt5_symbol)
        if offset:
            df.index = df.index - pd.Timedelta(hours=offset)
        return df

    def _server_offset_hours(self, mt5, mt5_symbol) -> int:
        """Broker server time minus UTC, in whole hours (e.g. FBS = +3 summer).

        Uses the configured value if set; otherwise auto-detects from a fresh tick
        (server tick time vs real UTC, rounded to the nearest hour). Auto-detection
        is only trusted when the tick is fresh (a whole-hour offset within a sane
        range), so a stale weekend tick can't poison it. Warns if a configured
        offset disagrees with a freshly detected one (likely a DST change)."""
        configured = self.cfg.get("live", {}).get("mt5_server_utc_offset_hours")

        # ---------------------------------------------------------------------
        # CATATAN BUG (diperbaiki 2026-08-13) - jangan longgarkan ambang di bawah.
        #
        # Versi lama memakai `abs(diff - nearest) <= 0.5` sebagai penjaga "tick segar".
        # Syarat itu SELALU BENAR: round() ke bilangan bulat terdekat, menurut
        # definisinya, selalu menyisakan <= 0.5. Jadi penjaganya tidak menyaring
        # apa pun. Tick yang basi 40 menit memberi diff 2.33 -> nearest 2 -> sisa
        # 0.33 -> lolos sebagai "segar", dan offset jadi +2 padahal sebenarnya +3.
        #
        # Terpantau NYATA 4 kali (17 Jul 2x, 13 Agu 2x), selalu di jam sepi. Salah
        # 1 jam menggeser batas bar H1/H4 -> BOS dihitung di bar yang salah, expiry
        # order meleset sejam, dan jendela sesi ORB (13:30 UTC) ikut bergeser.
        #
        # Perbaikan: (1) sisa harus mendekati NOL, bukan <= 0.5; tick segar memberi
        # sisa dalam hitungan detik. (2) offset yang sudah mapan hanya boleh berubah
        # setelah dua deteksi berturut-turut sepakat - supaya satu tick aneh tidak
        # cukup menggesernya, tapi pergantian DST tetap tertangkap.
        # ---------------------------------------------------------------------
        TOLERANSI_JAM = 0.08          # ~5 menit; tick segar jauh di bawah ini

        # PENDEKATAN LAMA GAGAL SECARA PRINSIP (2026-08-15). Uji sisa pembulatan
        # tidak bisa membedakan tick SEGAR dari tick yang basi persis N jam bulat.
        # Terbukti di akhir pekan: tick terakhir Jumat sore basi ~1.92 jam -> sisa
        # 0.08 -> LOLOS -> offset terdeteksi -2h padahal broker +3h. Menambal ambang
        # tidak menyelesaikannya; masalahnya ada di sinyal yang dipakai.
        #
        # Yang dipakai sekarang: bandingkan tick dengan BAR M1 TERAKHIR. Bar hanya
        # terbentuk saat pasar buka, jadi selisih tick-vs-bar kecil = pasar hidup =
        # tick benar-benar segar. Kalau pasar tutup, deteksi DITOLAK seluruhnya dan
        # nilai tersimpan yang dipakai.
        detected = None
        tick = mt5.symbol_info_tick(mt5_symbol)
        bars = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M1, 0, 1)
        if tick and tick.time and bars is not None and len(bars):
            jarak_bar = abs(int(tick.time) - int(bars[-1]["time"])) / 60.0   # menit
            # Bar M1 terakhir HARUS dekat dengan waktu nyata, diukur memakai offset
            # yang DIPATOK di config. Ini yang membedakan pasar hidup dari feed mati:
            # di akhir pekan tick DAN bar sama-sama basi dari Jumat, jadi jaraknya
            # satu sama lain tetap kecil - membandingkan keduanya saja tidak cukup.
            umur_bar_menit = 1e9
            if configured is not None:
                bar_utc = pd.Timestamp(int(bars[-1]["time"]), unit="s", tz="UTC")                           - pd.Timedelta(hours=int(configured))
                umur_bar_menit = (pd.Timestamp.now("UTC") - bar_utc).total_seconds() / 60.0
            pasar_hidup = jarak_bar <= 5.0 and umur_bar_menit <= 10.0
            if pasar_hidup:
                server_now = pd.Timestamp(tick.time, unit="s", tz="UTC")
                diff = (server_now - pd.Timestamp.now("UTC")).total_seconds() / 3600.0
                nearest = round(diff)
                if abs(diff - nearest) <= TOLERANSI_JAM and -12 <= nearest <= 14:
                    detected = int(nearest)
            else:
                logger.debug(f"offset: pasar tampak TUTUP (bar M1 terakhir "
                             f"{umur_bar_menit:.0f} menit lalu) -> deteksi ditolak")

        if configured is not None:
            if detected is not None and detected != configured:
                logger.warning(
                    f"Configured mt5_server_utc_offset_hours={configured} but live "
                    f"offset looks like {detected} (DST change?). Update config.yaml."
                )
            offset = int(configured)
        elif detected is None:
            # Tick tidak dipercaya. JANGAN jatuh ke 0 - itu bug yang dibuat 2026-08-14:
            # pada proses yang BARU start (mis. dihidupkan watchdog di akhir pekan saat
            # semua tick basi), self._offset_hours masih None sehingga `or 0` memberi
            # offset 0 dan SELURUH bar bergeser 3 jam. Jendela sesi ORB, bar H1 eterna,
            # dan zona SMC semuanya jadi salah tanpa satu pun tanda.
            # Urutan cadangan: nilai di memori -> nilai terakhir yang tersimpan di disk.
            if self._offset_hours is not None:
                offset = self._offset_hours
            else:
                simpan = self._baca_offset_tersimpan()
                if simpan is not None:
                    logger.warning(f"tick basi & belum ada offset di memori -> pakai "
                                   f"nilai tersimpan {simpan:+d}h")
                    offset = simpan
                else:
                    logger.error("tick basi DAN tidak ada offset tersimpan -> memakai 0. "
                                 "SEMUA bar akan bergeser sampai tick segar tiba.")
                    offset = 0
        elif self._offset_hours is None:
            offset = detected                  # deteksi pertama: langsung dipakai
        elif detected == self._offset_hours:
            self._offset_calon = None          # stabil
            offset = detected
        else:
            # Berbeda dari yang mapan: butuh KONFIRMASI kedua sebelum digeser.
            if getattr(self, "_offset_calon", None) == detected:
                logger.warning(f"MT5 offset berubah {self._offset_hours:+d}h -> "
                               f"{detected:+d}h (dikonfirmasi 2x; pergantian DST?)")
                self._offset_calon = None
                offset = detected
            else:
                self._offset_calon = detected
                logger.info(f"offset terdeteksi {detected:+d}h berbeda dari "
                            f"{self._offset_hours:+d}h - menunggu konfirmasi kedua")
                offset = self._offset_hours

        if offset != self._offset_hours:
            logger.info(f"MT5 server->UTC offset = {offset:+d}h")
            self._offset_hours = offset
        # Simpan hanya offset yang datang dari tick SEGAR, supaya file tidak pernah
        # berisi nilai hasil tebakan.
        if detected is not None and detected == offset:
            self._tulis_offset(offset)
        return offset

    _OFFSET_FILE = Path(r"C:\Quant\_MONITOR\mt5_offset.txt")

    @classmethod
    def _baca_offset_tersimpan(cls):
        try:
            return int(cls._OFFSET_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    @classmethod
    def _tulis_offset(cls, off: int) -> None:
        try:
            cls._OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
            if cls._baca_offset_tersimpan() != off:
                cls._OFFSET_FILE.write_text(str(int(off)), encoding="utf-8")
        except Exception:
            pass
