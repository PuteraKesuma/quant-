"""Integrity checks on raw OHLCV. Read-only; returns structured findings."""
from . import read_ohlcv


def check_integrity(symbol: str, cfg: dict) -> dict:
    df = read_ohlcv(symbol, cfg)
    findings = []

    def add(check, severity, count, detail, sample=None):
        if count:
            findings.append({
                "check": check, "severity": severity,
                "count": int(count), "detail": detail,
                "sample": sample or [],
            })

    ohlc = ["open", "high", "low", "close"]

    # nulls
    add("null_ohlc", "error", df[ohlc].isna().any(axis=1).sum(), "rows with null OHLC")

    # duplicate / non-monotonic timestamps
    add("duplicate_ts", "error", df["ts"].duplicated().sum(), "duplicate timestamps")
    add("non_monotonic_ts", "error",
        (df["ts"].diff().dt.total_seconds() < 0).sum(), "timestamps not increasing")

    # OHLC consistency: high must be the max, low must be the min
    bad = df[(df["high"] < df[["open", "close", "low"]].max(axis=1)) |
             (df["low"]  > df[["open", "close", "high"]].min(axis=1))]
    add("ohlc_violation", "error", len(bad),
        "high<max(o,c,l) or low>min(o,c,h)", bad["ts"].head(5).astype(str).tolist())

    # non-positive prices
    nonpos = df[(df[ohlc] <= 0).any(axis=1)]
    add("nonpositive_price", "error", len(nonpos),
        "price <= 0", nonpos["ts"].head(5).astype(str).tolist())

    # negative volume
    add("negative_volume", "error", (df["volume"] < 0).sum(), "volume < 0")

    # flatline (O=H=L=C) — warning only
    flat = df[(df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])]
    add("flatline", "warning", len(flat),
        "O=H=L=C (low liquidity?)", flat["ts"].head(5).astype(str).tolist())

    # spikes — warning only
    thr = cfg["audit"]["spike_return_threshold"]
    spikes = df[df["close"].pct_change().abs() > thr]
    add("spike", "warning", len(spikes),
        f"|1m return| > {thr}", spikes["ts"].head(5).astype(str).tolist())

    return {
        "rows": len(df),
        "range": [str(df["ts"].min()), str(df["ts"].max())],
        "findings": findings,
    }
