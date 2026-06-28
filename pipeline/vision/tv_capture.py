"""Headless TradingView multi-timeframe screenshots.

Renders the TradingView *advanced-chart* widget (anonymous, no login) for a
symbol like ``OANDA:XAUUSD`` at one or more timeframes and returns clean
candlestick PNGs — the real TradingView chart, not an mplfinance render.

Threading contract: a FRESH Chromium is launched and torn down inside EVERY
call, entirely within the calling thread. The signal server runs sync route
handlers in a threadpool (no running asyncio loop in those threads), so
``sync_playwright`` is valid there, and because nothing is cached across calls
Playwright's single-thread affinity is never violated. The trade-off is ~browser
launch cost per cycle, which is negligible at the vision slot's 30-min cadence.

``capture_multi_tv()`` RAISES on failure; the caller (ChartCapturer) wraps it and
the VisionStrategy degrades to its cached decision / FLAT — vision can never 500
the server.
"""
from loguru import logger

# TradingView interval codes (minutes, or D/W/M).
TF_TO_INTERVAL = {
    "M1": "1", "M3": "3", "M5": "5", "M15": "15", "M30": "30",
    "H1": "60", "H2": "120", "H4": "240", "D1": "D", "W1": "W", "MN1": "M",
}

# Advanced-chart widget in a bare page. autosize fills the viewport; side toolbar
# hidden for a cleaner image; symbol/timezone fixed; no studies (SMC reads price).
_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#131722">
<div class="tradingview-widget-container" style="width:100%;height:100vh">
  <div id="c" style="width:100%;height:100%"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
  new TradingView.widget({{
    "container_id":"c","symbol":"{symbol}","interval":"{interval}",
    "autosize":true,"theme":"{theme}","style":"1","timezone":"Etc/UTC",
    "hide_side_toolbar":true,"allow_symbol_change":false,
    "withdateranges":false,"save_image":false,"studies":[]
  }});
  </script>
</div></body></html>"""


def _interval(tf: str) -> str:
    iv = TF_TO_INTERVAL.get(tf.upper())
    if iv is None:
        raise ValueError(f"unsupported timeframe for TradingView: {tf!r}")
    return iv


def capture_multi_tv(tv_symbol: str, timeframes: list[str], *,
                     width: int = 1280, height: int = 800, theme: str = "dark",
                     settle_ms: int = 3500, timeout_ms: int = 25000
                     ) -> list[tuple[str, bytes]]:
    """Return [(tf_label, png_bytes), ...] for each timeframe (order preserved).

    One Chromium for the whole call, one page per timeframe. Raises on failure."""
    from playwright.sync_api import sync_playwright

    out: list[tuple[str, bytes]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        try:
            ctx = browser.new_context(viewport={"width": width, "height": height})
            # Open every timeframe page FIRST so they bootstrap the widget + load chart
            # data concurrently in the browser; then wait + screenshot. Total time is
            # ~the slowest single chart, not the sum (≈3x faster than serial).
            pages: list[tuple[str, object]] = []
            for tf in timeframes:
                iv = _interval(tf)
                page = ctx.new_page()
                page.set_content(_HTML.format(symbol=tv_symbol, interval=iv, theme=theme),
                                 wait_until="domcontentloaded")
                pages.append((tf, page))
            try:
                for tf, page in pages:                      # all loading in parallel now
                    page.wait_for_selector("iframe", timeout=timeout_ms)
                    page.frame_locator("iframe").locator("canvas").first.wait_for(timeout=timeout_ms)
                pages[0][1].wait_for_timeout(settle_ms)     # one settle for all to paint
                for tf, page in pages:
                    out.append((tf, page.screenshot(type="png")))
            finally:
                for _, page in pages:
                    page.close()
        finally:
            browser.close()
    if not out:
        raise RuntimeError("TradingView capture produced no images")
    logger.info(f"[tv_capture] {tv_symbol}: {len(out)} frame(s) {[t for t, _ in out]}")
    return out
