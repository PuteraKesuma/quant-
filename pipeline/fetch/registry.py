"""Maps (symbol, source) -> fetcher instance, driven by config.yaml."""
from pathlib import Path

# Re-export the ONE loader instead of keeping a second copy here.
# This module used to define its own `load_config` with a bare
# `open(ROOT / "config.yaml")`. On Windows that decodes as cp1252 and dies on
# the first byte cp1252 cannot map (0x9d, from a UTF-8 curly quote in the
# Indonesian comments) -- so every `python -m pipeline.fetch.run_fetch` failed
# with UnicodeDecodeError before fetching a single bar. base_fetcher.load_config
# had already been fixed for exactly this on 2026-08-07; this copy was missed
# and silently kept the bug alive. Importing it removes the duplication so the
# two cannot drift apart again.
from .base_fetcher import load_config  # noqa: F401  (re-exported for callers)

ROOT = Path(__file__).parent.parent.parent


def build_fetcher(symbol: str, source: str, cfg: dict | None = None):
    cfg = cfg or load_config()
    if symbol not in cfg["symbols"]:
        raise ValueError(f"Unknown symbol '{symbol}'. Known: {list(cfg['symbols'])}")
    scfg = cfg["symbols"][symbol]

    if source == "mt5":
        from .mt5_fetcher import MT5Fetcher
        return MT5Fetcher(symbol, scfg["mt5_symbol"])
    if source == "dukascopy":
        from .dukascopy_fetcher import DukascopyFetcher
        return DukascopyFetcher(symbol, scfg["dukascopy_instrument"])

    raise ValueError(f"Unknown source '{source}'. Use 'mt5' or 'dukascopy'.")
