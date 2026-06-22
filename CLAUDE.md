# ORB Research — Claude Agent Context

## Project
Opening Range Breakout (ORB) quantitative research across XAUUSD, NAS100, GBPUSD.
Pipeline: Raw data → Features → Backtest → Forward Test → Walk-Forward Test.

## Stack
- Python 3.11+
- DuckDB for raw OHLCV storage
- Parquet (via PyArrow/Polars) for feature layers
- config.yaml as single source of truth for all parameters

## Data Sources
- **Dukascopy** (primary, `data.source: dukascopy`) — deep free history via `dukascopy-python`, HTTP only.
- **MT5** (recent/live) — requires MT5 terminal open + logged in. Note: on FBS, `copy_rates_range` is broken; we use `copy_rates_from_pos`.
- **Databento** (CME futures, offline file import) — `pipeline/fetch/import_databento.py` reads a `.FUT` parent DBN (`ohlcv-1m`), keeps outright contracts, and builds a **continuous front-month** series (max-volume contract per UTC day) into Level_0 with a `contract` column. Used for MGC (micro gold futures).
- Symbol → instrument mapping lives in `config.yaml` per symbol (`mt5_symbol`, `dukascopy_instrument`).
- `pipeline/fetch/registry.py` builds the right fetcher from (symbol, source).

## Folder Map
```
data/Level_0_Raw/       → DuckDB files per symbol (1m OHLCV)
data/Level_1_Features/  → Parquet feature files per symbol/session
data/Level_2_Datamart/  → Labeled datasets split by test type
model/                  → Trained model artifacts per symbol/strategy
pipeline/fetch/         → Data ingestion scripts
pipeline/features/      → Feature engineering modules
pipeline/backtest/      → Backtesting engine and ORB strategy
pipeline/forward_test/  → Forward test runner
pipeline/walk_forward/  → Walk-forward optimization
pipeline/analysis/      → Metrics, reports, charts
pipeline/audit/         → Data integrity + continuity worker (detect & auto-repair)
pipeline/live/          → Execution layer: FastAPI signal server (brain)
mt5_ea/                 → MQL5 Expert Advisor (hands) — deploy to MT5/Experts/
_DOC/audit/             → Per-symbol audit reports (markdown)
_MEMORY/                → Timestamped continuity checkpoints
```

## Conventions
- All time in UTC
- Symbol names: XAUUSD, NAS100, GBPUSD (uppercase, no slash)
- Session names: london, new_york, frankfurt (lowercase)
- All configs read from config.yaml — no hardcoded parameters in scripts
- Logging via loguru, not print()
- Test files in tests/ mirroring pipeline/ structure

## Key Entry Points
- `pipeline/fetch/run_fetch.py --source dukascopy|mt5` — fetch raw data
- `pipeline/features/build_features.py` — build feature parquets
- `pipeline/backtest/runner.py` — run backtest
- `pipeline/walk_forward/wfo_runner.py` — run walk-forward
- `pipeline/analysis/report_gen.py` — generate reports
- `pipeline/audit/run_audit.py --once|--watch` — data audit worker (integrity + continuity + auto-repair)
- `pipeline/live/run_server.py` — start the FastAPI signal server (execution layer)

## Execution Layer (live)
- Split brain/hands: **`pipeline/live/`** FastAPI server decides, **`mt5_ea/SignalExecutor.mq5`** executes. The EA is **strategy-agnostic** — it knows nothing about ORB or any model, it just executes the signals the server returns.
- The EA polls `GET /signals?symbol=NAS100` every second via `WebRequest`; server returns a **list** `{symbol, ts, signals:[{strategy, action, sl, tp, lot, magic, signal_id, ts}...]}`.
- **Multi-model:** each strategy "slot" in `config.yaml live.strategies` runs concurrently and holds its own position, tagged by its own unique `magic`. Single-model = a list of one.
- **Idempotency:** `action` is a *desired state* (BUY/SELL/FLAT) and `signal_id` is stable per signal; the EA tracks the last `signal_id` per `magic` and acts only when it changes, so 1s polling never duplicates orders. The broker's SL/TP closes the trade.
- Strategy types (registry in `pipeline/live/signal.py::STRATEGY_TYPES`, selected by config `type:`): `dummy` (1-min FLAT→BUY→FLAT→SELL test cycle) and `orb` (reuses `pipeline/backtest/strategy_orb.py`). Add a new model = add a class + a config slot; the EA never changes.
- Live bars come from MT5 (terminal already open for the EA). MT5 requires the server URL whitelisted: Tools → Options → Expert Advisors → WebRequest allowed URLs → `http://127.0.0.1:8000`.

## Data Audit
- `python -m pipeline.audit.run_audit --once` runs one pass; no flag / `--watch` runs continuously (interval in `config.yaml audit.interval_minutes`).
- Continuity auto-detects each symbol's daily-break window; classifies gaps as weekend / daily-break / holiday / medium-break (all expected) vs `ANOMALY_INTRADAY`.
- Repair is conservative: only forward-fills gaps ≤ `audit.max_fill_gap_minutes`, marks them `is_synthetic=TRUE` in the `ohlcv` table, logs to `audit_log`. Re-fetch is off by default (`audit.refetch_before_fill`).
- Backtests can exclude fabricated bars with `WHERE NOT is_synthetic`.
