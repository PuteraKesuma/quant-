"""Maps (symbol, source) -> fetcher instance, driven by config.yaml."""
from pathlib import Path
import yaml

ROOT = Path(__file__).parent.parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


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
