"""Repair: re-fetch real bars for larger gaps, forward-fill tiny gaps as
synthetic, and log every action. Writes are guarded and provenance-tracked
via an `is_synthetic` column + an `audit_log` table.
"""
import pandas as pd
from loguru import logger

from . import raw_db_path, read_ohlcv, connect_utc
from .continuity import classify_gaps
from pipeline.fetch.registry import build_fetcher


def _ensure_schema(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info('ohlcv')").fetchall()]
    if "is_synthetic" not in cols:
        con.execute("ALTER TABLE ohlcv ADD COLUMN is_synthetic BOOLEAN DEFAULT FALSE")
    con.execute(
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "run_ts TIMESTAMPTZ, symbol VARCHAR, action VARCHAR, "
        "gap_start TIMESTAMPTZ, gap_end TIMESTAMPTZ, n_affected INTEGER, detail VARCHAR)"
    )


def _log(symbol, cfg, action, n, detail):
    con = connect_utc(raw_db_path(symbol, cfg), read_only=False)
    _ensure_schema(con)
    con.execute("INSERT INTO audit_log VALUES (now(), ?, ?, NULL, NULL, ?, ?)",
                [symbol, action, int(n), detail])
    con.close()


def _forward_fill(symbol, cfg, small_gaps) -> int:
    """Synthesize bars for tiny gaps: O=H=L=C=prev_close, volume=0, is_synthetic=True."""
    if not small_gaps:
        return 0
    df = read_ohlcv(symbol, cfg).set_index("ts")
    rows = []
    for g in small_gaps:
        start, end = pd.Timestamp(g["start"]), pd.Timestamp(g["end"])
        if start not in df.index:
            continue
        pc = float(df.loc[start, "close"])
        rng = pd.date_range(start + pd.Timedelta(minutes=1),
                            end - pd.Timedelta(minutes=1), freq="1min", tz="UTC")
        for t in rng:
            rows.append({"ts": t, "open": pc, "high": pc, "low": pc,
                         "close": pc, "volume": 0.0, "is_synthetic": True})
    if not rows:
        return 0
    sdf = pd.DataFrame(rows)
    con = connect_utc(raw_db_path(symbol, cfg), read_only=False)
    _ensure_schema(con)
    con.execute("INSERT OR REPLACE INTO ohlcv (ts,open,high,low,close,volume,is_synthetic) "
                "SELECT ts,open,high,low,close,volume,is_synthetic FROM sdf")
    con.close()
    return len(sdf)


def repair_symbol(symbol: str, cfg: dict, source: str | None = None) -> dict:
    acfg = cfg.get("audit", {})
    if not acfg.get("enable_repair", False):
        return {"enabled": False}

    source = source or cfg["data"]["source"]
    max_fill = acfg.get("max_fill_gap_minutes", 5)

    # make sure schema (is_synthetic + audit_log) exists before any write
    con = connect_utc(raw_db_path(symbol, cfg), read_only=False)
    _ensure_schema(con)
    con.close()

    anomalies = [g for g in classify_gaps(symbol, cfg)["gaps"]
                 if g["class"] == "ANOMALY_INTRADAY"]
    if not anomalies:
        return {"enabled": True, "refetched": 0, "filled": 0,
                "anomalies_before": 0, "anomalies_after": 0}

    # medium anomalies (a few min..1h) may be transient source misses — optional,
    # bounded re-fetch. Tiny gaps are source-side (illiquid) so re-fetch won't help.
    medium = [g for g in anomalies if max_fill + 1 < g["minutes"] <= 60]

    refetched = 0
    if acfg.get("refetch_before_fill", False) and medium:
        cap = acfg.get("max_refetch_days", 60)
        fetcher = build_fetcher(symbol, source, cfg)
        days = sorted({pd.Timestamp(g["start"]).date() for g in medium})[:cap]
        for day in days:
            try:
                d0 = pd.Timestamp(day)
                df = fetcher.fetch(str(d0.date()), str((d0 + pd.Timedelta(days=1)).date()))
                if df is not None and not df.empty:
                    fetcher.upsert(df)
                    refetched += len(df)
            except Exception as e:
                logger.warning(f"[{symbol}] re-fetch {day} failed: {e}")

    # 2) forward-fill residual tiny gaps (synthetic, marked)
    small_after = [g for g in classify_gaps(symbol, cfg)["gaps"]
                   if g["class"] == "ANOMALY_INTRADAY" and g["minutes"] <= max_fill + 1]
    filled = _forward_fill(symbol, cfg, small_after)

    anomalies_after = sum(1 for g in classify_gaps(symbol, cfg)["gaps"]
                          if g["class"] == "ANOMALY_INTRADAY")

    _log(symbol, cfg, "repair", refetched + filled,
         f"refetched={refetched} filled={filled} anomalies {len(anomalies)}->{anomalies_after}")
    return {"enabled": True, "refetched": refetched, "filled": filled,
            "anomalies_before": len(anomalies), "anomalies_after": anomalies_after}
