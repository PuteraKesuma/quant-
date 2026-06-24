"""Chart capture: produce a PNG of the current chart for a symbol.

Two modes, selected by config `capture_mode`:
  - `mt5` (default): pull recent bars from the open MT5 terminal (same
    normalisation as `pipeline/live/data.py`) and render a candlestick chart
    with mplfinance to in-memory PNG bytes, with a few swing-based S/R lines.
  - `tradingview` (optional): open `chart_url` in headless Chromium (Playwright,
    lazy-imported) and screenshot the chart. The browser context is created once
    and reused across calls.

`capture()` RAISES on failure; the VisionStrategy wraps it and degrades to the
cached decision or FLAT.
"""
import io

import matplotlib
matplotlib.use("Agg")          # headless/server backend — render charts off the
                               # main thread (uvicorn worker) without a GUI backend
from loguru import logger


class ChartCapturer:
    """Renders or screenshots a chart for one vision slot."""

    def __init__(self, spec: dict, cfg: dict):
        self.spec = spec
        self.cfg = cfg
        self.symbol = spec["symbol"]
        p = spec.get("params", {})
        self.mode = p.get("capture_mode", "mt5")
        self.chart_bars = int(p.get("chart_bars", 200))
        self.chart_timeframe = str(p.get("chart_timeframe", "M5"))
        self.chart_url = p.get("chart_url", "") or ""
        # Optional multi-timeframe list (highest->lowest), e.g. ["H1","M15","M5"].
        # When set, capture_multi() renders one image per timeframe (mt5 mode only).
        tfs = p.get("timeframes")
        if isinstance(tfs, str):
            tfs = [t.strip() for t in tfs.split(",") if t.strip()]
        self.timeframes = list(tfs) if tfs else []
        self._mt5_init = False
        self._pw = None
        self._browser = None
        self._context = None

    def capture(self, symbol: str) -> bytes:
        """Return PNG bytes of the current chart for `symbol`. Raises on failure."""
        if self.mode == "mt5":
            return self._capture_mt5(symbol)
        if self.mode == "tradingview":
            return self._capture_tv(symbol)
        raise ValueError(f"unknown capture_mode: {self.mode!r}")

    def capture_multi(self, symbol: str) -> list[tuple[str, bytes]]:
        """Return [(timeframe_label, PNG bytes), ...] for each configured timeframe,
        highest->lowest. mt5 mode only; falls back to a single image otherwise.
        Raises on failure (the VisionStrategy wraps and degrades to cached/FLAT)."""
        if not self.timeframes:
            return [(self.chart_timeframe, self.capture(symbol))]
        if self.mode != "mt5":          # multi-TF rendering needs the mt5 renderer
            logger.warning(f"timeframes set but capture_mode={self.mode!r}; using single image")
            return [(self.chart_timeframe, self.capture(symbol))]
        return [(tf, self._capture_mt5(symbol, timeframe=tf)) for tf in self.timeframes]

    # ----------------------------------------------------------------- mt5 mode
    def _capture_mt5(self, symbol: str, timeframe: str | None = None) -> bytes:
        import MetaTrader5 as mt5
        import pandas as pd

        if not self._mt5_init:
            if not mt5.initialize():
                raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
            self._mt5_init = True

        tf_label = timeframe or self.chart_timeframe
        mt5_symbol = self.cfg["symbols"][symbol]["mt5_symbol"]
        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            raise ValueError(f"Symbol '{mt5_symbol}' not found in MT5.")
        if not info.visible:
            mt5.symbol_select(mt5_symbol, True)

        tf = self._tf_const(mt5, tf_label)
        rates = mt5.copy_rates_from_pos(mt5_symbol, tf, 0, self.chart_bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No {tf_label} bars for {mt5_symbol}: {mt5.last_error()}")

        df = pd.DataFrame(rates)
        df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"})
        df = df.set_index("ts")[["open", "high", "low", "close", "volume"]].sort_index()
        return self._render(df, symbol, tf_label)

    @staticmethod
    def _tf_const(mt5, tf: str):
        table = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        if tf not in table:
            raise ValueError(f"unsupported chart_timeframe: {tf!r}")
        return table[tf]

    def _render(self, df, symbol: str, tf_label: str | None = None) -> bytes:
        """Candlestick PNG (~1280x720) with swing S/R lines, in memory."""
        import mplfinance as mpf

        ohlc = df.rename(columns=str.capitalize)  # Open/High/Low/Close/Volume for mplfinance
        buf = io.BytesIO()
        kwargs = dict(
            type="candle", volume=True, style="charles",
            title=f"{symbol} {tf_label or self.chart_timeframe}",
            figsize=(12.8, 7.2),
            savefig=dict(fname=buf, dpi=100, format="png"),
        )
        levels = self._sr_levels(ohlc)
        if levels:
            kwargs["hlines"] = dict(hlines=levels, linewidths=0.6,
                                    colors="gray", linestyle="--")
        mpf.plot(ohlc, **kwargs)
        buf.seek(0)
        return buf.getvalue()

    @staticmethod
    def _sr_levels(ohlc, k: int = 5, max_lines: int = 6) -> list[float]:
        """Simple N-bar swing highs/lows as S/R; de-clustered, capped to `max_lines`."""
        try:
            highs, lows = ohlc["High"], ohlc["Low"]
            n = len(ohlc)
            raw: list[float] = []
            for i in range(k, n - k):
                if highs.iloc[i] == highs.iloc[i - k:i + k + 1].max():
                    raw.append(float(highs.iloc[i]))
                if lows.iloc[i] == lows.iloc[i - k:i + k + 1].min():
                    raw.append(float(lows.iloc[i]))
            if not raw:
                return []
            span = float(highs.max() - lows.min()) or 1.0
            min_gap = span * 0.01                      # drop near-duplicate levels
            kept: list[float] = []
            for lvl in sorted(raw):
                if not kept or abs(lvl - kept[-1]) >= min_gap:
                    kept.append(lvl)
            if len(kept) > max_lines:                  # keep the most extreme spread
                idx = [round(i * (len(kept) - 1) / (max_lines - 1)) for i in range(max_lines)]
                kept = [kept[i] for i in sorted(set(idx))]
            return kept
        except Exception as e:                         # S/R is best-effort
            logger.warning(f"S/R level computation skipped: {e}")
            return []

    # --------------------------------------------------------- tradingview mode
    def _capture_tv(self, symbol: str) -> bytes:
        if not self.chart_url:
            raise ValueError("capture_mode=tradingview requires params.chart_url")
        ctx = self._tv_context()
        page = ctx.new_page()
        try:
            page.goto(self.chart_url, wait_until="networkidle")
            page.wait_for_selector("canvas", timeout=15000)  # chart canvas
            return page.screenshot(type="png")
        finally:
            page.close()

    def _tv_context(self):
        """Create the headless Chromium context once and reuse it across calls."""
        if self._context is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as e:
                raise RuntimeError(
                    "playwright not installed for capture_mode=tradingview. "
                    "Run: pip install playwright && playwright install chromium"
                ) from e
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 720})
        return self._context
