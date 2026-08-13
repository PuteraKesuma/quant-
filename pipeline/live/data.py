"""Live bar provider for signal evaluation.

Pulls recent M1 OHLCV straight from the MT5 terminal (already open to run the
EA), mirroring the normalisation in `pipeline/fetch/mt5_fetcher.py`: UTC index,
columns open/high/low/close/volume. The `orb` signal needs only today's bars up
to and including the opening range, so a few hundred bars is plenty.
"""
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

        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            raise ValueError(f"Symbol '{mt5_symbol}' not found in MT5.")
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

        detected = None
        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick and tick.time:
            server_now = pd.Timestamp(tick.time, unit="s", tz="UTC")
            diff = (server_now - pd.Timestamp.now("UTC")).total_seconds() / 3600.0
            nearest = round(diff)
            if abs(diff - nearest) <= TOLERANSI_JAM and -12 <= nearest <= 14:
                detected = int(nearest)
            else:
                logger.debug(f"offset diabaikan: tick basi (diff {diff:+.3f}h, "
                             f"sisa {abs(diff - nearest):.3f}h > {TOLERANSI_JAM})")

        if configured is not None:
            if detected is not None and detected != configured:
                logger.warning(
                    f"Configured mt5_server_utc_offset_hours={configured} but live "
                    f"offset looks like {detected} (DST change?). Update config.yaml."
                )
            offset = int(configured)
        elif detected is None:
            offset = self._offset_hours or 0   # tick tidak dipercaya -> pertahankan
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
        return offset
